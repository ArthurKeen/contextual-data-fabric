"""Query-graph partition planner (M5 / E1).

Given a conceptual SPARQL query and a :class:`~cdf.query.catalog.SourceCatalog`,
partition the query's basic graph pattern by the source each concept/property
maps to, and emit per-source sub-queries plus the cross-source join keys.

Routing algorithm (OBDA-style, two passes):

1. **Class binding.** Every ``?x a :Class`` triple binds variable ``?x`` to the
   source that owns ``:Class``.
2. **Route.** Each triple goes to:
   - a ``rdf:type`` triple → the class's source;
   - any other triple whose subject variable is class-bound → that source (the
     subject's class wins, which resolves properties whose plain name is shared
     across sources);
   - otherwise, if the predicate maps to exactly one source → that source;
   - else it is left **unresolved** (surfaced, never dropped).

A variable that ends up in the buckets of two *different* sources is a
cross-source **join key** — the point where the executor reconciles results via
the canonical entity hub (M6). This falls out of the routing naturally: an
object-property triple stays with its subject's source, while the object
variable is independently class-bound to the other source, so it appears on both
sides.
"""

from __future__ import annotations

from typing import Any

from rdflib import RDF, URIRef
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import Variable

from .catalog import SourceCatalog
from .types import PartitionPlan, SourceRef, SubQuery, TriplePattern

_TermTriple = tuple[Any, Any, Any]


class UnsupportedQueryError(ValueError):
    """Raised when a query uses a construct E1 cannot faithfully partition.

    E1 partitions **basic graph patterns** (conjunctive triple patterns). A
    construct like ``FILTER``, ``OPTIONAL``, ``UNION``, ``MINUS``, ``BIND`` or a
    named ``GRAPH`` block changes the pattern's meaning in ways a naive
    per-source split would silently get wrong (e.g. a dropped ``FILTER`` broadens
    the answer). Rather than return an incorrect plan, the planner refuses —
    honouring the fabric's "never silent omission" principle. These land in a
    later E1 iteration.
    """


# Algebra node names that carry graph-pattern semantics a BGP-only partitioner
# would drop or mangle. Result modifiers (Project, OrderBy, Slice, Distinct,
# Reduced, ToMultiSet, SelectQuery) are safe — they apply after the per-source
# results are joined, so they don't affect partitioning.
_UNSUPPORTED_NODES = {
    "Filter": "FILTER",
    "LeftJoin": "OPTIONAL",
    "Union": "UNION",
    "Minus": "MINUS",
    "Extend": "BIND / expression assignment",
    "Graph": "named GRAPH block",
    "Group": "GROUP BY / aggregation",
    "AggregateJoin": "aggregation",
}


def _collect_bgp_triples(node: Any, out: list[_TermTriple]) -> None:
    """Walk a SPARQL algebra tree and gather every BGP triple (s, p, o)."""
    if isinstance(node, CompValue):
        if node.name == "BGP":
            out.extend(node["triples"])
        for value in node.values():
            _collect_bgp_triples(value, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect_bgp_triples(item, out)


def _check_supported(node: Any, found: set[str]) -> None:
    """Collect any unsupported graph-pattern node names in the algebra."""
    if isinstance(node, CompValue):
        if node.name in _UNSUPPORTED_NODES:
            found.add(node.name)
        for value in node.values():
            _check_supported(value, found)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _check_supported(item, found)


def _triple_vars(triple: _TermTriple) -> list[Variable]:
    return [t for t in triple if isinstance(t, Variable)]


def _route(
    triple: _TermTriple,
    catalog: SourceCatalog,
    var_source: dict[Variable, SourceRef],
) -> SourceRef | None:
    subject, predicate, obj = triple
    if predicate == RDF.type and isinstance(obj, URIRef):
        return catalog.source_of_class(str(obj))
    if isinstance(subject, Variable) and subject in var_source:
        return var_source[subject]
    if isinstance(predicate, URIRef):
        sources = catalog.sources_of_property(str(predicate))
        if len(sources) == 1:
            return next(iter(sources))
    return None


def _public(triple: _TermTriple) -> TriplePattern:
    s, p, o = triple
    return TriplePattern(subject=s.n3(), predicate=p.n3(), object=o.n3())


def _serialize(select_vars: list[str], triples: list[_TermTriple]) -> str:
    body = "\n".join(f"  {s.n3()} {p.n3()} {o.n3()} ." for s, p, o in triples)
    projection = " ".join(select_vars) if select_vars else "*"
    return f"SELECT {projection} WHERE {{\n{body}\n}}"


def partition_query(sparql: str, catalog: SourceCatalog) -> PartitionPlan:
    """Partition a conceptual SPARQL query into per-source sub-queries.

    Args:
        sparql: a conceptual SPARQL SELECT over the master ontology.
        catalog: the concept→source index.

    Returns:
        A :class:`PartitionPlan`. Sub-queries are ordered by ``source_id`` for
        determinism; ``unresolved`` lists any triple whose concept/property maps
        to no known source.
    """
    algebra = prepareQuery(sparql).algebra
    projection = tuple(f"?{v}" for v in (algebra.get("PV") or []))

    unsupported: set[str] = set()
    _check_supported(algebra, unsupported)
    if unsupported:
        constructs = sorted(_UNSUPPORTED_NODES[n] for n in unsupported)
        raise UnsupportedQueryError(
            "E1 partitions basic graph patterns only; unsupported construct(s): "
            + ", ".join(constructs)
        )

    triples: list[_TermTriple] = []
    _collect_bgp_triples(algebra, triples)

    # Pass 1 — class binding.
    var_source: dict[Variable, SourceRef] = {}
    for subject, predicate, obj in triples:
        if predicate == RDF.type and isinstance(obj, URIRef) and isinstance(subject, Variable):
            source = catalog.source_of_class(str(obj))
            if source is not None:
                var_source[subject] = source

    # Pass 2 — route each triple to a source bucket.
    buckets: dict[SourceRef, list[_TermTriple]] = {}
    unresolved: list[_TermTriple] = []
    for triple in triples:
        source = _route(triple, catalog, var_source)
        if source is None:
            unresolved.append(triple)
        else:
            buckets.setdefault(source, []).append(triple)

    # Join keys — variables that appear under more than one source.
    var_sources: dict[str, set[SourceRef]] = {}
    for source, source_triples in buckets.items():
        for triple in source_triples:
            for var in _triple_vars(triple):
                var_sources.setdefault(f"?{var}", set()).add(source)
    join_keys = tuple(sorted(v for v, srcs in var_sources.items() if len(srcs) > 1))

    # Build per-source sub-queries (stable order).
    sub_queries: list[SubQuery] = []
    for source in sorted(buckets, key=lambda s: s.source_id):
        source_triples = buckets[source]
        ordered_vars: list[str] = []
        present: set[str] = set()
        for triple in source_triples:
            for var in _triple_vars(triple):
                name = f"?{var}"
                if name not in present:
                    present.add(name)
                    ordered_vars.append(name)

        select_vars = [v for v in projection if v in present]
        for key in join_keys:
            if key in present and key not in select_vars:
                select_vars.append(key)

        sub_queries.append(
            SubQuery(
                source=source,
                triples=tuple(_public(t) for t in source_triples),
                variables=tuple(ordered_vars),
                sparql=_serialize(select_vars, source_triples),
            )
        )

    return PartitionPlan(
        sub_queries=tuple(sub_queries),
        join_keys=join_keys,
        projection=projection,
        unresolved=tuple(_public(t) for t in unresolved),
    )

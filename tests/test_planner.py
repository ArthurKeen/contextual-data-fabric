"""Tests for the query-graph partition planner (cdf.query.planner)."""

from __future__ import annotations

import pytest

from cdf.query import SourceCatalog, UnsupportedQueryError, partition_query

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"


def _csi(kind, ref, entities, relationships=()):
    return {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {"name": n, "properties": [{"name": p} for p in props]} for n, props in entities
            ],
            "relationships": [
                {"type": t, "fromEntity": f, "toEntity": to} for t, f, to in relationships
            ],
        },
        "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
        "provenance": {"producer": "test", "direction": "forward",
                       "source": {"kind": kind, "ref": ref}},
    }


def _two_source_catalog() -> SourceCatalog:
    # Relational source owns Order (+ its FK relationship placed_by);
    # graph source owns User.
    return SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "shop", [("Order", ["id", "total"])],
                 [("placed_by", "Order", "User")]),
            _csi("arango", "docs", [("User", ["name"])]),
        ]
    )


def _sub_by_kind(plan):
    return {sq.source.kind: sq for sq in plan.sub_queries}


def test_single_source_no_join_keys():
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("Order", ["id", "total"])])]
    )
    plan = partition_query(
        PREFIX + "SELECT ?o ?t WHERE { ?o a c:Order ; c:total ?t }", cat
    )
    assert plan.is_single_source
    assert plan.join_keys == ()
    assert plan.sub_queries[0].source.kind == "postgresql"
    assert not plan.unresolved


def test_two_source_partition_and_join_key():
    plan = partition_query(
        PREFIX
        + """SELECT ?u ?name WHERE {
             ?o a c:Order ; c:placed_by ?u ; c:total ?t .
             ?u a c:User ; c:name ?name .
           }""",
        _two_source_catalog(),
    )
    subs = _sub_by_kind(plan)
    assert set(subs) == {"postgresql", "arango"}
    # ?u bridges the two sources -> it is the join key.
    assert plan.join_keys == ("?u",)
    # The relationship triple stays with its subject's (relational) source.
    pg_preds = {t.predicate for t in subs["postgresql"].triples}
    assert "<urn:arango-sparql:concept#placed_by>" in pg_preds
    assert "<urn:arango-sparql:concept#total>" in pg_preds
    # The User property is answered by the graph source.
    graph_preds = {t.predicate for t in subs["arango"].triples}
    assert "<urn:arango-sparql:concept#name>" in graph_preds
    assert not plan.unresolved


def test_join_key_selected_in_both_sub_queries():
    plan = partition_query(
        PREFIX
        + """SELECT ?u ?name WHERE {
             ?o a c:Order ; c:placed_by ?u .
             ?u a c:User ; c:name ?name .
           }""",
        _two_source_catalog(),
    )
    subs = _sub_by_kind(plan)
    assert "?u" in subs["postgresql"].sparql
    assert "?u" in subs["arango"].sparql
    # Each sub-query is a self-contained SELECT.
    assert subs["postgresql"].sparql.startswith("SELECT")
    assert subs["arango"].sparql.startswith("SELECT")


def test_shared_property_name_resolved_by_class_binding():
    # Both sources expose a "name" property; the subject's class must decide.
    cat = SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "shop", [("Order", ["name"])]),
            _csi("arango", "docs", [("User", ["name"])]),
        ]
    )
    plan = partition_query(
        PREFIX + "SELECT ?n WHERE { ?u a c:User ; c:name ?n }", cat
    )
    subs = _sub_by_kind(plan)
    # Routed to the graph source (User's source), NOT left unresolved/ambiguous.
    assert set(subs) == {"arango"}
    assert not plan.unresolved
    assert any(t.predicate.endswith("name>") for t in subs["arango"].triples)


def test_unknown_class_is_unresolved_not_dropped():
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("Order", ["id"])])]
    )
    plan = partition_query(
        PREFIX + "SELECT ?x WHERE { ?x a c:Ghost ; c:ghostprop ?i }", cat
    )
    # Unknown class (?x can't bind) + unknown property -> both unresolved.
    assert len(plan.unresolved) == 2
    assert plan.sub_queries == ()


def test_unknown_property_on_known_class_routes_to_class_source():
    # A property absent from the catalog still routes to the subject's class
    # source (class binding wins) rather than being dropped.
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("Order", ["id"])])]
    )
    plan = partition_query(
        PREFIX + "SELECT ?o WHERE { ?o a c:Order ; c:mystery ?m }", cat
    )
    assert not plan.unresolved
    assert plan.sub_queries[0].source.kind == "postgresql"
    assert len(plan.sub_queries[0].triples) == 2


def test_projection_is_preserved():
    plan = partition_query(
        PREFIX + "SELECT ?u ?name WHERE { ?u a c:User ; c:name ?name }",
        SourceCatalog.from_csi_documents([_csi("arango", "docs", [("User", ["name"])])]),
    )
    assert plan.projection == ("?u", "?name")


@pytest.mark.parametrize(
    "pattern",
    [
        "?u a c:User ; c:name ?n . FILTER(?n = 'x')",
        "?u a c:User . OPTIONAL { ?u c:name ?n }",
        "{ ?u a c:User } UNION { ?u a c:Order }",
        "?u a c:User BIND('x' AS ?n)",
    ],
)
def test_unsupported_constructs_refuse_not_silently_drop(pattern):
    cat = SourceCatalog.from_csi_documents(
        [_csi("arango", "docs", [("User", ["name"])], ())]
    )
    with pytest.raises(UnsupportedQueryError):
        partition_query(PREFIX + f"SELECT ?u WHERE {{ {pattern} }}", cat)


def test_order_by_and_limit_are_allowed():
    # Result modifiers don't affect partitioning; they must NOT be refused.
    cat = SourceCatalog.from_csi_documents([_csi("arango", "docs", [("User", ["name"])])])
    plan = partition_query(
        PREFIX + "SELECT ?u ?n WHERE { ?u a c:User ; c:name ?n } ORDER BY ?n LIMIT 10",
        cat,
    )
    assert plan.sub_queries[0].source.kind == "arango"


def test_deterministic_sub_query_order():
    cat = _two_source_catalog()
    q = (
        PREFIX
        + "SELECT ?u WHERE { ?o a c:Order ; c:placed_by ?u . ?u a c:User ; c:name ?n }"
    )
    ids1 = [sq.source.source_id for sq in partition_query(q, cat).sub_queries]
    ids2 = [sq.source.source_id for sq in partition_query(q, cat).sub_queries]
    assert ids1 == ids2 == sorted(ids1)

"""Native ClickHouse :class:`SourceExecutor` (M5 — the source Ontop can't drive).

Ontop has no ClickHouse dialect, so the relational leg for a ClickHouse source is
generated natively here. This is r2g's retired P12.2 pushdown-SQL path, resurrected
for exactly the engine the "adopt Ontop" decision doesn't reach — and it earns its
keep because E1 hands each leg a **single-source pattern** — a Basic Graph Pattern
plus any single-leg ``FILTER`` conjuncts / ``OPTIONAL`` columns E1 pushes down
(``UNION``/aggregation are still refused) — so compiling it to SQL is a bounded
problem, not general SPARQL→SQL.

It consumes the **same R2RML** r2g's ``export-r2rml`` emits (concept→table via
``rr:logicalTable``/``rr:tableName``, property→column via ``rr:column``, concept
IRIs under ``urn:arango-sparql:concept#``) — so a ClickHouse source is mapped by
the identical toolchain as an Ontop source; only the executor differs.

Compilation (single-source BGP → ClickHouse SQL):

- ``?s a c:Class``            → a table in FROM (one alias per subject variable),
- ``?s c:prop ?v``           → ``alias.col`` exposed as ``?v``,
- ``?s c:prop "lit"``        → ``WHERE alias.col = 'lit'``,
- ``FILTER(?v op lit)``      → ``WHERE col op lit`` (E1's pushed-down filter),
- a variable shared by two subjects → an equi-join between their columns,
- the E2 bind-join ``VALUES (?k) {…}`` → ``WHERE col IN (…)`` (FR-13),
- the SELECT → ``col AS <bare-var>`` so result rows are already bindings.

The DB call is an injectable ``transport`` so the R2RML parse + SQL compiler are
unit-testable without a database; the default transport uses ``clickhouse-connect``.
**Only a live ClickHouse validates the emitted dialect** — see ``deploy/clickhouse``
and the opt-in live test (a fake transport accepts SQL a real server may reject).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from rdflib import RDF, Graph, Literal, Namespace, URIRef
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import Variable

from cdf.query.executor import Binding, SourceResult
from cdf.query.types import SubQuery

_RR = Namespace("http://www.w3.org/ns/r2rml#")
# (sql) -> iterable of result rows (each a dict keyed by the SELECT column alias)
Transport = Callable[[str], Any]

# class IRI -> {"table": str, "columns": {property IRI: column name}}
Mapping = dict[str, dict[str, Any]]


class ClickHouseError(ValueError):
    """Raised when an R2RML mapping or a sub-query cannot be compiled."""


# ---------------------------------------------------------------------------
# R2RML → mapping
# ---------------------------------------------------------------------------


def parse_r2rml(turtle: str) -> Mapping:
    """Parse r2g-emitted R2RML into a ``{class_iri: {table, columns}}`` map."""
    graph = Graph()
    graph.parse(data=turtle, format="turtle")
    mapping: Mapping = {}
    for tm in graph.subjects(RDF.type, _RR.TriplesMap):
        logical = graph.value(tm, _RR.logicalTable)
        subject = graph.value(tm, _RR.subjectMap)
        if logical is None or subject is None:
            continue
        table = graph.value(logical, _RR.tableName)
        cls = graph.value(subject, _RR["class"])
        if table is None or cls is None:
            continue
        entry = mapping.setdefault(str(cls), {"table": str(table), "columns": {}})
        for pom in graph.objects(tm, _RR.predicateObjectMap):
            predicate = graph.value(pom, _RR.predicate)
            obj = graph.value(pom, _RR.objectMap)
            column = graph.value(obj, _RR.column) if obj is not None else None
            if predicate is not None and column is not None:
                entry["columns"][str(predicate)] = str(column)
    return mapping


# ---------------------------------------------------------------------------
# SQL emission helpers
# ---------------------------------------------------------------------------


def _ident(name: str) -> str:
    """Backtick-quote a ClickHouse identifier."""
    return "`" + name.replace("`", "``") + "`"


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _literal_value(term: Literal) -> Any:
    """Python value of a SPARQL literal (typed numbers/bools coerced)."""
    try:
        py = term.toPython()
    except Exception:  # noqa: BLE001 — malformed typed literal → use the lexical form
        return str(term)
    return py


def _collect_bgp(node: Any, out: list[tuple[Any, Any, Any]]) -> None:
    if isinstance(node, CompValue):
        if node.name == "BGP":
            out.extend(node["triples"])
        for value in node.values():
            _collect_bgp(value, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect_bgp(item, out)


_VALUES_RE = re.compile(r"VALUES\s*\(([^)]*)\)\s*\{(.*)\}\s*$", re.IGNORECASE | re.DOTALL)
_ROW_RE = re.compile(r"\(([^)]*)\)")
_TERM_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\S+')


def _parse_values(sparql: str) -> tuple[list[str], list[list[Any]]]:
    """Extract a trailing ``VALUES (?v …) { (…) … }`` clause (E2's bind-join)."""
    match = _VALUES_RE.search(sparql)
    if not match:
        return [], []
    variables = [v.lstrip("?") for v in match.group(1).split()]
    rows: list[list[Any]] = []
    for row_text in _ROW_RE.findall(match.group(2)):
        terms: list[Any] = []
        for tok in _TERM_RE.findall(row_text):
            if tok == "UNDEF":
                terms.append(None)
            elif tok.startswith('"'):
                terms.append(tok[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
            else:
                try:
                    terms.append(int(tok))
                except ValueError:
                    try:
                        terms.append(float(tok))
                    except ValueError:
                        terms.append(tok)
        rows.append(terms)
    return variables, rows


#: Comparison operators a pushed-down E1 FILTER conjunct may use (SQL-identical).
_FILTER_OPS = {">", "<", ">=", "<=", "=", "!="}
_FLIP_OP = {">": "<", "<": ">", ">=": "<=", "<=": ">=", "=": "=", "!=": "!="}


def _collect_filters(node: Any, out: list[tuple[Variable, str, Literal]]) -> None:
    """Collect the ``?var op literal`` conjuncts E1 pushes into a single leg.

    Walks the algebra for ``Filter`` nodes and extracts conjunctions of simple
    comparisons — the exact shape the planner guarantees before pushdown. A
    ``FILTER`` this cannot map is **raised, never dropped**: a silently-dropped
    filter would broaden the answer (the fabric's "never silent omission" rule).
    Shared with the Snowflake compiler (same dialect-neutral parse)."""
    if isinstance(node, CompValue):
        if node.name == "Filter":
            _collect_filter_expr(node["expr"], out)
        for value in node.values():
            _collect_filters(value, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect_filters(item, out)


def _collect_filter_expr(expr: Any, out: list[tuple[Variable, str, Literal]]) -> None:
    name = getattr(expr, "name", None)
    if name == "ConditionalAndExpression":
        _collect_filter_expr(expr["expr"], out)
        for sub in expr["other"]:
            _collect_filter_expr(sub, out)
        return
    if name == "RelationalExpression":
        left, op, right = expr["expr"], expr["op"], expr["other"]
        if op in _FILTER_OPS:
            if isinstance(left, Variable) and isinstance(right, Literal):
                out.append((left, op, right))
                return
            if isinstance(left, Literal) and isinstance(right, Variable):
                out.append((right, _FLIP_OP[op], left))
                return
    raise ClickHouseError(
        "FILTER cannot be compiled to SQL (expected a conjunction of "
        f"`?var {{{','.join(sorted(_FILTER_OPS))}}} literal`): {name or expr!r}"
    )


# ---------------------------------------------------------------------------
# BGP → ClickHouse SQL
# ---------------------------------------------------------------------------


def compile_sql(sparql: str, mapping: Mapping) -> str:
    """Compile a single-source BGP sub-query to ClickHouse SQL."""
    algebra = prepareQuery(sparql).algebra
    projection = [str(v) for v in (algebra.get("PV") or [])]
    triples: list[tuple[Any, Any, Any]] = []
    _collect_bgp(algebra, triples)

    subject_class: dict[Variable, str] = {}
    for subject, predicate, obj in triples:
        if predicate == RDF.type and isinstance(obj, URIRef) and isinstance(subject, Variable):
            subject_class[subject] = str(obj)
    if not subject_class:
        raise ClickHouseError("sub-query has no typed subject to map to a table")

    aliases: dict[Variable, str] = {}
    from_parts: list[str] = []
    for i, (svar, cls) in enumerate(subject_class.items()):
        if cls not in mapping:
            raise ClickHouseError(f"class {cls!r} is not in the R2RML mapping")
        alias = f"t{i}"
        aliases[svar] = alias
        from_parts.append(f"{_ident(mapping[cls]['table'])} AS {alias}")

    def column_expr(svar: Variable, predicate: URIRef) -> str:
        cols = mapping[subject_class[svar]]["columns"]
        col = cols.get(str(predicate))
        if col is None:
            raise ClickHouseError(
                f"property {predicate!r} not mapped for class {subject_class[svar]!r}"
            )
        return f"{aliases[svar]}.{_ident(col)}"

    var_exprs: dict[Variable, list[str]] = {}
    where: list[str] = []
    for subject, predicate, obj in triples:
        if predicate == RDF.type:
            continue
        if subject not in aliases:
            raise ClickHouseError("a property is used on an untyped subject")
        expr = column_expr(subject, predicate)
        if isinstance(obj, Variable):
            var_exprs.setdefault(obj, []).append(expr)
        elif isinstance(obj, Literal):
            where.append(f"{expr} = {_sql_literal(_literal_value(obj))}")
        elif isinstance(obj, URIRef):
            where.append(f"{expr} = {_sql_literal(str(obj))}")

    # A variable shared by more than one triple is an equi-join.
    for exprs in var_exprs.values():
        for other in exprs[1:]:
            where.append(f"{exprs[0]} = {other}")

    # Bind-join: VALUES(?k){…} -> col IN (…) (single var) or tuple IN (multi-var).
    val_vars, val_rows = _parse_values(sparql)
    if val_vars and val_rows:
        maybe_cols = [var_exprs.get(Variable(v), [None])[0] for v in val_vars]
        cols = [c for c in maybe_cols if c is not None]
        if len(cols) == len(val_vars):  # every VALUES var is bound to a column
            if len(cols) == 1:
                inlist = ", ".join(_sql_literal(r[0]) for r in val_rows)
                where.append(f"{cols[0]} IN ({inlist})")
            else:
                tuples = ", ".join(
                    "(" + ", ".join(_sql_literal(v) for v in r) + ")" for r in val_rows
                )
                where.append(f"({', '.join(cols)}) IN ({tuples})")

    # Pushed-down E1 FILTER conjuncts: ?var op literal -> col op literal.
    filters: list[tuple[Variable, str, Literal]] = []
    _collect_filters(algebra, filters)
    for fvar, fop, flit in filters:
        bound = var_exprs.get(fvar)
        if not bound:
            raise ClickHouseError(f"FILTER references variable {fvar!r} not bound in this leg")
        where.append(f"{bound[0]} {fop} {_sql_literal(_literal_value(flit))}")

    select: list[str] = []
    for pv in projection:
        var = Variable(pv)
        if var in var_exprs:
            select.append(f"{var_exprs[var][0]} AS {_ident(pv)}")
    sql = f"SELECT {', '.join(select) or '*'} FROM {', '.join(from_parts)}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql


# ---------------------------------------------------------------------------
# Transport + executor
# ---------------------------------------------------------------------------


class _ClickHouseTransport:
    """Lazily opened persistent client with an explicit close lifecycle."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._client: Any | None = None
        self._lock = threading.Lock()

    def __call__(self, sql: str) -> Any:
        import clickhouse_connect  # lazy: engine driver, not a core dep

        with self._lock:
            if self._client is None:
                self._client = clickhouse_connect.get_client(dsn=self._dsn)
            client = self._client
        return client.query(sql).named_results()

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _clickhouse_transport(dsn: str) -> Transport:
    """Default transport: run SQL over a reusable ``clickhouse-connect`` client."""
    return _ClickHouseTransport(dsn)


class ClickHouseExecutor:
    """Executes a sub-query against ClickHouse by compiling R2RML+BGP → SQL."""

    def __init__(
        self,
        *,
        r2rml: str | None = None,
        mapping: Mapping | None = None,
        transport: Transport | None = None,
        dsn: str | None = None,
        source_objects: tuple[str, ...] = (),
        clock: Callable[[], str] | None = None,
    ) -> None:
        """
        Args:
            r2rml: R2RML Turtle (as emitted by ``r2g export-r2rml``) — parsed to
                the concept→table/column mapping. Provide this or ``mapping``.
            mapping: a pre-parsed mapping (dependency injection for tests).
            transport: ``(sql) -> rows`` seam; default runs clickhouse-connect.
            dsn: ClickHouse DSN for the default transport (e.g.
                ``clickhouse://user:pw@host:8123/db``).
            source_objects: physical tables served, cited per result (FR-2).
            clock: as-of stamp provider (FR-12); defaults to UTC now.
        """
        if mapping is None:
            if r2rml is None:
                raise ValueError("ClickHouseExecutor requires r2rml or mapping")
            mapping = parse_r2rml(r2rml)
        if not mapping:
            raise ValueError("R2RML mapping is empty (no TriplesMaps parsed)")
        if transport is None:
            if not dsn:
                raise ValueError("ClickHouseExecutor requires a transport or a dsn")
            transport = _clickhouse_transport(dsn)
        self._mapping = mapping
        self._transport = transport
        self._source_objects = tuple(source_objects)
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def execute(self, subquery: SubQuery) -> SourceResult:
        sql = compile_sql(subquery.sparql, self._mapping)
        rows: tuple[Binding, ...] = tuple(dict(row) for row in self._transport(sql))
        return SourceResult(
            rows=rows,
            native_query=sql,
            as_of=self._clock(),
            source_objects=self._source_objects,
        )

    def close(self) -> None:
        """Close an injected/persistent transport when it exposes lifecycle."""
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()

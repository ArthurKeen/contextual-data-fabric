"""Tests for the federated executor (cdf.query.executor).

Uses real E1 plans (partition_query) fed to in-memory fake source executors, so
the join/reassembly, retrieval path, and partial-failure semantics are exercised
against genuine partition contracts.
"""

from __future__ import annotations

from cdf.query import SourceCatalog, SourceResult, execute_plan, partition_query

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"


class FakeExecutor:
    def __init__(self, rows, *, native=None, as_of=None, fail=False):
        self._rows = tuple(rows)
        self._native = native
        self._as_of = as_of
        self._fail = fail

    def execute(self, subquery):
        if self._fail:
            raise RuntimeError("source unavailable")
        return SourceResult(rows=self._rows, native_query=self._native, as_of=self._as_of)


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


def _catalog():
    return SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "shop", [("Order", ["id", "total"])],
                 [("placed_by", "Order", "User")]),
            _csi("arango", "docs", [("User", ["name"])]),
        ]
    )


_Q = (
    PREFIX
    + """SELECT ?u ?name WHERE {
         ?o a c:Order ; c:placed_by ?u ; c:total ?t .
         ?u a c:User ; c:name ?name .
       }"""
)


def test_happy_path_joins_and_projects():
    plan = partition_query(_Q, _catalog())
    result = execute_plan(
        plan,
        {
            "postgresql:shop": FakeExecutor(
                [{"o": "o1", "u": "u1", "t": 100}, {"o": "o2", "u": "u2", "t": 50}],
                native="SELECT ... FROM orders",
                as_of="2026-07-15T00:00:00Z",
            ),
            "arango:docs": FakeExecutor(
                [{"u": "u1", "name": "Alice"}], as_of="2026-07-14T00:00:00Z"
            ),
        },
    )
    assert result.partial is False
    assert result.failed_sources == ()
    # Only u1 matches across both legs; projected to ?u ?name.
    assert result.bindings == ({"u": "u1", "name": "Alice"},)


def test_retrieval_path_records_each_leg():
    plan = partition_query(_Q, _catalog())
    result = execute_plan(
        plan,
        {
            "postgresql:shop": FakeExecutor(
                [{"o": "o1", "u": "u1", "t": 100}], native="SQL-TEXT", as_of="T1"
            ),
            "arango:docs": FakeExecutor([{"u": "u1", "name": "Alice"}], native="AQL-TEXT"),
        },
    )
    steps = {s.source_id: s for s in result.retrieval_path}
    assert set(steps) == {"postgresql:shop", "arango:docs"}
    assert steps["postgresql:shop"].status == "ok"
    assert steps["postgresql:shop"].native_query == "SQL-TEXT"
    assert steps["postgresql:shop"].as_of == "T1"
    assert steps["postgresql:shop"].row_count == 1
    # The sub-query SPARQL is carried for citation.
    assert "SELECT" in steps["arango:docs"].sparql


def test_non_matching_join_is_empty_not_partial():
    plan = partition_query(_Q, _catalog())
    result = execute_plan(
        plan,
        {
            "postgresql:shop": FakeExecutor([{"o": "o1", "u": "u1", "t": 100}]),
            "arango:docs": FakeExecutor([{"u": "u9", "name": "Nobody"}]),
        },
    )
    # A legitimate empty answer is NOT partial.
    assert result.bindings == ()
    assert result.partial is False


def test_cartesian_on_multiple_matches():
    plan = partition_query(_Q, _catalog())
    result = execute_plan(
        plan,
        {
            "postgresql:shop": FakeExecutor(
                [{"o": "o1", "u": "u1", "t": 100}, {"o": "o2", "u": "u1", "t": 50}]
            ),
            "arango:docs": FakeExecutor([{"u": "u1", "name": "Alice"}]),
        },
    )
    assert result.bindings == ({"u": "u1", "name": "Alice"}, {"u": "u1", "name": "Alice"})


def test_leg_failure_is_declared_not_raised():
    plan = partition_query(_Q, _catalog())
    result = execute_plan(
        plan,
        {
            "postgresql:shop": FakeExecutor([{"o": "o1", "u": "u1", "t": 100}]),
            "arango:docs": FakeExecutor([], fail=True),
        },
    )
    assert result.partial is True
    assert result.failed_sources == ("arango:docs",)
    # 'name' comes only from the failed graph leg -> flagged unavailable.
    assert result.unavailable_vars == ("name",)
    # The surviving leg still contributes what it can (?u).
    assert result.bindings == ({"u": "u1"},)
    failed_step = next(s for s in result.retrieval_path if s.source_id == "arango:docs")
    assert failed_step.status == "failed"
    assert "source unavailable" in failed_step.error


def test_missing_executor_treated_as_failed_leg():
    plan = partition_query(_Q, _catalog())
    result = execute_plan(
        plan,
        {"postgresql:shop": FakeExecutor([{"o": "o1", "u": "u1", "t": 100}])},
    )
    assert result.partial is True
    assert result.failed_sources == ("arango:docs",)
    step = next(s for s in result.retrieval_path if s.source_id == "arango:docs")
    assert step.status == "failed"
    assert "no executor" in step.error


def test_unresolved_plan_marks_result_partial():
    cat = SourceCatalog.from_csi_documents([_csi("arango", "docs", [("User", ["name"])])])
    plan = partition_query(
        PREFIX + "SELECT ?u WHERE { ?u a c:User ; c:name ?n . ?x a c:Ghost }", cat
    )
    assert plan.unresolved  # sanity: E1 flagged the ghost
    result = execute_plan(plan, {"arango:docs": FakeExecutor([{"u": "u1", "name": "A"}])})
    assert result.partial is True
    assert result.unresolved == plan.unresolved


def test_single_source_passthrough():
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("Order", ["id", "total"])])]
    )
    plan = partition_query(PREFIX + "SELECT ?o ?t WHERE { ?o a c:Order ; c:total ?t }", cat)
    result = execute_plan(
        plan,
        {"postgresql:shop": FakeExecutor([{"o": "o1", "t": 100}, {"o": "o2", "t": 50}])},
    )
    assert result.partial is False
    assert result.bindings == ({"o": "o1", "t": 100}, {"o": "o2", "t": 50})

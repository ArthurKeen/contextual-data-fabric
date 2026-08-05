"""Tests for the grounding envelope + cite-or-refuse gate (cdf.query.grounding)."""

from __future__ import annotations

from cdf.query import (
    SourceCatalog,
    SourceResult,
    execute_plan,
    ground,
    partition_query,
)

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"


class FakeExecutor:
    def __init__(self, rows, *, native=None, as_of=None, objects=(), fail=False):
        self._rows = tuple(rows)
        self._native = native
        self._as_of = as_of
        self._objects = tuple(objects)
        self._fail = fail

    def execute(self, subquery):
        if self._fail:
            raise RuntimeError("source unavailable")
        return SourceResult(
            rows=self._rows,
            native_query=self._native,
            as_of=self._as_of,
            source_objects=self._objects,
        )


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


def _run(executors, query=_Q, catalog=None):
    plan = partition_query(query, catalog or _catalog())
    return execute_plan(plan, executors)


def test_grounded_answer_carries_citations():
    result = _run(
        {
            "postgresql:shop": FakeExecutor(
                [{"o": "o1", "u": "u1", "t": 100}],
                native="SELECT ... FROM orders",
                as_of="2026-07-15T00:00:00Z",
                objects=["public.orders"],
            ),
            "arango:docs": FakeExecutor(
                [{"u": "u1", "name": "Alice"}],
                native="FOR d IN users ...",
                as_of="2026-07-14T00:00:00Z",
                objects=["users"],
            ),
        }
    )
    env = ground(result)
    assert env.status == "grounded"
    assert env.is_grounded and not env.is_refused
    assert env.bindings == ({"u": "u1", "name": "Alice"},)
    # One citation per successful leg, carrying SQL/AQL + source objects + as-of.
    cites = {c.source_id: c for c in env.citations}
    assert set(cites) == {"postgresql:shop", "arango:docs"}
    assert cites["postgresql:shop"].native_query == "SELECT ... FROM orders"
    assert cites["postgresql:shop"].source_objects == ("public.orders",)
    assert cites["arango:docs"].as_of == "2026-07-14T00:00:00Z"
    assert env.refusal_reason is None
    assert env.execution_metrics is result.execution_metrics


def test_refuse_when_load_bearing_leg_fails():
    # Graph leg (sole source of ?name) fails -> requested column uncitable.
    result = _run(
        {
            "postgresql:shop": FakeExecutor([{"o": "o1", "u": "u1", "t": 100}]),
            "arango:docs": FakeExecutor([], fail=True),
        }
    )
    env = ground(result)
    assert env.status == "refused"
    assert env.bindings == ()
    assert "name" in env.refusal_reason
    assert "arango:docs" in env.refusal_reason
    # Only the surviving leg is cited; nothing fabricated.
    assert {c.source_id for c in env.citations} == {"postgresql:shop"}
    assert env.execution_metrics is result.execution_metrics


def test_strict_mode_refuses_any_partiality_by_default():
    # Projection is just ?u (available from the surviving relational leg), but a
    # leg still failed -> strict mode refuses (dropped constraint could broaden).
    q = PREFIX + "SELECT ?u WHERE { ?o a c:Order ; c:placed_by ?u . ?u a c:User ; c:name ?n }"
    result = _run(
        {
            "postgresql:shop": FakeExecutor([{"o": "o1", "u": "u1"}]),
            "arango:docs": FakeExecutor([], fail=True),
        },
        query=q,
    )
    env = ground(result)  # allow_partial defaults to False
    assert env.status == "refused"


def test_concierge_mode_allows_partial_when_columns_available():
    q = PREFIX + "SELECT ?u WHERE { ?o a c:Order ; c:placed_by ?u . ?u a c:User ; c:name ?n }"
    result = _run(
        {
            "postgresql:shop": FakeExecutor([{"o": "o1", "u": "u1"}]),
            "arango:docs": FakeExecutor([], fail=True),
        },
        query=q,
    )
    env = ground(result, allow_partial=True)
    assert env.status == "partial"
    assert env.bindings == ({"u": "u1"},)  # answer returned...
    assert env.failed_sources == ("arango:docs",)  # ...with the failure declared
    assert env.refusal_reason is None


def test_concierge_still_refuses_when_projected_var_unavailable():
    # Even in concierge mode, a missing REQUESTED column must refuse.
    result = _run(
        {
            "postgresql:shop": FakeExecutor([{"o": "o1", "u": "u1", "t": 100}]),
            "arango:docs": FakeExecutor([], fail=True),
        }
    )
    env = ground(result, allow_partial=True)
    assert env.status == "refused"
    assert "name" in env.refusal_reason


def test_unresolved_pattern_refuses_in_strict_mode():
    cat = SourceCatalog.from_csi_documents([_csi("arango", "docs", [("User", ["name"])])])
    result = _run(
        {"arango:docs": FakeExecutor([{"u": "u1", "name": "A"}])},
        query=PREFIX + "SELECT ?u WHERE { ?u a c:User ; c:name ?n . ?x a c:Ghost }",
        catalog=cat,
    )
    env = ground(result)
    assert env.status == "refused"
    assert "no known source" in env.refusal_reason


def test_empty_but_grounded_is_not_refused():
    # No matches across legs is a legitimate grounded empty answer.
    result = _run(
        {
            "postgresql:shop": FakeExecutor([{"o": "o1", "u": "u1", "t": 100}]),
            "arango:docs": FakeExecutor([{"u": "u9", "name": "Nobody"}]),
        }
    )
    env = ground(result)
    assert env.status == "grounded"
    assert env.bindings == ()
    assert len(env.citations) == 2


def test_retrieval_path_includes_failed_legs():
    result = _run(
        {
            "postgresql:shop": FakeExecutor([{"o": "o1", "u": "u1", "t": 100}]),
            "arango:docs": FakeExecutor([], fail=True),
        }
    )
    env = ground(result)
    statuses = {s.source_id: s.status for s in env.retrieval_path}
    assert statuses == {"postgresql:shop": "ok", "arango:docs": "failed"}

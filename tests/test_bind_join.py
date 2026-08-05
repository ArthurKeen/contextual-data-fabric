"""Bind-join tests (WP-P1.4 / FR-13 / CC-11).

Legs run in stages (relational, then graph); a stage-two leg sharing a join
variable with the stage-one join receives the accumulated distinct key rows as
a trailing ``VALUES`` clause — visible in the retrieval path (the seeded SPARQL
is the cited SPARQL). Concurrency within a stage is covered in
``test_executor.py``.
"""

from __future__ import annotations

from cdf.query import SourceCatalog, SourceResult, execute_plan, partition_query
from cdf.query.executor import _with_values

_ACCOUNTS_CSI = {
    "csiVersion": "1",
    "conceptualModel": {
        "entities": [
            {"name": "Account", "properties": [{"name": "accountId"}, {"name": "accountName"}]}
        ]
    },
    "physicalMapping": {"entities": {"Account": {"tableName": "accounts"}}},
    "provenance": {"producer": "r2g", "direction": "forward",
                   "source": {"kind": "postgresql", "ref": "crm"}},
}

_TICKETS_CSI = {
    "csiVersion": "1",
    "conceptualModel": {
        "entities": [
            {"name": "Ticket", "properties": [{"name": "subject"}, {"name": "accountId"}]}
        ]
    },
    "arangoPhysicalMapping": {
        "entities": {"Ticket": {"style": "COLLECTION", "collectionName": "tickets"}},
        "relationships": {},
    },
    "provenance": {"producer": "analyzer", "direction": "reverse",
                   "source": {"kind": "arango", "ref": "tickets"}},
}

# account_id appears on both sources → the property routes by the subject's
# class (planner pass 2), and the shared ?acct variable is the join key.
_JOIN_SPARQL = (
    "PREFIX c: <urn:arango-sparql:concept#> "
    "SELECT ?name ?subject WHERE { "
    "  ?a a c:Account ; c:accountName ?name ; c:accountId ?acct . "
    "  ?t a c:Ticket ; c:subject ?subject ; c:accountId ?acct . }"
)


class _Recorder:
    """Stub executor that records the SPARQL it was asked to run."""

    def __init__(self, rows):
        self.rows = rows
        self.seen_sparql: str | None = None
        self.seen_sparqls: list[str] = []

    def execute(self, subquery):
        self.seen_sparql = subquery.sparql
        self.seen_sparqls.append(subquery.sparql)
        return SourceResult(rows=tuple(self.rows), native_query="native", as_of="t0")


def _catalog() -> SourceCatalog:
    return SourceCatalog.from_csi_documents([_ACCOUNTS_CSI, _TICKETS_CSI])


def test_second_leg_is_seeded_with_first_legs_join_keys() -> None:
    plan = partition_query(_JOIN_SPARQL, _catalog())
    assert {v.lstrip("?") for v in plan.join_keys} == {"acct"}

    pg = _Recorder([
        {"a": "urn:r1", "name": "Meridian", "acct": "001bbkuFW1b7KegAZT"},
        {"a": "urn:r2", "name": "Northwind", "acct": "001LxbLlyzNOfmaOHp"},
    ])
    ar = _Recorder([
        {"t": "tickets/1", "subject": "escalation", "acct": "001bbkuFW1b7KegAZT"},
    ])
    result = execute_plan(plan, {"postgresql:crm": pg, "arango:tickets": ar})

    # The Arango leg's SPARQL carries the pushed-down keys.
    assert ar.seen_sparql is not None
    assert "VALUES (?acct)" in ar.seen_sparql
    assert '"001bbkuFW1b7KegAZT"' in ar.seen_sparql
    assert '"001LxbLlyzNOfmaOHp"' in ar.seen_sparql
    # …and the seeding is declared in the retrieval path.
    arango_step = next(s for s in result.retrieval_path if s.kind == "arango")
    assert arango_step.seeded_vars == ("acct",)
    assert "VALUES" in arango_step.sparql
    # The join produced exactly the Meridian pairing.
    assert result.bindings == ({"name": "Meridian", "subject": "escalation"},)
    assert not result.partial


def test_seed_over_batch_size_uses_values_batches_and_deduplicates() -> None:
    plan = partition_query(_JOIN_SPARQL, _catalog())
    pg = _Recorder([{"a": f"urn:{i}", "name": f"n{i}", "acct": f"k{i}"} for i in range(5)])
    ar = _Recorder([{"t": "tickets/1", "subject": "s", "acct": "k3"}])
    result = execute_plan(plan, {"postgresql:crm": pg, "arango:tickets": ar}, seed_cap=2)

    assert len(ar.seen_sparqls) == 3
    assert all("VALUES (?acct)" in sparql for sparql in ar.seen_sparqls)
    seeded_values = " ".join(ar.seen_sparqls)
    assert all(f'"k{i}"' in seeded_values for i in range(5))
    arango_step = next(s for s in result.retrieval_path if s.kind == "arango")
    assert arango_step.seeded_vars == ("acct",)
    assert arango_step.seed_strategy == "values-batched"
    assert arango_step.seed_batch_count == 3
    metrics = result.execution_metrics
    assert metrics is not None
    arango_metrics = next(m for m in metrics.legs if m.kind == "arango")
    assert arango_metrics.seed_row_count == 5
    assert arango_metrics.seed_cap == 2
    assert arango_metrics.seed_cap_exceeded is True
    assert arango_metrics.seed_batch_count == 3
    assert arango_metrics.seed_overflow is False
    assert metrics.seed_cap_exceeded is True
    # Engine-side join still reconciles correctly.
    assert result.bindings == ({"name": "n3", "subject": "s"},)


def test_values_serialization_escapes_and_types() -> None:
    out = _with_values(
        "SELECT ?x WHERE { ?s ?p ?x }",
        ["x"],
        [{"x": 'say "hi"'}, {"x": 7}, {"x": True}, {"x": None}],
    )
    assert out.endswith('VALUES (?x) { ("say \\"hi\\"") (7) (true) (UNDEF) }')

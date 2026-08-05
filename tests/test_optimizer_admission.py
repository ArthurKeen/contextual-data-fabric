"""P2.2 statistics planning, admission, and runtime-cap tests."""

from __future__ import annotations

import time

import pytest

from cdf.query import (
    PlanAdmissionPolicy,
    SourceCatalog,
    SourceResult,
    estimate_plan,
    execute_plan,
    partition_query,
)
from cdf.service import FederationService

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"
QUERY = PREFIX + """SELECT ?name ?qv ?src WHERE {
  ?a a c:Account ; c:accountId ?k ; c:accountName ?name .
  ?u a c:UsageMetric ; c:accountId ?k ; c:queryVolumeM ?qv .
  ?d a c:Document ; c:accountId ?k ; c:source ?src .
}"""


def _csi(kind, ref, entity, properties, rows=None, *, cost_rate=None):
    document = {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {"name": entity, "properties": [{"name": name} for name in properties]}
            ]
        },
        "provenance": {
            "producer": "test",
            "direction": "forward",
            "source": {"kind": kind, "ref": ref},
        },
    }
    if rows is not None:
        source = {"rowCount": rows, "estimatedBytes": rows * 100}
        if cost_rate is not None:
            source["costPerGbUsd"] = cost_rate
        document["statistics"] = {
            "version": "1",
            "snapshotId": f"{ref}-snapshot",
            "asOf": "2026-08-05T12:00:00Z",
            "source": source,
            "classes": {
                entity: {
                    "rowCount": rows,
                    "estimatedBytes": rows * 100,
                    "properties": {"accountId": {"ndv": max(1, rows)}},
                }
            },
        }
    return document


def _catalog(*, stats=True):
    rows = (100, 10, 1000) if stats else (None, None, None)
    return SourceCatalog.from_csi_documents(
        [
            _csi(
                "postgresql",
                "crm",
                "Account",
                ["accountId", "accountName"],
                rows[0],
                cost_rate=0.1,
            ),
            _csi(
                "snowflake",
                "telemetry",
                "UsageMetric",
                ["accountId", "queryVolumeM"],
                rows[1],
            ),
            _csi(
                "arango",
                "cmf",
                "Document",
                ["accountId", "source"],
                rows[2],
                cost_rate=0.2,
            ),
        ]
    )


class Recorder:
    def __init__(self, rows, calls, source_id, *, delay=0.0, fail_on_call=None):
        self.rows = tuple(rows)
        self.calls = calls
        self.source_id = source_id
        self.delay = delay
        self.fail_on_call = fail_on_call
        self.count = 0

    def execute(self, subquery):
        self.count += 1
        self.calls.append(self.source_id)
        if self.delay:
            time.sleep(self.delay)
        if self.fail_on_call == self.count:
            raise RuntimeError("batch failed")
        return SourceResult(rows=self.rows)


def _executors(calls):
    return {
        "postgresql:crm": Recorder([{"k": "A", "name": "Acme"}], calls, "postgresql:crm"),
        "snowflake:telemetry": Recorder(
            [{"k": "A", "qv": 12.5}], calls, "snowflake:telemetry"
        ),
        "arango:cmf": Recorder([{"k": "A", "src": "slack"}], calls, "arango:cmf"),
    }


def test_fixed_statistics_choose_stable_selective_first_dp_plan():
    catalog = _catalog()
    plan = partition_query(QUERY, catalog)
    first = estimate_plan(plan, catalog)
    second = estimate_plan(plan, catalog)
    assert first == second
    assert first.strategy == "dynamic-programming"
    assert first.execution_order == (
        "snowflake:telemetry",
        "arango:cmf",
        "postgresql:crm",
    )
    assert first.stages == tuple((source_id,) for source_id in first.execution_order)
    assert first.legs[0].estimated_rows == 10
    assert first.legs[0].snapshot_id == "telemetry-snapshot"
    assert first.estimated_cost_usd is None  # one source has no rate: unknown, not free


def test_no_statistics_preserves_legacy_safe_stages():
    catalog = _catalog(stats=False)
    estimate = estimate_plan(partition_query(QUERY, catalog), catalog)
    assert estimate.strategy == "legacy-no-statistics"
    assert estimate.stages == (
        ("postgresql:crm", "snowflake:telemetry"),
        ("arango:cmf",),
    )


def test_executor_uses_optimizer_order_and_preserves_answer():
    catalog = _catalog()
    plan = partition_query(QUERY, catalog)
    strategy = estimate_plan(plan, catalog)
    calls = []
    result = execute_plan(plan, _executors(calls), strategy=strategy)
    assert calls == list(strategy.execution_order)
    assert result.bindings == ({"name": "Acme", "qv": 12.5, "src": "slack"},)
    assert result.execution_metrics is not None
    assert result.execution_metrics.strategy == "dynamic-programming"


def test_independent_statistics_planned_legs_share_parallel_stage_metrics():
    catalog = _catalog()
    query = PREFIX + """SELECT ?name ?qv WHERE {
      ?a a c:Account ; c:accountName ?name .
      ?u a c:UsageMetric ; c:queryVolumeM ?qv .
    }"""
    plan = partition_query(query, catalog)
    strategy = estimate_plan(plan, catalog)
    assert strategy.stages == (("postgresql:crm", "snowflake:telemetry"),)
    calls = []
    result = execute_plan(
        plan,
        {
            "postgresql:crm": Recorder(
                [{"name": "Acme"}], calls, "postgresql:crm", delay=0.03
            ),
            "snowflake:telemetry": Recorder(
                [{"qv": 12.5}], calls, "snowflake:telemetry", delay=0.03
            ),
        },
        strategy=strategy,
    )
    metrics = result.execution_metrics
    assert metrics is not None
    assert metrics.execution_duration_ms < metrics.leg_duration_sum_ms
    assert result.bindings == ({"name": "Acme", "qv": 12.5},)


def test_preflight_refusal_makes_no_source_calls():
    catalog = _catalog()
    calls = []
    service = FederationService(
        catalog=catalog,
        executors=_executors(calls),
        admission_policy=PlanAdmissionPolicy(max_estimated_rows=1),
    )
    envelope = service.federate_sparql(QUERY)
    assert envelope.status == "refused"
    assert calls == []
    assert envelope.admission_refusal is not None
    assert envelope.admission_refusal.phase == "preflight"
    assert envelope.admission_refusal.metric == "estimated_rows"


def test_seed_rows_over_hard_cap_refuse_without_target_call():
    catalog = SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "crm", "Account", ["accountId", "accountName"]),
            _csi("arango", "cmf", "Document", ["accountId", "source"]),
        ]
    )
    query = PREFIX + """SELECT ?name ?src WHERE {
      ?a a c:Account ; c:accountId ?k ; c:accountName ?name .
      ?d a c:Document ; c:accountId ?k ; c:source ?src .
    }"""
    plan = partition_query(query, catalog)
    calls = []
    pg = Recorder(
        [{"k": f"K{i}", "name": f"N{i}"} for i in range(4)],
        calls,
        "postgresql:crm",
    )
    ar = Recorder([{"k": "K1", "src": "email"}], calls, "arango:cmf")
    result = execute_plan(
        plan,
        {"postgresql:crm": pg, "arango:cmf": ar},
        admission_policy=PlanAdmissionPolicy(seed_batch_rows=2, max_seed_rows=3),
    )
    assert calls == ["postgresql:crm"]
    assert result.admission_refusal is not None
    assert result.admission_refusal.code == "max_seed_rows_exceeded"
    assert result.execution_metrics is not None
    assert result.execution_metrics.seed_overflow is True


def test_failed_seed_batch_discards_earlier_batch_results():
    catalog = SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "crm", "Account", ["accountId", "accountName"]),
            _csi("arango", "cmf", "Document", ["accountId", "source"]),
        ]
    )
    query = PREFIX + """SELECT ?name ?src WHERE {
      ?a a c:Account ; c:accountId ?k ; c:accountName ?name .
      ?d a c:Document ; c:accountId ?k ; c:source ?src .
    }"""
    plan = partition_query(query, catalog)
    calls = []
    pg = Recorder(
        [{"k": f"K{i}", "name": f"N{i}"} for i in range(3)],
        calls,
        "postgresql:crm",
    )
    ar = Recorder(
        [{"k": "K0", "src": "email"}],
        calls,
        "arango:cmf",
        fail_on_call=2,
    )
    result = execute_plan(
        plan,
        {"postgresql:crm": pg, "arango:cmf": ar},
        admission_policy=PlanAdmissionPolicy(seed_batch_rows=2, max_seed_rows=5),
    )
    assert ar.count == 2
    assert result.partial is True
    assert result.failed_sources == ("arango:cmf",)
    assert all("src" not in binding for binding in result.bindings)


def test_runtime_intermediate_and_final_caps_are_declared():
    single_catalog = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "crm", "Account", ["accountName"])]
    )
    query = PREFIX + "SELECT ?name WHERE { ?a a c:Account ; c:accountName ?name }"

    calls = []
    slow = Recorder([{"name": "A"}], calls, "postgresql:crm", delay=0.01)
    runtime = FederationService(
        catalog=single_catalog,
        executors={"postgresql:crm": slow},
        admission_policy=PlanAdmissionPolicy(runtime_wall_time_ms=1),
    ).federate_sparql(query)
    assert runtime.status == "refused"
    assert runtime.admission_refusal is not None
    assert runtime.admission_refusal.code == "runtime_wall_time_exceeded"

    final = FederationService(
        catalog=single_catalog,
        executors={
            "postgresql:crm": Recorder(
                [{"name": "A"}, {"name": "B"}], [], "postgresql:crm"
            )
        },
        admission_policy=PlanAdmissionPolicy(max_final_rows=1),
    ).federate_sparql(query)
    assert final.status == "refused"
    assert final.admission_refusal is not None
    assert final.admission_refusal.code == "max_final_rows_exceeded"

    catalog = _catalog(stats=False)
    intermediate = FederationService(
        catalog=catalog,
        executors=_executors([]),
        admission_policy=PlanAdmissionPolicy(max_intermediate_rows=0),
    ).federate_sparql(QUERY)
    assert intermediate.status == "refused"
    assert intermediate.admission_refusal is not None
    assert intermediate.admission_refusal.code == "max_intermediate_rows_exceeded"


def test_generous_budgets_leave_answers_unchanged():
    catalog = _catalog()
    baseline = FederationService(catalog=catalog, executors=_executors([])).federate_sparql(
        QUERY
    )
    bounded = FederationService(
        catalog=catalog,
        executors=_executors([]),
        admission_policy=PlanAdmissionPolicy(
            max_estimated_rows=100,
            max_estimated_bytes=1_000_000,
            runtime_wall_time_ms=1000,
            max_intermediate_rows=100,
            max_final_rows=100,
        ),
    ).federate_sparql(QUERY)
    assert bounded.status == baseline.status == "grounded"
    assert bounded.bindings == baseline.bindings


def test_opt_in_final_cap_is_explicit_partial_truncation():
    catalog = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "crm", "Account", ["accountName"])]
    )
    query = PREFIX + "SELECT ?name WHERE { ?a a c:Account ; c:accountName ?name }"
    envelope = FederationService(
        catalog=catalog,
        executors={
            "postgresql:crm": Recorder(
                [{"name": "A"}, {"name": "B"}], [], "postgresql:crm"
            )
        },
        admission_policy=PlanAdmissionPolicy(
            max_final_rows=1,
            allow_partial_on_runtime_cap=True,
        ),
    ).federate_sparql(query, allow_partial=True)
    assert envelope.status == "partial"
    assert envelope.bindings == ({"name": "A"},)
    assert envelope.execution_metrics is not None
    assert envelope.execution_metrics.truncated is True


def test_admission_policy_environment_parsing_and_validation():
    policy = PlanAdmissionPolicy.from_env(
        {
            "CDF_MAX_ESTIMATED_ROWS": "100",
            "CDF_RUNTIME_WALL_TIME_MS": "12.5",
            "CDF_SEED_BATCH_ROWS": "25",
            "CDF_MAX_SEED_ROWS": "100",
            "CDF_ALLOW_PARTIAL_ON_RUNTIME_CAP": "true",
        }
    )
    assert policy.max_estimated_rows == 100
    assert policy.runtime_wall_time_ms == 12.5
    assert policy.seed_batch_rows == 25
    assert policy.max_seed_rows == 100
    assert policy.allow_partial_on_runtime_cap is True
    with pytest.raises(ValueError, match="CDF_SEED_BATCH_ROWS"):
        PlanAdmissionPolicy.from_env({"CDF_SEED_BATCH_ROWS": "0"})
    with pytest.raises(ValueError, match="CDF_MAX_ESTIMATED_COST_USD"):
        PlanAdmissionPolicy.from_env({"CDF_MAX_ESTIMATED_COST_USD": "nan"})

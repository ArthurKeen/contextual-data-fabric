"""P2.2 WP-12 bounded assembled-execution contracts."""

from __future__ import annotations

import json
import time

import pytest

from cdf.query import (
    ArangoAssemblyBackend,
    AssemblyPolicy,
    PlanAdmissionPolicy,
    SourceCatalog,
    SourceResult,
)
from cdf.query.assembly import AssemblyLineage, AssemblyRecord
from cdf.service import FederationService

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"
QUERY = PREFIX + """SELECT ?name ?source WHERE {
  ?a a c:Account ; c:accountId ?k ; c:accountName ?name .
  ?d a c:Document ; c:accountId ?k ; c:source ?source .
}"""


def _csi(kind: str, ref: str, entity: str, properties: list[str], *, stats=True):
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
    if stats:
        document["statistics"] = {
            "version": "1",
            "snapshotId": f"{ref}-snapshot",
            "asOf": "2026-08-05T12:00:00Z",
            "source": {"rowCount": 1, "estimatedBytes": 100},
            "classes": {
                entity: {
                    "rowCount": 1,
                    "estimatedBytes": 100,
                    "properties": {"accountId": {"ndv": 1}},
                }
            },
        }
    return document


def _catalog(*, stats: bool = True) -> SourceCatalog:
    return SourceCatalog.from_csi_documents(
        [
            _csi(
                "postgresql",
                "crm",
                "Account",
                ["accountId", "accountName"],
                stats=stats,
            ),
            _csi(
                "arango",
                "docs",
                "Document",
                ["accountId", "source"],
                stats=stats,
            ),
        ]
    )


class _Executor:
    def __init__(self, rows, *, fail=False, interrupt=False):
        self.rows = tuple(rows)
        self.fail = fail
        self.interrupt = interrupt

    def execute(self, _subquery):
        if self.interrupt:
            raise KeyboardInterrupt
        if self.fail:
            raise RuntimeError("source failed")
        return SourceResult(
            rows=self.rows,
            native_query="SELECT safe FROM source",
            as_of="2026-08-05T12:00:00Z",
        )


class _FakeJob:
    def __init__(self, job_id: str, *, cleanup_fails=False, write_delay=0.0):
        self.job_id = job_id
        self.records = []
        self.cleanup_calls = 0
        self.cleanup_fails = cleanup_fails
        self.write_delay = write_delay

    def write(self, records):
        if self.write_delay:
            time.sleep(self.write_delay)
        self.records.extend(records)

    def cleanup(self):
        self.cleanup_calls += 1
        if self.cleanup_fails:
            raise RuntimeError("cleanup password=hunter2 failed")


class _FakeBackend:
    name = "fake-arango"

    def __init__(self, *, cleanup_fails=False, write_delay=0.0):
        self.jobs = []
        self.cleanup_fails = cleanup_fails
        self.write_delay = write_delay

    def create_job(self, job_id, ttl_seconds):
        job = _FakeJob(
            job_id,
            cleanup_fails=self.cleanup_fails,
            write_delay=self.write_delay,
        )
        self.jobs.append((job, ttl_seconds))
        return job


def _executors(*, extra_rows=False, fail=False, interrupt=False):
    accounts = [{"k": "A", "name": "Acme", "api_token": "do-not-store"}]
    documents = [{"k": "A", "source": "slack"}]
    if extra_rows:
        accounts.append({"k": "B", "name": "Beta"})
        documents.append({"k": "B", "source": "email"})
    return {
        "postgresql:crm": _Executor(accounts, interrupt=interrupt),
        "arango:docs": _Executor(documents, fail=fail),
    }


def _service(
    backend=None,
    *,
    stats=True,
    executors=None,
    assembly_policy=None,
    admission_policy=None,
) -> FederationService:
    return FederationService(
        catalog=_catalog(stats=stats),
        executors=executors or _executors(),
        assembly_backend=backend,
        assembly_policy=assembly_policy or AssemblyPolicy(),
        admission_policy=admission_policy or PlanAdmissionPolicy(),
    )


def test_virtual_default_is_unchanged_and_never_creates_job() -> None:
    backend = _FakeBackend()
    service = _service(backend)

    implicit = service.federate_sparql(QUERY)
    explicit = service.federate_sparql(QUERY, execution_mode="virtual")

    assert implicit.bindings == explicit.bindings == ({"name": "Acme", "source": "slack"},)
    assert implicit.status == explicit.status == "grounded"
    assert implicit.assembly_metrics.mode == "virtual"
    assert backend.jobs == []


def test_assembled_jobs_are_isolated_lineaged_bounded_and_cleaned() -> None:
    backend = _FakeBackend()
    service = _service(backend)

    first = service.federate_sparql(QUERY, execution_mode="assembled")
    second = service.federate_sparql(QUERY, execution_mode="assembled")

    assert first.status == "grounded"
    assert second.status == "grounded"
    assert first.bindings == ({"name": "Acme", "source": "slack"},)
    assert first.assembly_metrics.cleanup_status == "succeeded"
    assert first.assembly_metrics.materialized_rows == 3
    assert first.assembly_metrics.materialized_bytes > 0
    assert first.execution_metrics is not None
    assert first.execution_metrics.assembly_metrics == first.assembly_metrics
    jobs = [item[0] for item in backend.jobs]
    assert jobs[0].job_id != jobs[1].job_id
    assert all(job.cleanup_calls == 1 for job in jobs)

    source_records = [record for record in jobs[0].records if record.kind == "source_row"]
    joined = [record for record in jobs[0].records if record.kind == "joined_intermediate"]
    assert len(source_records) == 2
    assert len(joined) == 1
    assert set(joined[0].input_row_ids) == {record.row_id for record in source_records}
    assert {record.lineage.source_id for record in source_records} == {
        "postgresql:crm",
        "arango:docs",
    }
    assert all(record.lineage.subquery for record in source_records)
    assert all(
        record.lineage.native_query == "SELECT safe FROM source"
        for record in source_records
    )
    account_record = next(
        record for record in source_records if record.lineage.source_id == "postgresql:crm"
    )
    assert account_record.values["api_token"] == "[REDACTED]"
    assert "do-not-store" not in repr(jobs[0].records)
    assert "password" not in repr(jobs[0].records).casefold()


@pytest.mark.parametrize(
    ("service", "code"),
    [
        (_service(None), "assembly_backend_unconfigured"),
        (_service(_FakeBackend(), stats=False), "assembly_estimate_unknown"),
    ],
)
def test_disabled_or_unknown_estimate_refuses_before_job(service, code) -> None:
    envelope = service.federate_sparql(QUERY, execution_mode="assembled")
    assert envelope.status == "refused"
    assert envelope.bindings == ()
    assert envelope.assembly_refusal is not None
    assert envelope.assembly_refusal.code == code
    if service.assembly_backend is not None:
        assert service.assembly_backend.jobs == []


def test_over_budget_preflight_refuses_before_creating_resources() -> None:
    backend = _FakeBackend()
    result = _service(
        backend,
        assembly_policy=AssemblyPolicy(max_rows=2),
    ).federate_sparql(QUERY, execution_mode="assembled")

    assert result.status == "refused"
    assert result.assembly_refusal is not None
    assert result.assembly_refusal.phase == "preflight"
    assert result.assembly_refusal.metric == "estimated_materialized_rows"
    assert backend.jobs == []


def test_from_env_requires_explicit_enable_and_wires_separate_arango_backend(
    tmp_path,
    monkeypatch,
) -> None:
    (tmp_path / "account.json").write_text(
        json.dumps(
            _csi(
                "postgresql",
                "crm",
                "Account",
                ["accountId", "accountName"],
            )
        )
    )
    calls = []

    class Client:
        def __init__(self, *, hosts):
            calls.append(("client", hosts))

        def db(self, name, *, username, password):
            calls.append(("db", name, username, password))
            return object()

    monkeypatch.setattr("arango.ArangoClient", Client)
    base_env = {
        "CDF_CSI_DIR": str(tmp_path),
        "CDF_NL_DISABLED": "true",
        "CDF_ASSEMBLY_ARANGO_URL": "http://assembly.example",
        "CDF_ASSEMBLY_ARANGO_DATABASE": "assembly",
        "CDF_ASSEMBLY_ARANGO_USER": "worker",
        "CDF_ASSEMBLY_ARANGO_PASSWORD": "private",
        "CDF_ASSEMBLY_MAX_ROWS": "25",
        "CDF_ASSEMBLY_MAX_BYTES": "4096",
        "CDF_ASSEMBLY_WALL_TIME_MS": "2500",
        "CDF_ASSEMBLY_TTL_SECONDS": "90",
    }
    disabled = FederationService.from_env(base_env)
    assert disabled.assembly_backend is None
    assert calls == []

    enabled = FederationService.from_env(
        {**base_env, "CDF_ASSEMBLY_ENABLED": "true"}
    )
    assert isinstance(enabled.assembly_backend, ArangoAssemblyBackend)
    assert calls == [
        ("client", "http://assembly.example"),
        ("db", "assembly", "worker", "private"),
    ]
    assert enabled.assembly_policy == AssemblyPolicy(
        max_rows=25,
        max_serialized_bytes=4096,
        wall_time_ms=2500,
        ttl_seconds=90,
    )


def test_runtime_row_and_byte_caps_refuse_without_truncation_and_cleanup() -> None:
    row_backend = _FakeBackend()
    row_service = _service(
        row_backend,
        executors=_executors(extra_rows=True),
        assembly_policy=AssemblyPolicy(max_rows=3, max_serialized_bytes=10_000),
    )
    row_result = row_service.federate_sparql(QUERY, execution_mode="assembled")
    assert row_result.status == "refused"
    assert row_result.bindings == ()
    assert row_result.assembly_refusal is not None
    assert row_result.assembly_refusal.metric == "assembly_materialized_rows"
    assert row_backend.jobs[0][0].cleanup_calls == 1

    byte_backend = _FakeBackend()
    byte_service = _service(
        byte_backend,
        executors={
            **_executors(),
            "postgresql:crm": _Executor([{"k": "A", "name": "x" * 2000}]),
        },
        assembly_policy=AssemblyPolicy(max_rows=10, max_serialized_bytes=500),
    )
    byte_result = byte_service.federate_sparql(QUERY, execution_mode="assembled")
    assert byte_result.status == "refused"
    assert byte_result.bindings == ()
    assert byte_result.assembly_refusal is not None
    assert byte_result.assembly_refusal.metric == "assembly_serialized_bytes"
    assert byte_backend.jobs[0][0].cleanup_calls == 1


def test_wall_source_admission_and_cancellation_outcomes_all_cleanup() -> None:
    slow_backend = _FakeBackend(write_delay=0.01)
    slow = _service(
        slow_backend,
        assembly_policy=AssemblyPolicy(wall_time_ms=1),
    ).federate_sparql(QUERY, execution_mode="assembled")
    assert slow.status == "refused"
    assert slow.assembly_refusal is not None
    assert slow.assembly_refusal.metric == "assembly_wall_time_ms"
    assert slow_backend.jobs[0][0].cleanup_calls == 1

    failed_backend = _FakeBackend()
    failed = _service(
        failed_backend,
        executors=_executors(fail=True),
    ).federate_sparql(QUERY, execution_mode="assembled")
    assert failed.status == "refused"
    assert failed_backend.jobs[0][0].cleanup_calls == 1

    admission_backend = _FakeBackend()
    admission = _service(
        admission_backend,
        admission_policy=PlanAdmissionPolicy(max_estimated_rows=0),
    ).federate_sparql(QUERY, execution_mode="assembled")
    assert admission.status == "refused"
    assert admission.admission_refusal is not None
    assert admission_backend.jobs[0][0].cleanup_calls == 1

    interrupted_backend = _FakeBackend()
    with pytest.raises(KeyboardInterrupt):
        _service(
            interrupted_backend,
            executors=_executors(interrupt=True),
        ).federate_sparql(QUERY, execution_mode="assembled")
    assert interrupted_backend.jobs[0][0].cleanup_calls == 1


def test_cleanup_failure_is_a_redacted_structured_refusal() -> None:
    backend = _FakeBackend(cleanup_fails=True)
    result = _service(backend).federate_sparql(QUERY, execution_mode="assembled")
    assert result.status == "refused"
    assert result.bindings == ()
    assert result.assembly_refusal is not None
    assert result.assembly_refusal.code == "assembly_cleanup_failed"
    assert result.assembly_metrics.cleanup_status == "failed"
    assert "hunter2" not in repr(result)


class _StrictCollection:
    def __init__(self, name):
        self.name = name
        self.ttl_calls = []
        self.inserted = []

    def add_ttl_index(self, *, fields, expiry_time, name):
        self.ttl_calls.append((fields, expiry_time, name))

    def insert_many(self, documents):
        self.inserted.extend(documents)


class _StrictArangoDB:
    def __init__(self):
        self.collections = {}
        self.graphs = []
        self.deleted_graphs = []
        self.deleted_collections = []

    def create_collection(self, name, edge=False):
        assert name not in self.collections
        collection = _StrictCollection(name)
        self.collections[name] = collection
        return collection

    def create_graph(self, name, *, edge_definitions):
        self.graphs.append((name, edge_definitions))

    def collection(self, name):
        return self.collections[name]

    def delete_graph(self, name, *, drop_collections, ignore_missing):
        self.deleted_graphs.append((name, drop_collections, ignore_missing))

    def delete_collection(self, name, *, ignore_missing):
        self.deleted_collections.append((name, ignore_missing))


def test_arango_backend_creates_ttl_graph_writes_edges_and_drops_everything() -> None:
    db = _StrictArangoDB()
    job = ArangoAssemblyBackend(db).create_job("a1b2c3", 60)
    records = [
        AssemblyRecord(
            row_id="r_1",
            kind="source_row",
            values={"k": "A"},
            lineage=AssemblyLineage(stage="source", source_id="postgresql:crm"),
            input_row_ids=(),
            expires_at=1234.0,
        ),
        AssemblyRecord(
            row_id="r_2",
            kind="joined_intermediate",
            values={"k": "A"},
            lineage=AssemblyLineage(stage="deterministic_join"),
            input_row_ids=("r_1",),
            expires_at=1234.0,
        ),
    ]
    job.write(records)
    job.cleanup()

    assert len(db.graphs) == 1
    assert all(collection.ttl_calls for collection in db.collections.values())
    vertices = db.collections[job.vertex_collection].inserted
    edges = db.collections[job.edge_collection].inserted
    assert [vertex["_key"] for vertex in vertices] == ["r_1", "r_2"]
    assert edges == [
        {
            "_key": "r_2_0",
            "_from": f"{job.vertex_collection}/r_2",
            "_to": f"{job.vertex_collection}/r_1",
            "job_id": "a1b2c3",
            "relation": "derived_from",
            "expires_at": 1234.0,
        }
    ]
    assert db.deleted_graphs == [(job.graph_name, False, True)]
    assert {name for name, _ignore in db.deleted_collections} == {
        job.vertex_collection,
        job.edge_collection,
    }

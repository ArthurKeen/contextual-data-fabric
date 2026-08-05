"""P3 WP-14 runtime entity-resolution integration tests."""

from __future__ import annotations

import json
import time
from dataclasses import asdict

from cdf.catalog import RuntimeResolution, parse_manifest
from cdf.query import (
    PlanAdmissionPolicy,
    SourceCatalog,
    SourceResult,
    execute_plan,
    ground,
    partition_query,
)
from cdf.query.assembly import AssemblyExecution, AssemblyPolicy
from cdf.resolution import (
    FieldEvidence,
    GuardedResolver,
    ResolutionPolicy,
    ResolveEvidence,
    ResolveResult,
)
from cdf.service import FederationService

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"


def _csi(kind, ref, entity, properties):
    return {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {
                    "name": entity,
                    "properties": [{"name": name} for name in properties],
                }
            ],
            "relationships": [],
        },
        "provenance": {
            "producer": "test",
            "direction": "forward",
            "source": {"kind": kind, "ref": ref},
        },
    }


def _binding(*, join="k", scope="scope", observable="name_var"):
    return RuntimeResolution(
        mode="canonical_hub",
        join_variable=join,
        canonical_key_regex=r"^canonical/[a-z]+$",
        canonical_key_prefix="canonical/",
        scope_binding_variable=scope,
        observable_bindings={"name": observable},
        policy_profile="fabric_canonical_hub",
        resolver="test",
    )


def _result(request, canonical="canonical/acme", *, status="resolved", reason="matched"):
    return ResolveResult(
        status=status,
        canonical_id=canonical if status == "resolved" else None,
        reason=reason,
        score=0.98 if status == "resolved" else None,
        margin=0.5 if status == "resolved" else None,
        evidence=(
            ResolveEvidence(
                profile="fabric_canonical_hub",
                candidate_count=1,
                field_scores=(FieldEvidence("name", 0.97, 1.0),),
                vector_score=0.98,
            )
            if status == "resolved"
            else None
        ),
        candidate_account_scope=request.account_scope,
        deadline_at=request.deadline_at,
        elapsed_ms=0.2,
    )


class _Resolver:
    def __init__(self, choose=None):
        self.requests = []
        self.choose = choose or (lambda request: _result(request))

    def resolve(self, request):
        self.requests.append(request)
        return self.choose(request)


class _Executor:
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.sparql = []

    def execute(self, subquery):
        self.sparql.append(subquery.sparql)
        return SourceResult(rows=self.rows, native_query="SELECT safe")


def test_canonical_bypass_makes_zero_calls_and_preserves_answer():
    catalog = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "crm", "Account", ["accountId", "scope", "name"])]
    )
    query = (
        PREFIX
        + "SELECT ?k ?name_var WHERE { ?a a c:Account ; c:accountId ?k ; "
        "c:scope ?scope ; c:name ?name_var }"
    )
    plan = partition_query(query, catalog)
    resolver = _Resolver()
    result = execute_plan(
        plan,
        {
            "postgresql:crm": _Executor(
                [{"k": "canonical/acme", "scope": "tenant-a", "name_var": "Acme"}]
            )
        },
        entity_resolver=resolver,
        resolution_bindings={"postgresql:crm": _binding()},
    )

    assert resolver.requests == []
    assert result.bindings == ({"k": "canonical/acme", "name_var": "Acme"},)
    assert result.resolution_metrics.bypasses == 1
    assert result.retrieval_path[0].row_count == 1


def test_missing_join_values_resolve_then_join_and_seed_canonical_once():
    catalog = SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "crm", "Account", ["accountId", "scope", "name"]),
            _csi("arango", "signals", "Signal", ["accountId", "scope", "alias"]),
            _csi("arango", "notes", "Note", ["accountId", "text"]),
        ]
    )
    query = PREFIX + """SELECT ?name_var ?signal ?text WHERE {
      ?a a c:Account ; c:accountId ?k ; c:scope ?scope_a ; c:name ?name_var .
      ?s a c:Signal ; c:accountId ?k ; c:scope ?scope_s ; c:alias ?signal .
      ?n a c:Note ; c:accountId ?k ; c:text ?text .
    }"""
    plan = partition_query(query, catalog)
    resolver = _Resolver()
    note = _Executor([{"k": "canonical/acme", "text": "healthy"}])
    result = execute_plan(
        plan,
        {
            "postgresql:crm": _Executor(
                [{"scope_a": "tenant-a", "name_var": "Acme"}]
            ),
            "arango:signals": _Executor(
                [{"scope_s": "tenant-a", "signal": "Acme"}]
            ),
            "arango:notes": note,
        },
        entity_resolver=resolver,
        resolution_bindings={
            "postgresql:crm": _binding(scope="scope_a"),
            "arango:signals": _binding(scope="scope_s", observable="signal"),
        },
    )

    assert result.bindings == (
        {"name_var": "Acme", "signal": "Acme", "text": "healthy"},
    )
    assert len(resolver.requests) == 1
    assert resolver.requests[0].attributes == {"name": "Acme"}
    assert "k" not in resolver.requests[0].attributes
    assert "VALUES (?k) { (\"canonical/acme\") }" in note.sparql[0]
    assert result.resolution_metrics.cache_hits == 1
    assert "Acme" not in repr(result.resolution_events)


def test_blank_join_value_resolves_without_becoming_an_observable():
    catalog = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "crm", "Account", ["accountId", "scope", "name"])]
    )
    plan = partition_query(
        PREFIX
        + "SELECT ?k WHERE { ?a a c:Account ; c:accountId ?k ; "
        "c:scope ?scope ; c:name ?name_var }",
        catalog,
    )
    resolver = _Resolver()
    result = execute_plan(
        plan,
        {
            "postgresql:crm": _Executor(
                [{"k": "   ", "scope": "tenant-a", "name_var": "Acme"}]
            )
        },
        entity_resolver=resolver,
        resolution_bindings={"postgresql:crm": _binding()},
    )

    assert result.bindings == ({"k": "canonical/acme"},)
    assert resolver.requests[0].attributes == {"name": "Acme"}
    assert "   " not in repr(resolver.requests)


def test_missing_join_without_observables_uses_guarded_abstention():
    catalog = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "crm", "Account", ["accountId", "scope", "name"])]
    )
    plan = partition_query(
        PREFIX
        + "SELECT ?k WHERE { ?a a c:Account ; c:accountId ?k ; "
        "c:scope ?scope ; c:name ?name_var }",
        catalog,
    )

    class _UnusedBackend:
        calls = 0

        def resolve(self, _request):
            self.calls += 1
            raise AssertionError("guard must abstain before backend invocation")

    backend = _UnusedBackend()
    guarded = GuardedResolver(
        backend,
        ResolutionPolicy(observable_fields=("name",)),
    )
    result = execute_plan(
        plan,
        {"postgresql:crm": _Executor([{"scope": "tenant-a"}])},
        entity_resolver=guarded,
        resolution_bindings={"postgresql:crm": _binding()},
    )

    assert backend.calls == 0
    assert result.resolution_shortfalls[0].reason == "no_observable_fields"
    assert result.bindings == ()


def test_strict_shortfall_refuses_but_safe_partial_drops_native_key():
    catalog = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "crm", "Account", ["accountId", "scope", "name"])]
    )
    plan = partition_query(
        PREFIX
        + "SELECT ?k ?name_var WHERE { ?a a c:Account ; c:accountId ?k ; "
        "c:scope ?scope ; c:name ?name_var }",
        catalog,
    )

    def choose(request):
        if request.attributes["name"] == "Unknown":
            return _result(request, status="abstained", reason="below_threshold")
        return _result(request)

    result = execute_plan(
        plan,
        {
            "postgresql:crm": _Executor(
                [
                    {"k": "native-good", "scope": "tenant-a", "name_var": "Acme"},
                    {"k": "native-bad", "scope": "tenant-a", "name_var": "Unknown"},
                ]
            )
        },
        entity_resolver=_Resolver(choose),
        resolution_bindings={"postgresql:crm": _binding()},
    )
    strict = ground(result)
    partial = ground(result, allow_partial=True)

    assert strict.status == "refused"
    assert strict.bindings == ()
    assert partial.status == "partial"
    assert partial.bindings == ({"k": "canonical/acme", "name_var": "Acme"},)
    assert partial.resolution_shortfalls[0].reason == "below_threshold"
    assert "native-bad" not in repr(partial)
    serialized_events = json.loads(json.dumps(asdict(partial)))["resolution_events"]
    resolved_event = next(item for item in serialized_events if item["status"] == "resolved")
    assert resolved_event["evidence"]["field_scores"][0]["field"] == "name"
    assert resolved_event["score"] == 0.98
    assert resolved_event["margin"] == 0.5
    assert "Acme" not in json.dumps(serialized_events)


def test_cross_scope_refusal_can_never_be_partial():
    catalog = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "crm", "Account", ["accountId", "scope", "name"])]
    )
    plan = partition_query(
        PREFIX
        + "SELECT ?k WHERE { ?a a c:Account ; c:accountId ?k ; "
        "c:scope ?scope ; c:name ?name_var }",
        catalog,
    )

    def refuse(request):
        return _result(
            request,
            status="refused",
            reason="cross_account_candidate",
        )

    result = execute_plan(
        plan,
        {
            "postgresql:crm": _Executor(
                [{"k": "native", "scope": "tenant-a", "name_var": "Acme"}]
            )
        },
        entity_resolver=_Resolver(refuse),
        resolution_bindings={"postgresql:crm": _binding()},
    )
    envelope = ground(result, allow_partial=True)

    assert envelope.status == "refused"
    assert envelope.bindings == ()
    assert envelope.resolution_refusal is not None
    assert envelope.resolution_metrics.cross_scope == 1


def test_service_wires_catalog_binding_and_injected_resolver():
    catalog = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "crm", "Account", ["accountId", "scope", "name"])]
    )
    catalog._runtime_resolution["postgresql:crm"] = _binding()
    resolver = _Resolver()
    service = FederationService(
        catalog=catalog,
        executors={
            "postgresql:crm": _Executor(
                [{"k": "native", "scope": "tenant-a", "name_var": "Acme"}]
            )
        },
        entity_resolver=resolver,
    )
    query = (
        PREFIX
        + "SELECT ?k WHERE { ?a a c:Account ; c:accountId ?k ; "
        "c:scope ?scope ; c:name ?name_var }"
    )

    envelope = service.federate_sparql(query)

    assert envelope.status == "grounded"
    assert envelope.bindings == ({"k": "canonical/acme"},)
    assert envelope.citations[0].resolution_events[0].profile == (
        "fabric_canonical_hub"
    )
    assert envelope.execution_metrics is not None
    assert envelope.execution_metrics.resolution_metrics.calls == 1


def test_resolution_call_cap_and_deadline_are_explicit():
    catalog = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "crm", "Account", ["accountId", "scope", "name"])]
    )
    plan = partition_query(
        PREFIX
        + "SELECT ?k WHERE { ?a a c:Account ; c:accountId ?k ; "
        "c:scope ?scope ; c:name ?name_var }",
        catalog,
    )
    rows = [
        {"k": "n1", "scope": "tenant-a", "name_var": "One"},
        {"k": "n2", "scope": "tenant-a", "name_var": "Two"},
    ]
    capped_resolver = _Resolver()
    capped = execute_plan(
        plan,
        {"postgresql:crm": _Executor(rows)},
        entity_resolver=capped_resolver,
        resolution_bindings={"postgresql:crm": _binding()},
        admission_policy=PlanAdmissionPolicy(
            max_resolution_calls=1,
            resolution_batch_size=1,
        ),
    )
    assert capped.resolution_refusal is not None
    assert capped.resolution_refusal.code == "max_resolution_calls_exceeded"
    assert capped_resolver.requests == []

    def slow(request):
        time.sleep(0.01)
        return _result(request)

    deadline = execute_plan(
        plan,
        {"postgresql:crm": _Executor(rows[:1])},
        entity_resolver=_Resolver(slow),
        resolution_bindings={"postgresql:crm": _binding()},
        admission_policy=PlanAdmissionPolicy(
            resolution_deadline_ms=1,
            resolution_batch_size=1,
        ),
    )
    assert deadline.resolution_shortfalls[0].reason == "deadline_exceeded"
    assert ground(deadline).status == "refused"


class _Job:
    job_id = "job"

    def __init__(self):
        self.records = []

    def write(self, records):
        self.records.extend(records)

    def cleanup(self):
        return None


def test_assembly_lineage_stores_value_free_event_summaries():
    request = type(
        "Request",
        (),
        {"account_scope": "tenant-a", "deadline_at": time.monotonic() + 1},
    )()
    event_result = _result(request)
    from cdf.resolution import ResolutionEvent

    event = ResolutionEvent(
        source_id="postgresql:crm",
        status="resolved",
        reason="matched",
        resolver="test",
        profile="fabric_canonical_hub",
        canonical_id="canonical/acme",
        score=event_result.score,
        margin=event_result.margin,
        evidence=event_result.evidence,
    )
    job = _Job()
    assembly = AssemblyExecution(
        job,
        backend_name="fake",
        policy=AssemblyPolicy(max_rows=10, max_serialized_bytes=10_000),
    )
    assembly.materialize_source(
        [{"k": "canonical/acme", "name": "Acme"}],
        source_id="postgresql:crm",
        subquery="SELECT",
        native_query="SQL",
        as_of=None,
        resolution_events=(event,),
    )

    lineage = asdict(job.records[0].lineage)
    assert lineage["resolution_events"][0]["reason"] == "matched"
    serialized = json.dumps(lineage)
    assert "canonical/acme" not in serialized
    assert "Acme" not in serialized


def test_manifest_resolution_binding_validation_is_strict():
    base = {
        "catalogManifestVersion": "1",
        "generation": "g",
        "contentHash": "0" * 64,
        "conceptBase": "urn:arango-sparql:concept#",
        "sources": [
            {
                "sourceId": "arango:test",
                "kind": "arango",
                "ref": "test",
                "concepts": ["Thing"],
                "csi": {
                    "path": "csi.json",
                    "sha256": "0" * 64,
                    "generation": "g",
                    "producer": "test",
                    "direction": "reverse",
                },
                "r2rml": None,
                "statisticsSnapshot": None,
                "joinKeys": [],
                "entitlements": {
                    "classification": "internal",
                    "allowedRoles": [],
                    "mask": "none",
                },
                "runtimeResolution": {
                    "mode": "canonical_hub",
                    "joinVariable": "k",
                    "canonicalKeyRegex": r"^canonical/[a-z]+$",
                    "canonicalKeyPrefix": "canonical/",
                    "scopeBindingVariable": "scope",
                    "observableBindings": {"name": "name_var"},
                    "policyProfile": "fabric_canonical_hub",
                    "resolver": "test",
                },
                "auth": {"mode": "service", "delegation": "none"},
            }
        ],
    }
    assert parse_manifest(base).sources[0].runtime_resolution.join_variable == "k"

    invalid = json.loads(json.dumps(base))
    invalid["sources"][0]["runtimeResolution"]["observableBindings"] = {
        "name": "same",
        "country": "same",
    }
    try:
        parse_manifest(invalid)
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate observable binding was accepted")

    oracle = json.loads(json.dumps(base))
    oracle["sources"][0]["runtimeResolution"]["observableBindings"] = {
        "canonical_id": "name_var"
    }
    try:
        parse_manifest(oracle)
    except ValueError as exc:
        assert "oracle" in str(exc)
    else:
        raise AssertionError("oracle field was accepted")

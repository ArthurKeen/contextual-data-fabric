"""Request-context propagation and fail-closed source delegation."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from cdf.auth import AuthenticatedPrincipal, RequestContext
from cdf.connectors import BaseSourceIdentity, SourceIdentity
from cdf.query import SourceCatalog, SourceResult, execute_plan, partition_query

PREFIX = "PREFIX c: <urn:arango-sparql:concept#> "


def _csi(ref: str, entity: str, prop: str):
    return {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [{"name": entity, "properties": [{"name": prop}]}]
        },
        "physicalMapping": {"entities": {entity: {"tableName": entity.casefold()}}},
        "provenance": {
            "producer": "test",
            "direction": "forward",
            "source": {"kind": "postgresql", "ref": ref},
        },
    }


def _context() -> RequestContext:
    return RequestContext(
        principal=AuthenticatedPrincipal(
            issuer="https://idp.example",
            subject="asker-1",
            tenant="tenant-a",
        ),
        request_id="req-concurrent",
        trace_id="trace-concurrent",
        purpose="support",
        deadline=datetime.now(timezone.utc) + timedelta(minutes=1),
    )


class _ContextExecutor:
    def __init__(self, rows, barrier=None):
        self.rows = tuple(rows)
        self.barrier = barrier
        self.contexts = []

    def execute_with_context(self, _subquery, context):
        self.contexts.append(context)
        if self.barrier is not None:
            self.barrier.wait(timeout=2)
        return SourceResult(rows=self.rows)


def test_same_immutable_context_reaches_concurrent_legs() -> None:
    catalog = SourceCatalog.from_csi_documents(
        [_csi("one", "Account", "name"), _csi("two", "Ticket", "subject")]
    )
    query = (
        PREFIX
        + "SELECT ?name ?subject WHERE { "
        "?a a c:Account ; c:name ?name . "
        "?t a c:Ticket ; c:subject ?subject . }"
    )
    plan = partition_query(query, catalog)
    barrier = threading.Barrier(2)
    one = _ContextExecutor([{"name": "Acme"}], barrier)
    two = _ContextExecutor([{"subject": "Issue"}], barrier)
    context = _context()

    result = execute_plan(
        plan,
        {"postgresql:one": one, "postgresql:two": two},
        request_context=context,
    )

    assert one.contexts[0].request is context
    assert two.contexts[0].request is context
    assert one.contexts[0].auth_mode == "service"
    assert result.request_metadata == context.safe_metadata()
    assert result.execution_metrics.request_metadata == context.safe_metadata()


class _LegacyExecutor:
    def __init__(self):
        self.called = False

    def execute(self, _subquery):
        self.called = True
        return SourceResult(rows=({"name": "must-not-run"},))


class _Broker:
    def __init__(self):
        self.calls = []

    def exchange(self, principal, source_id, base_identity, *, deadline):
        self.calls.append((principal, source_id, base_identity, deadline))
        return SourceIdentity(
            source_id=source_id,
            subject=principal.subject,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            scheme="Bearer",
            token="downstream-secret",
        )


def _single_source_plan():
    catalog = SourceCatalog.from_csi_documents([_csi("one", "Account", "name")])
    return partition_query(
        PREFIX + "SELECT ?name WHERE { ?a a c:Account ; c:name ?name }",
        catalog,
    )


def test_delegated_mode_without_broker_fails_closed_without_service_fallback() -> None:
    executor = _LegacyExecutor()
    result = execute_plan(
        _single_source_plan(),
        {"postgresql:one": executor},
        request_context=_context(),
        source_auth_modes={"postgresql:one": "delegated"},
        source_base_identities={
            "postgresql:one": BaseSourceIdentity("postgresql:one", "cdf-reader")
        },
    )

    assert executor.called is False
    assert result.failed_sources == ("postgresql:one",)
    assert "delegation broker" in result.retrieval_path[0].error


def test_delegated_mode_without_adapter_support_never_exchanges_or_falls_back() -> None:
    executor = _LegacyExecutor()
    broker = _Broker()
    result = execute_plan(
        _single_source_plan(),
        {"postgresql:one": executor},
        request_context=_context(),
        source_auth_modes={"postgresql:one": "delegated"},
        delegation_broker=broker,
        source_base_identities={
            "postgresql:one": BaseSourceIdentity("postgresql:one", "cdf-reader")
        },
    )

    assert executor.called is False
    assert broker.calls == []
    assert "does not support delegated identity" in result.retrieval_path[0].error


def test_delegated_mode_exchanges_and_passes_secret_safe_source_context() -> None:
    executor = _ContextExecutor([{"name": "Acme"}])
    broker = _Broker()
    context = _context()
    result = execute_plan(
        _single_source_plan(),
        {"postgresql:one": executor},
        request_context=context,
        source_auth_modes={"postgresql:one": "delegated"},
        delegation_broker=broker,
        source_base_identities={
            "postgresql:one": BaseSourceIdentity("postgresql:one", "cdf-reader")
        },
    )

    assert result.failed_sources == ()
    source_context = executor.contexts[0]
    assert source_context.request is context
    assert source_context.auth_mode == "delegated"
    assert source_context.identity.material.reveal() == "downstream-secret"
    assert "downstream-secret" not in repr(source_context)

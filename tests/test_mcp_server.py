"""Tests for the optional semantic MCP surface (M5 / F2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

mcp = pytest.importorskip("mcp", reason="install the optional mcp extra")
from mcp import Client  # noqa: E402
from mcp.server.auth.provider import AccessToken  # noqa: E402

from cdf.auth import AuthenticatedPrincipal, RequestContext  # noqa: E402
from cdf.governance import PlanAuthorization, ResourceDecision  # noqa: E402
from cdf.mcp_server import (  # noqa: E402
    create_mcp_server,
    request_context_from_access_token,
)
from cdf.query import SourceCatalog, SourceResult  # noqa: E402
from cdf.service import FederationService  # noqa: E402

_CSI = {
    "csiVersion": "1",
    "conceptualModel": {
        "entities": [{"name": "Account", "properties": [{"name": "name"}]}]
    },
    "physicalMapping": {"entities": {"Account": {"tableName": "accounts"}}},
    "provenance": {
        "producer": "r2g",
        "direction": "forward",
        "source": {"kind": "postgresql", "ref": "crm"},
    },
}
_SPARQL = (
    "PREFIX c: <urn:arango-sparql:concept#> "
    "SELECT ?name WHERE { ?account a c:Account ; c:name ?name }"
)


class _Executor:
    def execute(self, _subquery):
        return SourceResult(
            rows=({"name": "Acme"},),
            native_query="SELECT name FROM accounts",
            source_objects=("accounts",),
            as_of="2026-08-05T00:00:00Z",
        )


class _DenyPDP:
    def authorize(self, resources, _context, *, catalog_generation):
        decisions = tuple(
            ResourceDecision(
                source_id=item.source_id,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                usage=item.usage,
                action="deny",
                disclose_source=False,
                reason="withheld",
                variable=item.variable,
                optional=item.optional,
            )
            for item in resources
        )
        return PlanAuthorization(
            decision_id="deny",
            catalog_generation=catalog_generation,
            context_fingerprint="deny",
            decisions=decisions,
        )


class _NeverCalledLLM:
    provider = "test"
    model = "never"

    def __init__(self):
        self.calls = 0

    def generate(self, _messages):
        self.calls += 1
        raise AssertionError("LLM must not receive hidden-only catalog context")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _service() -> FederationService:
    return FederationService(
        catalog=SourceCatalog.from_csi_documents([_CSI]),
        executors={"postgresql:crm": _Executor()},
        prepared_questions={"which accounts exist?": _SPARQL},
    )


@pytest.mark.anyio
async def test_mcp_exposes_only_semantic_tools_and_uses_injected_service() -> None:
    calls = 0

    def factory() -> FederationService:
        nonlocal calls
        calls += 1
        return _service()

    server = create_mcp_server(factory)
    assert calls == 1

    async with Client(server, raise_exceptions=True) as client:
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == {
            "federate",
            "list_sources",
            "list_concepts",
            "nl_preview",
        }

        result = await client.call_tool(
            "federate", {"question": "Which accounts exist?", "allow_partial": False}
        )

    assert not result.is_error
    assert result.structured_content["status"] == "grounded"
    assert result.structured_content["bindings"] == [{"name": "Acme"}]
    assert result.structured_content["citations"][0]["source_id"] == "postgresql:crm"


@pytest.mark.anyio
async def test_mcp_introspection_is_json_safe_and_contains_no_credentials() -> None:
    server = create_mcp_server(_service)

    async with Client(server, raise_exceptions=True) as client:
        sources = await client.call_tool("list_sources", {})
        concepts = await client.call_tool("list_concepts", {})
        preview = await client.call_tool(
            "nl_preview", {"question": "Which accounts exist?"}
        )

    assert sources.structured_content == {
        "result": [
            {
                "source_id": "postgresql:crm",
                "kind": "postgresql",
                "ref": "crm",
                "available": True,
            }
        ]
    }
    concept_result = concepts.structured_content["result"]
    assert concept_result[0]["classes"] == [{"name": "Account", "properties": ["name"]}]
    assert preview.structured_content["ok"] is True
    assert preview.structured_content["sparql"] == _SPARQL
    assert "password" not in repr(
        (sources.structured_content, concepts.structured_content, preview.structured_content)
    ).casefold()


@pytest.mark.anyio
async def test_mcp_federate_exposes_same_execution_mode_contract() -> None:
    server = create_mcp_server(_service)

    async with Client(server, raise_exceptions=True) as client:
        virtual = await client.call_tool(
            "federate",
            {"question": "Which accounts exist?", "execution_mode": "virtual"},
        )
        assembled = await client.call_tool(
            "federate",
            {"question": "Which accounts exist?", "execution_mode": "assembled"},
        )

    assert virtual.structured_content["status"] == "grounded"
    assert virtual.structured_content["assembly_metrics"]["mode"] == "virtual"
    assert assembled.structured_content["status"] == "refused"
    assert (
        assembled.structured_content["assembly_refusal"]["code"]
        == "assembly_backend_unconfigured"
    )


@pytest.mark.anyio
async def test_mcp_federate_redacts_source_dsn_errors() -> None:
    class FailingExecutor:
        def execute(self, _subquery):
            raise RuntimeError(
                "connection failed: postgresql://reader:do-not-return@db.internal/crm"
            )

    def service() -> FederationService:
        return FederationService(
            catalog=SourceCatalog.from_csi_documents([_CSI]),
            executors={"postgresql:crm": FailingExecutor()},
            prepared_questions={"which accounts exist?": _SPARQL},
        )

    async with Client(create_mcp_server(service), raise_exceptions=True) as client:
        result = await client.call_tool("federate", {"question": "Which accounts exist?"})

    assert not result.is_error
    assert "do-not-return" not in repr(result.structured_content)
    assert "[REDACTED]" in repr(result.structured_content)


def test_mcp_access_token_maps_to_secret_free_request_context() -> None:
    token = AccessToken(
        token="opaque-subject-token",
        client_id="agent-client",
        scopes=["fabric.query"],
        subject="user-1",
        claims={
            "iss": "https://idp.example",
            "tenant": "tenant-a",
            "groups": ["csm"],
            "roles": ["analyst"],
        },
    )

    context = request_context_from_access_token(token)

    assert context.principal.subject == "user-1"
    assert context.principal.client_id == "agent-client"
    assert context.principal.tenant == "tenant-a"
    assert context.principal.groups == ("csm",)
    assert context.principal.roles == ("analyst",)
    assert "opaque-subject-token" not in repr(context)


@pytest.mark.anyio
async def test_mcp_required_auth_refuses_when_context_has_no_subject() -> None:
    server = create_mcp_server(
        _service,
        auth_required=True,
        context_factory=lambda: None,
    )

    async with Client(server, raise_exceptions=False) as client:
        result = await client.call_tool(
            "federate",
            {"question": "Which accounts exist?"},
        )

    assert result.is_error
    assert "authorization required" in repr(result).casefold()


@pytest.mark.anyio
async def test_mcp_injected_context_reaches_federation_service() -> None:
    context = RequestContext(
        principal=AuthenticatedPrincipal(
            issuer="https://idp.example",
            subject="user-1",
            authentication_method="mcp",
        ),
        request_id="mcp-request",
        trace_id="mcp-trace",
        purpose=None,
        deadline=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    server = create_mcp_server(
        _service,
        auth_required=True,
        context_factory=lambda: context,
    )

    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "federate",
            {"question": "Which accounts exist?"},
        )

    assert result.structured_content["request_metadata"]["request_id"] == "mcp-request"
    assert (
        result.structured_content["request_metadata"]["principal_key"]
        == "https://idp.example|user-1"
    )


@pytest.mark.anyio
async def test_mcp_preview_and_federate_refuse_before_hidden_only_llm_prompt() -> None:
    llm = _NeverCalledLLM()

    def service() -> FederationService:
        return FederationService(
            catalog=SourceCatalog.from_csi_documents([_CSI]),
            executors={"postgresql:crm": _Executor()},
            nl_client=llm,
            policy_pdp=_DenyPDP(),
        )

    async with Client(create_mcp_server(service), raise_exceptions=True) as client:
        preview = await client.call_tool(
            "nl_preview",
            {"question": "Unregistered account question"},
        )
        federated = await client.call_tool(
            "federate",
            {"question": "Unregistered account question"},
        )

    assert preview.structured_content["ok"] is False
    assert "no authorized catalog vocabulary" in preview.structured_content["error"]
    assert federated.structured_content["status"] == "refused"
    assert llm.calls == 0

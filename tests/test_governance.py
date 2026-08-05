"""P3 policy preflight, execution, postflight, and PDP contracts."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cdf.auth import AuthenticatedPrincipal, RequestContext
from cdf.catalog import parse_manifest
from cdf.connectors import SecretMaterial
from cdf.eval.nl_corpus import CorpusExample
from cdf.governance import (
    CatalogPolicyPDP,
    HttpOpenFGATransport,
    NonePolicyPDP,
    OpenFGAConfig,
    OpenFGAPolicyPDP,
    ResourceRequest,
    SecretMaskingKey,
    StaticMaskingKeyResolver,
    policy_pdp_from_env,
)
from cdf.query import SourceCatalog, SourceResult
from cdf.service import FederationService
from cdf.service.app import create_app

PREFIX = "PREFIX c: <urn:arango-sparql:concept#> "
QUERY = (
    PREFIX
    + "SELECT ?name ?tenantId WHERE { "
    "?a a c:Account ; c:name ?name ; c:tenantId ?tenantId . }"
)


def _context(
    *,
    roles: tuple[str, ...] = (),
    groups: tuple[str, ...] = (),
    scopes: tuple[str, ...] = (),
    purpose: str | None = "support",
    tenant: str = "tenant-a",
) -> RequestContext:
    return RequestContext(
        principal=AuthenticatedPrincipal(
            issuer="https://idp.example",
            subject="user-1",
            roles=roles,
            groups=groups,
            scopes=scopes,
            tenant=tenant,
            claims=(("region", "us-east"),),
        ),
        request_id="policy-request",
        trace_id="policy-trace",
        purpose=purpose,
        deadline=datetime.now(timezone.utc) + timedelta(minutes=1),
    )


def _catalog(entitlements: dict) -> SourceCatalog:
    csi = {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {
                    "name": "Account",
                    "properties": [
                        {"name": "name"},
                        {"name": "email"},
                        {"name": "tenantId"},
                        {"name": "privateNote"},
                    ],
                }
            ],
            "relationships": [],
        },
        "provenance": {
            "producer": "test",
            "direction": "reverse",
            "source": {"kind": "arango", "ref": "crm"},
        },
    }
    catalog = SourceCatalog.from_csi_documents([csi])
    manifest = parse_manifest(
        {
            "catalogManifestVersion": "1",
            "generation": "policy-v1",
            "contentHash": "0" * 64,
            "conceptBase": "urn:arango-sparql:concept#",
            "sources": [
                {
                    "sourceId": "arango:crm",
                    "kind": "arango",
                    "ref": "crm",
                    "concepts": ["Account"],
                    "csi": {
                        "path": "csi.json",
                        "sha256": "0" * 64,
                        "generation": "v1",
                        "producer": "test",
                        "direction": "reverse",
                    },
                    "r2rml": None,
                    "statisticsSnapshot": None,
                    "joinKeys": [],
                    "entitlements": entitlements,
                    "runtimeResolution": {"mode": "none"},
                    "auth": {"mode": "service", "delegation": "none"},
                }
            ],
        }
    )
    catalog.apply_manifest(manifest)
    return catalog


def _policy(**overrides):
    return {
        "classification": "internal",
        "allowedRoles": [],
        "mask": "none",
        "policyIds": ["policy:account-read"],
        **overrides,
    }


def _nl_policy_catalog() -> SourceCatalog:
    documents = [
        {
            "csiVersion": "1",
            "conceptualModel": {
                "entities": [
                    {
                        "name": "Account",
                        "properties": [
                            {"name": "name"},
                            {"name": "privateNote"},
                        ],
                    }
                ],
                "relationships": [],
            },
            "provenance": {
                "producer": "test",
                "direction": "reverse",
                "source": {"kind": "arango", "ref": "crm"},
            },
        },
        {
            "csiVersion": "1",
            "conceptualModel": {
                "entities": [
                    {
                        "name": "SecretRecord",
                        "properties": [{"name": "hiddenField"}],
                    }
                ],
                "relationships": [],
            },
            "provenance": {
                "producer": "test",
                "direction": "reverse",
                "source": {"kind": "arango", "ref": "vault"},
            },
        },
    ]
    catalog = SourceCatalog.from_csi_documents(documents)

    def source(
        source_id: str,
        ref: str,
        concept: str,
        entitlements: dict,
    ) -> dict:
        return {
            "sourceId": source_id,
            "kind": "arango",
            "ref": ref,
            "concepts": [concept],
            "csi": {
                "path": f"{ref}.json",
                "sha256": "0" * 64,
                "generation": "v1",
                "producer": "test",
                "direction": "reverse",
            },
            "r2rml": None,
            "statisticsSnapshot": None,
            "joinKeys": [],
            "entitlements": entitlements,
            "runtimeResolution": {"mode": "none"},
            "auth": {"mode": "service", "delegation": "none"},
        }

    catalog.apply_manifest(
        parse_manifest(
            {
                "catalogManifestVersion": "1",
                "generation": "policy-v1",
                "contentHash": "0" * 64,
                "conceptBase": "urn:arango-sparql:concept#",
                "sources": [
                    source(
                        "arango:crm",
                        "crm",
                        "Account",
                        _policy(
                            properties={
                                "privateNote": {"allowedRoles": ["admin"]}
                            }
                        ),
                    ),
                    source(
                        "arango:vault",
                        "vault",
                        "SecretRecord",
                        _policy(allowedRoles=["admin"]),
                    ),
                ],
            }
        )
    )
    return catalog


class _Executor:
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.calls = []

    def execute(self, subquery):
        self.calls.append(subquery)
        return SourceResult(
            rows=self.rows,
            native_query="SELECT governed fields",
            source_objects=("accounts",),
        )


class _CaptureLLM:
    provider = "test"
    model = "policy-safe"

    def __init__(self, sparql: str):
        self.sparql = sparql
        self.messages = []

    def generate(self, messages):
        self.messages.append(tuple(dict(item) for item in messages))
        return type(
            "Response",
            (),
            {
                "content": self.sparql,
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "cached_tokens": 0,
            },
        )()


class _Examples:
    def __init__(self, examples):
        self.examples = tuple(examples)

    def retrieve(self, _question, *, top_k):
        return self.examples[:top_k]


def _service(entitlements, executor, **kwargs):
    catalog = _catalog(entitlements)
    return FederationService(
        catalog=catalog,
        executors={"arango:crm": executor},
        policy_pdp=CatalogPolicyPDP(catalog),
        **kwargs,
    )


def test_role_scope_group_and_purpose_deny_before_source_call() -> None:
    executor = _Executor([{"name": "Acme", "tenantId": "tenant-a"}])
    service = _service(
        _policy(
            allowedRoles=["analyst"],
            allowedGroups=["support"],
            allowedScopes=["fabric.read"],
            allowedPurposes=["support"],
        ),
        executor,
    )

    denied = service.federate_sparql(QUERY, context=_context(roles=("analyst",)))
    allowed = service.federate_sparql(
        QUERY,
        context=_context(
            roles=("analyst",),
            groups=("support",),
            scopes=("fabric.read",),
        ),
    )

    assert denied.status == "refused"
    assert denied.authorization_refusal.code == "authorization_denied"
    assert len(executor.calls) == 1
    assert allowed.status == "grounded"


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        (
            _policy(properties={"email": {"unexpected": True}}),
            "unknown fields",
        ),
        (
            _policy(rowConstraints={"?tenantId": "tenant"}),
            "bare SPARQL binding variable",
        ),
        (
            _policy(rowConstraints={"tenantId": "claim:access_token"}),
            "secret-like claim",
        ),
    ],
)
def test_entitlements_reject_unknown_secret_like_and_unsafe_fields(
    rule: dict,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _catalog(rule)


def test_policy_required_forbids_legacy_none_backend() -> None:
    catalog = SourceCatalog()
    with pytest.raises(ValueError, match="forbidden"):
        policy_pdp_from_env(
            catalog,
            {
                "CDF_POLICY_BACKEND": "none",
                "CDF_POLICY_REQUIRED": "true",
            },
        )


def test_tenant_constraint_is_pushed_down_and_verified() -> None:
    executor = _Executor([{"name": "Acme", "tenantId": "tenant-a"}])
    service = _service(
        _policy(
            rowConstraints={"tenantId": "tenant"},
            allowFabricRowPushdown=True,
        ),
        executor,
    )

    envelope = service.federate_sparql(QUERY, context=_context())

    assert envelope.status == "grounded"
    assert 'VALUES ?tenantId { "tenant-a" }' in executor.calls[0].sparql
    assert 'FILTER(?tenantId = "tenant-a")' in executor.calls[0].sparql
    assert envelope.policy_ids == ("policy:account-read",)
    assert envelope.retrieval_path[0].authorization_events


def test_out_of_scope_source_row_is_fail_closed_and_never_cited() -> None:
    executor = _Executor([{"name": "Other", "tenantId": "tenant-b"}])
    service = _service(
        _policy(
            rowConstraints={"tenantId": "tenant"},
            allowFabricRowPushdown=True,
        ),
        executor,
    )

    envelope = service.federate_sparql(QUERY, context=_context())

    assert envelope.status == "refused"
    assert envelope.refusal_class == "cross_tenant_policy_violation"
    assert envelope.authorization_refusal.code == "row_scope_violation"
    assert "tenant-b" not in repr(envelope)
    assert envelope.citations == ()


def test_redaction_and_hmac_are_applied_only_after_execution() -> None:
    golden = json.loads(
        (Path(__file__).parent / "goldens" / "authorization-v1.json").read_text()
    )
    redacting = _Executor([{"name": "Acme", "tenantId": "tenant-a"}])
    redacted_service = _service(
        _policy(
            properties={
                "name": {
                    "mask": "redact",
                    "allowFabricMasking": True,
                    "policyIds": ["policy:name-mask"],
                }
            }
        ),
        redacting,
    )
    redacted = redacted_service.federate_sparql(QUERY, context=_context())
    assert redacting.rows[0]["name"] == "Acme"
    assert redacted.bindings[0]["name"] == golden["redacted_name"]

    resolver = StaticMaskingKeyResolver(SecretMaskingKey("stable-secret-material"))
    hashing = _Executor([{"name": "Acme", "tenantId": "tenant-a"}])
    hashed_service = _service(
        _policy(
            properties={
                "name": {
                    "mask": "hmac",
                    "allowFabricMasking": True,
                    "policyIds": ["policy:name-hmac"],
                }
            }
        ),
        hashing,
        masking_key_resolver=resolver,
    )
    first = hashed_service.federate_sparql(QUERY, context=_context())
    second = hashed_service.federate_sparql(QUERY, context=_context())
    assert first.bindings[0]["name"] == second.bindings[0]["name"]
    assert first.bindings[0]["name"] == golden["hmac_name"]
    assert first.bindings[0]["name"].startswith("hmac-sha256:")
    assert "stable-secret-material" not in repr((resolver, first))


def test_hmac_without_key_refuses_and_optional_drop_requires_partial() -> None:
    hmac_service = _service(
        _policy(
            properties={
                "name": {
                    "mask": "hmac",
                    "allowFabricMasking": True,
                }
            }
        ),
        _Executor([{"name": "Acme", "tenantId": "tenant-a"}]),
    )
    assert (
        hmac_service.federate_sparql(QUERY, context=_context()).authorization_refusal.code
        == "masking_key_unavailable"
    )

    drop_query = (
        PREFIX
        + "SELECT ?name ?privateNote WHERE { "
        "?a a c:Account ; c:name ?name . "
        "OPTIONAL { ?a c:privateNote ?privateNote . } }"
    )
    executor = _Executor([{"name": "Acme"}])
    drop_service = _service(
        _policy(
            properties={
                "privateNote": {
                    "mask": "drop",
                    "allowFabricMasking": True,
                    "policyIds": ["policy:private-note-drop"],
                }
            }
        ),
        executor,
    )
    refused = drop_service.federate_sparql(drop_query, context=_context())
    partial = drop_service.federate_sparql(
        drop_query,
        context=_context(),
        allow_partial=True,
    )
    assert refused.status == "refused"
    assert partial.status == "partial"
    assert "privateNote" not in executor.calls[-1].sparql
    assert "privateNote" not in partial.bindings[0]


def test_unauthorized_filter_is_refused_before_call() -> None:
    query = (
        PREFIX
        + 'SELECT ?name WHERE { ?a a c:Account ; c:name ?name ; c:email ?email . '
        + 'FILTER(?email = "x@example.com") }'
    )
    executor = _Executor([{"name": "Acme", "email": "x@example.com"}])
    service = _service(
        _policy(properties={"email": {"allowedRoles": ["admin"]}}),
        executor,
    )
    envelope = service.federate_sparql(query, context=_context())
    assert envelope.status == "refused"
    assert executor.calls == []


def test_service_pushdown_requires_trusted_pep_but_delegated_still_checks() -> None:
    catalog = _catalog(_policy(rowConstraints={"tenantId": "tenant"}))
    pdp = CatalogPolicyPDP(catalog)
    common = {
        "source_id": "arango:crm",
        "resource_type": "property",
        "resource_id": "tenantId",
        "usage": "filter",
        "variable": "tenantId",
    }
    service = pdp.authorize(
        (ResourceRequest(**common, source_auth_mode="service"),),
        _context(),
        catalog_generation="policy-v1",
    )
    delegated = pdp.authorize(
        (ResourceRequest(**common, source_auth_mode="delegated"),),
        _context(),
        catalog_generation="policy-v1",
    )
    assert service.decisions[0].action == "deny"
    assert delegated.decisions[0].action == "rewrite"
    assert delegated.decisions[0].row_constraints[0].expected_value == "tenant-a"


class _Transport:
    def __init__(self, allowed=True, error=False):
        self.allowed = allowed
        self.error = error
        self.calls = []

    def check(self, config, payload, *, bearer):
        self.calls.append((config, payload, bearer))
        if self.error:
            raise TimeoutError("fake timeout")
        return self.allowed


class _HTTPResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit):
        return self.body[:limit]


class _HTTPOpener:
    def __init__(self, body: bytes | None = None, error: Exception | None = None):
        self.body = body
        self.error = error
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        assert self.body is not None
        return _HTTPResponse(self.body)


def _openfga_config(api_url: str) -> OpenFGAConfig:
    return OpenFGAConfig(
        api_url=api_url,
        store_id="store-1",
        authorization_model_id="model-1",
        relationship="can_read",
        timeout_seconds=0.5,
    )


def test_openfga_requires_https_except_explicit_loopback() -> None:
    with pytest.raises(ValueError, match="requires HTTPS"):
        _openfga_config("http://fga.example")

    assert _openfga_config("https://fga.example").api_url == "https://fga.example"
    assert _openfga_config("http://localhost:8080").api_url.startswith("http://")
    assert _openfga_config("http://127.0.0.1:8080").api_url.startswith("http://")
    assert _openfga_config("http://[::1]:8080").api_url.startswith("http://")


def test_openfga_loopback_http_allows_no_bearer_only() -> None:
    transport = HttpOpenFGATransport()
    opener = _HTTPOpener(b'{"allowed":true}')
    transport._opener = opener
    config = _openfga_config("http://127.0.0.1:8080")

    assert transport.check(config, {"tuple_key": {}}, bearer=None) is True
    assert opener.requests[0][1] == 0.5
    assert opener.requests[0][0].get_header("Authorization") is None

    material = SecretMaterial("loopback-do-not-send")
    with pytest.raises(ConnectionError, match="requires HTTPS") as exc_info:
        transport.check(config, {"tuple_key": {}}, bearer=material)
    assert "loopback-do-not-send" not in repr(exc_info.value)
    assert len(opener.requests) == 1


def test_openfga_https_accepts_bearer_without_exposing_it() -> None:
    token = "https-policy-bearer"
    transport = HttpOpenFGATransport()
    opener = _HTTPOpener(b'{"allowed":false}')
    transport._opener = opener
    material = SecretMaterial(token)

    assert (
        transport.check(
            _openfga_config("https://fga.example"),
            {"tuple_key": {}},
            bearer=material,
        )
        is False
    )
    assert opener.requests[0][0].get_header("Authorization") == f"Bearer {token}"
    assert token not in repr(material)
    assert token not in repr(transport)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b'{"allowed":"true"}',
        b'{"allowed":1}',
        b"{}",
    ],
)
def test_openfga_rejects_malformed_response_shapes(body: bytes) -> None:
    transport = HttpOpenFGATransport()
    transport._opener = _HTTPOpener(body)
    with pytest.raises(ConnectionError, match="check unavailable"):
        transport.check(
            _openfga_config("https://fga.example"),
            {"tuple_key": {}},
            bearer=None,
        )


def test_openfga_rejects_oversized_response() -> None:
    transport = HttpOpenFGATransport()
    transport._opener = _HTTPOpener(
        b'{"allowed":true,"padding":"' + b"x" * (64 * 1024) + b'"}'
    )
    with pytest.raises(ConnectionError, match="check unavailable"):
        transport.check(
            _openfga_config("https://fga.example"),
            {"tuple_key": {}},
            bearer=None,
        )


def test_openfga_transport_errors_and_repr_never_expose_bearer() -> None:
    token = "production-policy-bearer"
    transport = HttpOpenFGATransport()
    transport._opener = _HTTPOpener(error=RuntimeError(f"failed with Bearer {token}"))
    material = SecretMaterial(token)

    with pytest.raises(ConnectionError, match="check unavailable") as exc_info:
        transport.check(
            _openfga_config("https://fga.example"),
            {"tuple_key": {}},
            bearer=material,
        )

    assert exc_info.value.__cause__ is None
    assert token not in repr(exc_info.value)
    assert token not in repr(material)
    assert token not in repr(transport)


def test_openfga_redirect_handler_refuses_redirects() -> None:
    handler = HttpOpenFGATransport._NoRedirectHandler()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://other") is None


@pytest.mark.parametrize(
    ("allowed", "error", "expected"),
    [(True, False, "allow"), (False, False, "deny"), (True, True, "deny")],
)
def test_openfga_fake_transport_allow_deny_and_unavailable(
    allowed: bool,
    error: bool,
    expected: str,
) -> None:
    catalog = _catalog(_policy())
    transport = _Transport(allowed=allowed, error=error)
    pdp = OpenFGAPolicyPDP(
        catalog,
        OpenFGAConfig(
            api_url="https://fga.example",
            store_id="store-1",
            authorization_model_id="model-1",
            relationship="can_read",
            timeout_seconds=0.5,
        ),
        transport=transport,
    )
    decision = pdp.authorize(
        (
            ResourceRequest(
                source_id="arango:crm",
                resource_type="source",
                resource_id="arango:crm",
                usage="load",
            ),
        ),
        _context(),
        catalog_generation="policy-v1",
    ).decisions[0]
    assert decision.action == expected
    assert transport.calls[0][1]["authorization_model_id"] == "model-1"
    if error:
        assert decision.reason == "policy decision point unavailable"


class _ChangingPDP:
    def __init__(self):
        self.calls = 0
        self.base = NonePolicyPDP()

    def authorize(self, resources, context, *, catalog_generation):
        self.calls += 1
        authorization = self.base.authorize(
            resources,
            context,
            catalog_generation=catalog_generation,
        )
        if self.calls == 1:
            return authorization
        denied = replace(
            authorization.decisions[0],
            action="deny",
            reason="changed",
        )
        return replace(
            authorization,
            decision_id="changed",
            decisions=(denied, *authorization.decisions[1:]),
        )


def test_policy_change_at_postflight_refuses_answer() -> None:
    catalog = _catalog(_policy())
    service = FederationService(
        catalog=catalog,
        executors={
            "arango:crm": _Executor([{"name": "Acme", "tenantId": "tenant-a"}])
        },
        policy_pdp=_ChangingPDP(),
    )
    envelope = service.federate_sparql(QUERY, context=_context())
    assert envelope.status == "refused"
    assert envelope.refusal_class == "policy_changed"


def test_llm_prompt_uses_only_authorized_vocabulary_and_examples() -> None:
    visible_query = (
        PREFIX + "SELECT ?name WHERE { ?a a c:Account ; c:name ?name . }"
    )
    client = _CaptureLLM(visible_query)
    examples = _Examples(
        (
            CorpusExample(
                id="hidden",
                question="Show SecretRecord hiddenField values",
                aliases=("List vault records",),
                sparql=(
                    PREFIX
                    + "SELECT ?hiddenField WHERE { "
                    "?record a c:SecretRecord ; c:hiddenField ?hiddenField . }"
                ),
                expected_sources=("arango:vault",),
                expected_join_keys=(),
                refusal=False,
            ),
            CorpusExample(
                id="visible",
                question="Show account names",
                aliases=("List visible names",),
                sparql=visible_query,
                expected_sources=("arango:crm",),
                expected_join_keys=(),
                refusal=False,
            ),
        )
    )
    catalog = _nl_policy_catalog()
    service = FederationService(
        catalog=catalog,
        executors={"arango:crm": _Executor([{"name": "Acme"}])},
        policy_pdp=CatalogPolicyPDP(catalog),
        nl_client=client,
        few_shot_retriever=examples,
        few_shot_top_k=2,
    )

    preview = service.resolve_question_for_preview(
        "Which visible account names exist?",
        _context(),
    )
    envelope = service.federate_question(
        "Which visible account names exist?",
        context=_context(),
    )

    assert preview.sparql == visible_query
    assert envelope.status == "grounded"
    prompt_text = repr(client.messages)
    assert "privateNote" not in prompt_text
    assert "arango:vault" not in prompt_text
    assert "SecretRecord" not in prompt_text
    assert "hiddenField" not in prompt_text
    assert "Show SecretRecord hiddenField values" not in prompt_text
    assert "Show account names" in prompt_text
    assert "class Account" in prompt_text
    assert "c:name" in prompt_text


def test_hidden_only_identity_refuses_without_calling_llm() -> None:
    client = _CaptureLLM(
        PREFIX + "SELECT ?name WHERE { ?a a c:Account ; c:name ?name . }"
    )
    service = _service(
        _policy(allowedRoles=["admin"]),
        _Executor([{"name": "Acme"}]),
        nl_client=client,
    )

    preview = service.resolve_question_for_preview("List accounts", _context())
    envelope = service.federate_question("List accounts", context=_context())

    assert preview.sparql is None
    assert "no authorized catalog vocabulary" in (preview.error or "")
    assert envelope.status == "refused"
    assert client.messages == []


def test_prepared_route_stays_local_but_plan_is_still_denied() -> None:
    hidden_query = (
        PREFIX
        + "SELECT ?hiddenField WHERE { "
        "?record a c:SecretRecord ; c:hiddenField ?hiddenField . }"
    )
    llm = _CaptureLLM(hidden_query)
    catalog = _nl_policy_catalog()
    service = FederationService(
        catalog=catalog,
        executors={},
        prepared_questions={"show vault records": hidden_query},
        nl_client=llm,
        policy_pdp=CatalogPolicyPDP(catalog),
    )

    preview = service.resolve_question_for_preview("Show vault records", _context())
    envelope = service.federate_question("Show vault records", context=_context())

    assert preview.sparql is None
    assert envelope.status == "refused"
    assert envelope.authorization_refusal is not None
    assert llm.messages == []


def test_http_preview_and_federate_share_authorized_nl_prompt() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    visible_query = (
        PREFIX + "SELECT ?name WHERE { ?a a c:Account ; c:name ?name . }"
    )
    llm = _CaptureLLM(visible_query)
    service = _service(
        _policy(properties={"privateNote": {"allowedRoles": ["admin"]}}),
        _Executor([{"name": "Acme"}]),
        nl_client=llm,
    )
    client = TestClient(create_app(service))

    preview = client.post("/nl-preview", json={"question": "List account names"})
    federated = client.post("/federate", json={"question": "List account names"})

    assert preview.json()["sparql"] == visible_query
    assert federated.json()["status"] == "grounded"
    assert "privateNote" not in repr(llm.messages)

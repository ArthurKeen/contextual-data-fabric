"""Tests for the HTTP service seam (``POST /federate`` → envelope).

The FastAPI layer is optional (``pip install -e ".[service]"``); these tests
skip when it isn't installed. The engine underneath is exercised with stub
executors — the live legs have their own opt-in tests.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cdf.query import SourceCatalog, SourceResult  # noqa: E402
from cdf.service import FederationService, create_app  # noqa: E402

_ACCOUNT_CSI = {
    "csiVersion": "1",
    "conceptualModel": {"entities": [{"name": "Account", "properties": [{"name": "name"}]}]},
    "physicalMapping": {"entities": {"Account": {"tableName": "accounts"}}},
    "provenance": {"producer": "r2g", "direction": "forward",
                   "source": {"kind": "postgresql", "ref": "crm"}},
}

_TICKET_CSI = {
    "csiVersion": "1",
    "conceptualModel": {"entities": [{"name": "Ticket", "properties": [{"name": "subject"}]}]},
    "arangoPhysicalMapping": {
        "entities": {"Ticket": {"style": "COLLECTION", "collectionName": "tickets"}},
        "relationships": {},
    },
    "provenance": {"producer": "analyzer", "direction": "reverse",
                   "source": {"kind": "arango", "ref": "tickets"}},
}

_SPARQL = (
    "PREFIX c: <urn:arango-sparql:concept#> "
    "SELECT ?name WHERE { ?a a c:Account ; c:name ?name }"
)


class _StubExecutor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, subquery):
        return SourceResult(
            rows=tuple(self._rows),
            native_query="SELECT stub",
            source_objects=("accounts",),
            as_of="2026-07-16T00:00:00Z",
        )


@pytest.fixture()
def client() -> TestClient:
    catalog = SourceCatalog.from_csi_documents([_ACCOUNT_CSI, _TICKET_CSI])
    service = FederationService(
        catalog=catalog,
        executors={"postgresql:crm": _StubExecutor([{"a": "urn:1", "name": "Acme"}])},
        prepared_questions={"which accounts exist?": _SPARQL},
    )
    return TestClient(create_app(service))


def test_health_lists_sources(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["sources"] == ["postgresql:crm"]
    assert body["prepared_questions"] == 1


def test_federate_sparql_returns_grounded_envelope(client: TestClient) -> None:
    resp = client.post("/federate", json={"sparql": _SPARQL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "grounded"
    assert body["bindings"] == [{"name": "Acme"}]
    assert body["citations"][0]["source_id"] == "postgresql:crm"
    assert body["retrieval_path"][0]["status"] == "ok"


def test_federate_prepared_question_resolves(client: TestClient) -> None:
    resp = client.post("/federate", json={"question": "Which  accounts EXIST?"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "grounded"


def test_unknown_question_is_refused_not_guessed(client: TestClient) -> None:
    body = client.post("/federate", json={"question": "meaning of life?"}).json()
    assert body["status"] == "refused"
    assert "WP-D1" in body["refusal_reason"]


def test_exactly_one_input_required(client: TestClient) -> None:
    assert client.post("/federate", json={}).status_code == 422
    assert (
        client.post("/federate", json={"sparql": _SPARQL, "question": "hi"}).status_code == 422
    )


def test_unsupported_construct_is_a_declared_422(client: TestClient) -> None:
    filtered = (
        "PREFIX c: <urn:arango-sparql:concept#> SELECT ?name WHERE "
        "{ ?a a c:Account ; c:name ?name . FILTER(?name != 'x') }"
    )
    resp = client.post("/federate", json={"sparql": filtered})
    assert resp.status_code == 422
    assert "FILTER" in resp.json()["detail"]

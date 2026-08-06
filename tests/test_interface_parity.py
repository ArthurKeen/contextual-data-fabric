"""Versioned HTTP/MCP semantic-envelope parity gate."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

mcp = pytest.importorskip("mcp", reason="install the optional mcp extra")
from mcp import Client  # noqa: E402

from cdf.eval.nl_corpus import normalize_question  # noqa: E402
from cdf.mcp_server import create_mcp_server  # noqa: E402
from cdf.query import SourceCatalog, SourceResult  # noqa: E402
from cdf.service import FederationService, create_app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "src/cdf/eval/corpora/interface-parity-v1.json"
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
_VOLATILE_KEYS = frozenset(
    {
        "request_metadata",
        "duration_ms",
        "leg_duration_sum_ms",
    }
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Executor:
    def execute(self, _subquery: Any) -> SourceResult:
        return SourceResult(
            rows=({"name": "Acme"},),
            native_query="SELECT name FROM accounts",
            source_objects=("accounts",),
            as_of="2026-08-05T00:00:00Z",
        )


def _load_cases() -> list[dict[str, Any]]:
    document = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["corpus_version"] == "interface-parity-v1"
    cases = document["cases"]
    assert isinstance(cases, list)
    return cases


def _service(cases: list[dict[str, Any]]) -> FederationService:
    prepared = {
        normalize_question(case["question"]): _SPARQL
        for case in cases
        if case["prepared"]
    }
    return FederationService(
        catalog=SourceCatalog.from_csi_documents([_CSI]),
        executors={"postgresql:crm": _Executor()},
        prepared_questions=prepared,
    )


def _stable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _stable(item)
            for key, item in value.items()
            if key not in _VOLATILE_KEYS and not key.endswith("_duration_ms")
        }
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


def test_interface_parity_corpus_is_versioned_and_has_twenty_cases() -> None:
    cases = _load_cases()
    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20
    assert {case["execution_mode"] for case in cases} == {"virtual", "assembled"}
    assert {case["expected_status"] for case in cases} == {"grounded", "refused"}


@pytest.mark.anyio
async def test_http_and_mcp_return_equivalent_semantic_envelopes() -> None:
    cases = _load_cases()
    service = _service(cases)
    http = TestClient(create_app(service))

    async with Client(create_mcp_server(lambda: service), raise_exceptions=True) as client:
        for case in cases:
            arguments = {
                "question": case["question"],
                "allow_partial": case["allow_partial"],
                "execution_mode": case["execution_mode"],
            }
            http_response = http.post("/federate", json=arguments)
            mcp_response = await client.call_tool("federate", arguments)

            assert http_response.status_code == 200, case["id"]
            assert not mcp_response.is_error, case["id"]
            assert mcp_response.structured_content is not None
            http_envelope = http_response.json()
            mcp_envelope = mcp_response.structured_content
            assert http_envelope["status"] == case["expected_status"], case["id"]
            assert mcp_envelope["status"] == case["expected_status"], case["id"]
            assert _stable(http_envelope) == _stable(mcp_envelope), case["id"]

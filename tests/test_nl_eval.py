"""Offline NL evaluation dimension scoring."""

from __future__ import annotations

import json
from pathlib import Path

from cdf.eval.nl_corpus import load_nl_corpus, validate_corpus_document
from cdf.eval.nl_eval import evaluate_nl_corpus
from cdf.query import SourceCatalog

ROOT = Path(__file__).resolve().parents[1]


def _catalog() -> SourceCatalog:
    documents = [
        json.loads(path.read_text())
        for path in sorted((ROOT / "deploy" / "csi").glob("*.json"))
    ]
    return SourceCatalog.from_csi_documents(documents)


def test_offline_corpus_scores_decomposition_and_safe_refusals() -> None:
    report = evaluate_nl_corpus(load_nl_corpus(), _catalog())
    assert report.all_passed

    peak = next(case for case in report.cases if case.id == "peak-usage-signals")
    assert peak.parse_valid is True
    assert peak.partition_valid is True
    assert peak.actual_sources == (
        "arango:cmf",
        "postgresql:crm",
        "snowflake:telemetry",
    )
    assert peak.actual_join_keys == ("?acct",)
    assert peak.actual_path == "deterministic"

    refusal = next(case for case in report.cases if case.id == "refuse-secrets")
    assert refusal.parse_valid is None
    assert refusal.partition_valid is None
    assert refusal.refusal_correct
    assert refusal.refusal_reason_correct


def test_fixture_client_can_score_llm_path_without_api_key() -> None:
    sparql = (
        "PREFIX c: <urn:arango-sparql:concept#> "
        "SELECT ?name WHERE { ?a a c:Account ; c:accountName ?name }"
    )
    corpus = validate_corpus_document(
        {
            "schema_version": 1,
            "corpus_version": "fixture",
            "examples": [
                {
                    "id": "fixture-account",
                    "question": "Which accounts are present?",
                    "aliases": ["List the present accounts."],
                    "expected": {
                        "sparql": sparql,
                        "sources": ["postgresql:crm"],
                        "join_keys": [],
                        "refusal": False,
                        "path": "llm",
                    },
                }
            ],
        }
    )

    class FakeClient:
        def generate(self, messages):
            return type("Response", (), {"content": sparql})()

    report = evaluate_nl_corpus(
        corpus,
        _catalog(),
        client=FakeClient(),
        deterministic=False,
    )
    assert report.all_passed
    assert report.cases[0].actual_path == "llm"

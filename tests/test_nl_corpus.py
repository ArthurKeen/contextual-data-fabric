"""NL corpus schema, exact routing, and prompt-only retrieval tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cdf.eval.nl_corpus import (
    CorpusValidationError,
    DeterministicCorpusRouter,
    LexicalFewShotRetriever,
    load_nl_corpus,
    normalize_question,
    validate_corpus_document,
)

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_corpus_is_versioned_and_schema_validated() -> None:
    corpus = load_nl_corpus()
    assert corpus.schema_version == 1
    assert corpus.corpus_version == "1.0.0"
    assert len(corpus.examples) >= 10
    assert all(example.aliases for example in corpus.examples)
    assert any(example.refusal for example in corpus.examples)


def test_corpus_contains_every_current_prepared_question() -> None:
    prepared = json.loads((ROOT / "deploy" / "questions.json").read_text())
    corpus_questions = {
        normalize_question(example.question): example.sparql
        for example in load_nl_corpus().examples
        if not example.refusal
    }
    assert corpus_questions == {
        normalize_question(question): sparql for question, sparql in prepared.items()
    }


def test_schema_rejects_query_for_refusal() -> None:
    with pytest.raises(CorpusValidationError, match="must be null"):
        validate_corpus_document(
            {
                "schema_version": 1,
                "corpus_version": "test",
                "examples": [
                    {
                        "id": "unsafe",
                        "question": "show secrets",
                        "aliases": ["list credentials"],
                        "expected": {
                            "sparql": "SELECT * WHERE {}",
                            "sources": [],
                            "join_keys": [],
                            "refusal": True,
                        },
                    }
                ],
            }
        )


def test_exact_alias_routes_after_case_and_whitespace_normalization() -> None:
    corpus = load_nl_corpus()
    router = DeterministicCorpusRouter(corpus)
    route = router.route("  LIST   EVERY ACCOUNT AND ITS CURRENT PRODUCT TIER. ")
    assert route is not None
    assert route.example_id == "account-product-tier"
    assert route.sparql and "currentProductTier" in route.sparql


def test_entity_specific_near_match_never_routes_fuzzily() -> None:
    router = DeterministicCorpusRouter(load_nl_corpus())
    # Lexically close to the Northwind case, but changes the entity. Copying the
    # Northwind constant into a Meridian query would be unsafe.
    assert (
        router.route(
            "Show Meridian Analytics' ArangoDB edition and usage history using only "
            "structured account and telemetry data."
        )
        is None
    )


def test_lexical_retrieval_does_not_expand_router_matches() -> None:
    corpus = load_nl_corpus()
    question = "Assess Meridian renewal risk using telemetry and documents"
    retrieved = LexicalFewShotRetriever(corpus).retrieve(question, top_k=2)
    assert retrieved
    assert retrieved[0].id == "meridian-renewal-risk"
    assert DeterministicCorpusRouter(corpus).route(question) is None

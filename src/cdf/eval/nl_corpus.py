"""Versioned NL evaluation corpus, deterministic routing, and few-shot retrieval.

The deterministic router is deliberately exact: normalization only case-folds
and collapses whitespace. Lexical similarity is available solely for selecting
prompt examples; it can never select executable SPARQL.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol, cast

SCHEMA_VERSION = 1
DEFAULT_CORPUS_RESOURCE = "corpora/nl-corpus-v1.json"
_TOKEN = re.compile(r"[a-z0-9_]+")


class CorpusValidationError(ValueError):
    """Raised when an NL corpus does not conform to the v1 schema."""


def normalize_question(question: str) -> str:
    """Normalize only case and whitespace, preserving entity-specific wording."""
    return " ".join(question.casefold().split())


@dataclass(frozen=True)
class CorpusExample:
    """One exact-route and evaluation example."""

    id: str
    question: str
    aliases: tuple[str, ...]
    sparql: str | None
    expected_sources: tuple[str, ...]
    expected_join_keys: tuple[str, ...]
    refusal: bool
    refusal_reason_contains: tuple[str, ...] = ()
    expected_path: str = "deterministic"

    @property
    def utterances(self) -> tuple[str, ...]:
        return (self.question, *self.aliases)


@dataclass(frozen=True)
class NlCorpus:
    """A validated, immutable corpus document."""

    schema_version: int
    corpus_version: str
    examples: tuple[CorpusExample, ...]


@dataclass(frozen=True)
class DeterministicRoute:
    """An explicit exact corpus match."""

    example_id: str
    sparql: str | None
    refusal: bool
    refusal_reason: str | None = None


class FewShotRetriever(Protocol):
    """Pluggable prompt-example retrieval seam."""

    def retrieve(self, question: str, *, top_k: int) -> Sequence[CorpusExample]:
        """Return ranked examples for prompt enrichment only."""


def _fail(location: str, message: str) -> None:
    raise CorpusValidationError(f"{location}: {message}")


def _string(value: Any, location: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        _fail(location, "expected string")
    if nonempty and not value.strip():
        _fail(location, "must not be empty")
    return value


def _string_list(value: Any, location: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail(location, "expected array of strings")
    result = tuple(_string(item, f"{location}[{i}]") for i, item in enumerate(value))
    if len(set(result)) != len(result):
        _fail(location, "contains duplicates")
    return result


def validate_corpus_document(document: Mapping[str, Any]) -> NlCorpus:
    """Validate and parse a corpus document against the built-in v1 schema."""
    if not isinstance(document, Mapping):
        _fail("$", "expected object")
    allowed_top = {"schema_version", "corpus_version", "examples"}
    unknown_top = set(document) - allowed_top
    if unknown_top:
        _fail("$", f"unknown field(s): {', '.join(sorted(unknown_top))}")
    if document.get("schema_version") != SCHEMA_VERSION:
        _fail("$.schema_version", f"expected {SCHEMA_VERSION}")
    corpus_version = _string(document.get("corpus_version"), "$.corpus_version")
    raw_examples_value = document.get("examples")
    if not isinstance(raw_examples_value, list) or not raw_examples_value:
        _fail("$.examples", "expected a non-empty array")
    raw_examples = cast(list[Any], raw_examples_value)

    examples: list[CorpusExample] = []
    ids: set[str] = set()
    utterances: dict[str, str] = {}
    for index, raw in enumerate(raw_examples):
        loc = f"$.examples[{index}]"
        if not isinstance(raw, Mapping):
            _fail(loc, "expected object")
        allowed = {"id", "question", "aliases", "expected"}
        unknown = set(raw) - allowed
        if unknown:
            _fail(loc, f"unknown field(s): {', '.join(sorted(unknown))}")
        example_id = _string(raw.get("id"), f"{loc}.id")
        if example_id in ids:
            _fail(f"{loc}.id", f"duplicate id {example_id!r}")
        ids.add(example_id)
        question = _string(raw.get("question"), f"{loc}.question")
        aliases = _string_list(raw.get("aliases"), f"{loc}.aliases")
        if not aliases:
            _fail(f"{loc}.aliases", "must include at least one explicit safe alias")

        expected = raw.get("expected")
        if not isinstance(expected, Mapping):
            _fail(f"{loc}.expected", "expected object")
        allowed_expected = {
            "sparql",
            "sources",
            "join_keys",
            "refusal",
            "refusal_reason_contains",
            "path",
        }
        unknown_expected = set(expected) - allowed_expected
        if unknown_expected:
            _fail(
                f"{loc}.expected",
                f"unknown field(s): {', '.join(sorted(unknown_expected))}",
            )
        refusal = expected.get("refusal")
        if not isinstance(refusal, bool):
            _fail(f"{loc}.expected.refusal", "expected boolean")
        sparql_value = expected.get("sparql")
        if refusal:
            if sparql_value is not None:
                _fail(f"{loc}.expected.sparql", "must be null for a refusal")
        else:
            sparql_value = _string(sparql_value, f"{loc}.expected.sparql")
        sources = _string_list(expected.get("sources"), f"{loc}.expected.sources")
        join_keys = _string_list(expected.get("join_keys"), f"{loc}.expected.join_keys")
        for key in join_keys:
            if not key.startswith("?"):
                _fail(f"{loc}.expected.join_keys", f"{key!r} must start with '?'")
        reason_contains = _string_list(
            expected.get("refusal_reason_contains", []),
            f"{loc}.expected.refusal_reason_contains",
        )
        path = _string(expected.get("path", "deterministic"), f"{loc}.expected.path")
        if path not in {"deterministic", "llm", "refuse"}:
            _fail(f"{loc}.expected.path", "expected deterministic, llm, or refuse")

        for utterance in (question, *aliases):
            normalized = normalize_question(utterance)
            prior = utterances.get(normalized)
            if prior is not None:
                _fail(loc, f"normalized utterance duplicates example {prior!r}")
            utterances[normalized] = example_id

        examples.append(
            CorpusExample(
                id=example_id,
                question=question,
                aliases=aliases,
                sparql=sparql_value,
                expected_sources=sources,
                expected_join_keys=join_keys,
                refusal=refusal,
                refusal_reason_contains=reason_contains,
                expected_path=path,
            )
        )

    return NlCorpus(
        schema_version=SCHEMA_VERSION,
        corpus_version=corpus_version,
        examples=tuple(examples),
    )


def load_nl_corpus(path: str | Path | None = None) -> NlCorpus:
    """Load the packaged corpus or an explicitly supplied corpus file."""
    if path is None:
        resource = files("cdf.eval").joinpath(DEFAULT_CORPUS_RESOURCE)
        document = json.loads(resource.read_text(encoding="utf-8"))
    else:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_corpus_document(document)


class DeterministicCorpusRouter:
    """Route only explicit normalized corpus questions and aliases."""

    def __init__(self, corpus: NlCorpus) -> None:
        self.corpus = corpus
        self._routes = {
            normalize_question(utterance): example
            for example in corpus.examples
            for utterance in example.utterances
        }

    def route(self, question: str) -> DeterministicRoute | None:
        """Return an exact route; no edit-distance or fuzzy fallback exists."""
        example = self._routes.get(normalize_question(question))
        if example is None:
            return None
        reason = None
        if example.refusal:
            reason = (
                "question matches a corpus refusal case"
                + (
                    f": {', '.join(example.refusal_reason_contains)}"
                    if example.refusal_reason_contains
                    else ""
                )
            )
        return DeterministicRoute(
            example_id=example.id,
            sparql=example.sparql,
            refusal=example.refusal,
            refusal_reason=reason,
        )


class LexicalFewShotRetriever:
    """Deterministic token-overlap baseline for prompt-only retrieval."""

    def __init__(self, corpus: NlCorpus) -> None:
        self.corpus = corpus

    @staticmethod
    def _tokens(text: str) -> frozenset[str]:
        return frozenset(_TOKEN.findall(normalize_question(text)))

    def retrieve(self, question: str, *, top_k: int) -> Sequence[CorpusExample]:
        if top_k <= 0:
            return ()
        query_tokens = self._tokens(question)
        ranked: list[tuple[int, float, str, CorpusExample]] = []
        for example in self.corpus.examples:
            example_tokens = self._tokens(" ".join(example.utterances))
            overlap = len(query_tokens & example_tokens)
            union = len(query_tokens | example_tokens)
            similarity = overlap / union if union else 0.0
            ranked.append((-overlap, -similarity, example.id, example))
        ranked.sort(key=lambda item: item[:3])
        return tuple(item[3] for item in ranked[:top_k] if item[0] < 0)

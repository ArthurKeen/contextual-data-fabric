"""Versioned canonical-hub corpus, evaluator, and deterministic CLI gate."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from cdf.resolution import (
    BackendResolveRequest,
    BackendResolveResult,
    FieldEvidence,
    GuardedResolver,
    ResolutionPolicy,
    ResolveEvidence,
    ResolveRequest,
    ResolveResult,
)

SCHEMA_VERSION = 1
DEFAULT_CORPUS_RESOURCE = "corpora/resolution-corpus-v1.json"


class ResolutionCorpusError(ValueError):
    """Raised when a resolution corpus is malformed."""


@dataclass(frozen=True)
class ResolutionExample:
    id: str
    account_scope: str
    attributes: Mapping[str, Any]
    truth_canonical_id: str | None
    backend_result: BackendResolveResult


@dataclass(frozen=True)
class ResolutionCorpus:
    schema_version: int
    corpus_version: str
    policy: ResolutionPolicy
    examples: tuple[ResolutionExample, ...]


@dataclass(frozen=True)
class ResolutionCaseScore:
    id: str
    truth_canonical_id: str | None
    result: ResolveResult
    true_positive: bool
    false_positive: bool
    false_negative: bool
    cross_scope_violation: bool
    evidence_completeness: float


@dataclass(frozen=True)
class ResolutionEvalReport:
    corpus_version: str
    cases: tuple[ResolutionCaseScore, ...]
    precision: float
    recall: float
    abstention_rate: float
    cross_scope_violations: int
    evidence_completeness: float
    passed: bool


class CorpusFakeResolver:
    """Deterministic backend whose outputs are labels in the corpus."""

    def __init__(self, corpus: ResolutionCorpus) -> None:
        self._results = {
            example.id: example.backend_result for example in corpus.examples
        }
        self.calls: list[str] = []

    def resolve(self, request: BackendResolveRequest) -> BackendResolveResult:
        if request.request_id is None or request.request_id not in self._results:
            raise KeyError("resolution corpus request_id is required")
        self.calls.append(request.request_id)
        return self._results[request.request_id]


def load_resolution_corpus(path: str | Path | None = None) -> ResolutionCorpus:
    if path is None:
        resource = files("cdf.eval").joinpath(DEFAULT_CORPUS_RESOURCE)
        document = json.loads(resource.read_text(encoding="utf-8"))
    else:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_resolution_corpus(document)


def validate_resolution_corpus(document: Mapping[str, Any]) -> ResolutionCorpus:
    if not isinstance(document, Mapping) or document.get("schema_version") != SCHEMA_VERSION:
        raise ResolutionCorpusError(f"schema_version must be {SCHEMA_VERSION}")
    corpus_version = _required_string(document.get("corpus_version"), "corpus_version")
    profile = document.get("profile")
    if not isinstance(profile, Mapping):
        raise ResolutionCorpusError("profile must be an object")
    fields_value = profile.get("observable_fields")
    if not isinstance(fields_value, list) or not fields_value:
        raise ResolutionCorpusError("profile.observable_fields must be a non-empty array")
    observable_fields = tuple(
        _required_string(value, "profile.observable_fields") for value in fields_value
    )
    policy = ResolutionPolicy(
        observable_fields=observable_fields,
        resolve_threshold=_required_float(
            profile.get("resolve_threshold"),
            "profile.resolve_threshold",
        ),
        minimum_margin=_required_float(
            profile.get("minimum_margin"),
            "profile.minimum_margin",
        ),
    )
    raw_examples = document.get("examples")
    if not isinstance(raw_examples, list) or not raw_examples:
        raise ResolutionCorpusError("examples must be a non-empty array")

    examples: list[ResolutionExample] = []
    seen: set[str] = set()
    for raw in raw_examples:
        if not isinstance(raw, Mapping):
            raise ResolutionCorpusError("every example must be an object")
        example_id = _required_string(raw.get("id"), "examples.id")
        if example_id in seen:
            raise ResolutionCorpusError(f"duplicate example id: {example_id}")
        seen.add(example_id)
        attributes = raw.get("attributes")
        if not isinstance(attributes, Mapping):
            raise ResolutionCorpusError(f"{example_id}.attributes must be an object")
        truth = raw.get("truth_canonical_id")
        if truth is not None and not isinstance(truth, str):
            raise ResolutionCorpusError(
                f"{example_id}.truth_canonical_id must be string or null"
            )
        backend = raw.get("backend")
        if not isinstance(backend, Mapping):
            raise ResolutionCorpusError(f"{example_id}.backend must be an object")
        examples.append(
            ResolutionExample(
                id=example_id,
                account_scope=_required_string(
                    raw.get("account_scope"),
                    f"{example_id}.account_scope",
                ),
                attributes=dict(attributes),
                truth_canonical_id=truth,
                backend_result=_parse_backend(backend, example_id),
            )
        )
    return ResolutionCorpus(
        schema_version=SCHEMA_VERSION,
        corpus_version=corpus_version,
        policy=policy,
        examples=tuple(examples),
    )


def evaluate_resolution_corpus(
    corpus: ResolutionCorpus,
    backend: CorpusFakeResolver | Any,
    *,
    clock: Callable[[], float] = time.monotonic,
    deadline_seconds: float = 5.0,
) -> ResolutionEvalReport:
    if deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be positive")
    wrapper = GuardedResolver(backend, corpus.policy, clock=clock)
    shared_deadline = clock() + deadline_seconds
    requests = tuple(
        ResolveRequest(
            account_scope=example.account_scope,
            attributes=example.attributes,
            deadline_at=shared_deadline,
            request_id=example.id,
        )
        for example in corpus.examples
    )
    results = wrapper.resolve_batch(requests, deadline_at=shared_deadline)
    cases = tuple(
        _score_case(example, result)
        for example, result in zip(corpus.examples, results, strict=True)
    )
    true_positives = sum(case.true_positive for case in cases)
    false_positives = sum(case.false_positive for case in cases)
    false_negatives = sum(case.false_negative for case in cases)
    predicted = true_positives + false_positives
    actual = true_positives + false_negatives
    precision = true_positives / predicted if predicted else 0.0
    recall = true_positives / actual if actual else 1.0
    abstention_rate = sum(case.result.status == "abstained" for case in cases) / len(cases)
    cross_scope_violations = sum(case.cross_scope_violation for case in cases)
    resolved = [case for case in cases if case.result.status == "resolved"]
    evidence_completeness = (
        sum(case.evidence_completeness for case in resolved) / len(resolved)
        if resolved
        else 0.0
    )
    passed = (
        precision == 1.0
        and cross_scope_violations == 0
        and evidence_completeness == 1.0
    )
    return ResolutionEvalReport(
        corpus_version=corpus.corpus_version,
        cases=cases,
        precision=precision,
        recall=recall,
        abstention_rate=abstention_rate,
        cross_scope_violations=cross_scope_violations,
        evidence_completeness=evidence_completeness,
        passed=passed,
    )


def _score_case(
    example: ResolutionExample,
    result: ResolveResult,
) -> ResolutionCaseScore:
    correct = (
        result.status == "resolved"
        and example.truth_canonical_id is not None
        and result.canonical_id == example.truth_canonical_id
    )
    false_positive = result.status == "resolved" and not correct
    false_negative = example.truth_canonical_id is not None and not correct
    cross_scope = (
        result.status == "resolved"
        and result.candidate_account_scope != example.account_scope
    )
    return ResolutionCaseScore(
        id=example.id,
        truth_canonical_id=example.truth_canonical_id,
        result=result,
        true_positive=correct,
        false_positive=false_positive,
        false_negative=false_negative,
        cross_scope_violation=cross_scope,
        evidence_completeness=_evidence_completeness(result),
    )


def _evidence_completeness(result: ResolveResult) -> float:
    checks = (
        result.score is not None,
        result.margin is not None,
        result.evidence is not None and bool(result.evidence.profile),
        result.evidence is not None and bool(result.evidence.field_scores),
        result.evidence is not None and 0 <= result.evidence.vector_score <= 1,
    )
    return sum(checks) / len(checks)


def _parse_backend(raw: Mapping[str, Any], example_id: str) -> BackendResolveResult:
    evidence_raw = raw.get("evidence")
    evidence = None
    if evidence_raw is not None:
        if not isinstance(evidence_raw, Mapping):
            raise ResolutionCorpusError(f"{example_id}.backend.evidence must be an object")
        field_values = evidence_raw.get("field_scores")
        if not isinstance(field_values, list):
            raise ResolutionCorpusError(
                f"{example_id}.backend.evidence.field_scores must be an array"
            )
        evidence = ResolveEvidence(
            profile=_required_string(
                evidence_raw.get("profile"),
                f"{example_id}.backend.evidence.profile",
            ),
            candidate_count=_required_int(
                evidence_raw.get("candidate_count"),
                f"{example_id}.backend.evidence.candidate_count",
            ),
            field_scores=tuple(
                FieldEvidence(
                    field=_required_string(
                        field.get("field") if isinstance(field, Mapping) else None,
                        f"{example_id}.backend.evidence.field",
                    ),
                    similarity=_required_float(
                        field.get("similarity"),
                        f"{example_id}.backend.evidence.field.similarity",
                    ),
                    weight=_required_float(
                        field.get("weight"),
                        f"{example_id}.backend.evidence.field.weight",
                    ),
                )
                for field in field_values
                if isinstance(field, Mapping)
            ),
            vector_score=_required_float(
                evidence_raw.get("vector_score"),
                f"{example_id}.backend.evidence.vector_score",
            ),
        )
    return BackendResolveResult(
        status=_required_string(raw.get("status"), f"{example_id}.backend.status"),
        canonical_id=_optional_string(raw.get("canonical_id")),
        reason=_required_string(raw.get("reason"), f"{example_id}.backend.reason"),
        score=_optional_float(raw.get("score")),
        margin=_optional_float(raw.get("margin")),
        evidence=evidence,
        candidate_account_scope=_optional_string(raw.get("candidate_account_scope")),
    )


def _required_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResolutionCorpusError(f"{location} must be a non-empty string")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ResolutionCorpusError("expected string or null")
    return value


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _required_float(value: Any, location: str) -> float:
    if value is None:
        raise ResolutionCorpusError(f"{location} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ResolutionCorpusError(f"{location} must be a number") from exc


def _required_int(value: Any, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResolutionCorpusError(f"{location} must be an integer")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the CDF resolution precision gate")
    parser.add_argument("--corpus", help="Resolution corpus JSON")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    corpus = load_resolution_corpus(args.corpus)
    backend = CorpusFakeResolver(corpus)
    report = evaluate_resolution_corpus(corpus, backend)
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(
            "resolution-eval: "
            f"precision={report.precision:.3f} recall={report.recall:.3f} "
            f"abstention={report.abstention_rate:.3f} "
            f"cross_scope={report.cross_scope_violations} "
            f"evidence={report.evidence_completeness:.3f}"
        )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

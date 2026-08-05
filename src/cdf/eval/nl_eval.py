"""Offline NL ingress evaluation harness and command-line entry point.

The default run exercises deterministic corpus routing and the real SPARQL
partitioner without a provider or API key. Tests and local experiments can
disable deterministic routing and inject a fixture client from JSON.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rdflib.plugins.sparql import prepareQuery

from cdf.query.catalog import SourceCatalog
from cdf.query.nl import nl_to_sparql
from cdf.query.planner import partition_query

from .nl_corpus import (
    CorpusExample,
    DeterministicCorpusRouter,
    FewShotRetriever,
    LexicalFewShotRetriever,
    NlCorpus,
    load_nl_corpus,
    normalize_question,
)


@dataclass(frozen=True)
class NlCaseScore:
    """Dimension-level result for one corpus example."""

    id: str
    question: str
    parse_valid: bool | None
    partition_valid: bool | None
    sources_correct: bool
    join_keys_correct: bool
    refusal_correct: bool
    refusal_reason_correct: bool
    path_correct: bool
    expected_sources: tuple[str, ...]
    actual_sources: tuple[str, ...]
    expected_join_keys: tuple[str, ...]
    actual_join_keys: tuple[str, ...]
    expected_path: str
    actual_path: str
    passed: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class NlEvalReport:
    """Aggregate offline evaluation report."""

    corpus_version: str
    cases: tuple[NlCaseScore, ...]

    @property
    def passed(self) -> int:
        return sum(case.passed for case in self.cases)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def all_passed(self) -> bool:
        return self.passed == self.total


def _score_case(
    example: CorpusExample,
    catalog: SourceCatalog,
    *,
    client: Any | None,
    router: DeterministicCorpusRouter | None,
    retriever: FewShotRetriever | None,
    few_shot_top_k: int,
) -> NlCaseScore:
    sparql: str | None = None
    refused = False
    refusal_reason: str | None = None
    actual_path = "refuse"
    errors: list[str] = []

    route = router.route(example.question) if router is not None else None
    if route is not None:
        actual_path = "deterministic"
        sparql = route.sparql
        refused = route.refusal
        refusal_reason = route.refusal_reason
    elif client is not None:
        actual_path = "llm"
        result = nl_to_sparql(
            example.question,
            catalog,
            client=client,
            few_shot_retriever=retriever,
            few_shot_top_k=few_shot_top_k,
        )
        sparql = result.sparql if result.ok else None
        refused = not result.ok
        refusal_reason = result.error
    else:
        refused = True
        refusal_reason = "no deterministic route or fixture client"

    parse_valid: bool | None = None
    partition_valid: bool | None = None
    actual_sources: tuple[str, ...] = ()
    actual_join_keys: tuple[str, ...] = ()
    if sparql is not None:
        try:
            prepareQuery(sparql)
            parse_valid = True
        except Exception as exc:  # noqa: BLE001 - evaluation records parser failures
            parse_valid = False
            errors.append(f"parse: {type(exc).__name__}: {exc}")
        if parse_valid:
            try:
                plan = partition_query(sparql, catalog)
                partition_valid = bool(plan.sub_queries) and not plan.unresolved
                actual_sources = tuple(sorted(source.source_id for source in plan.sources))
                actual_join_keys = tuple(sorted(plan.join_keys))
                if plan.unresolved:
                    errors.append(f"partition: {len(plan.unresolved)} unresolved pattern(s)")
                if not plan.sub_queries:
                    errors.append("partition: no source legs")
            except Exception as exc:  # noqa: BLE001 - evaluation records planner failures
                partition_valid = False
                errors.append(f"partition: {type(exc).__name__}: {exc}")
        else:
            partition_valid = False

    refusal_correct = refused == example.refusal
    reason_correct = all(
        refusal_reason and expected.casefold() in refusal_reason.casefold()
        for expected in example.refusal_reason_contains
    )
    sources_correct = actual_sources == tuple(sorted(example.expected_sources))
    join_keys_correct = actual_join_keys == tuple(sorted(example.expected_join_keys))
    path_correct = actual_path == example.expected_path
    query_dimensions = (
        example.refusal
        or (
            parse_valid is True
            and partition_valid is True
            and sources_correct
            and join_keys_correct
        )
    )
    passed = (
        refusal_correct
        and reason_correct
        and path_correct
        and query_dimensions
    )
    return NlCaseScore(
        id=example.id,
        question=example.question,
        parse_valid=parse_valid,
        partition_valid=partition_valid,
        sources_correct=sources_correct,
        join_keys_correct=join_keys_correct,
        refusal_correct=refusal_correct,
        refusal_reason_correct=reason_correct,
        path_correct=path_correct,
        expected_sources=tuple(sorted(example.expected_sources)),
        actual_sources=actual_sources,
        expected_join_keys=tuple(sorted(example.expected_join_keys)),
        actual_join_keys=actual_join_keys,
        expected_path=example.expected_path,
        actual_path=actual_path,
        passed=passed,
        errors=tuple(errors),
    )


def evaluate_nl_corpus(
    corpus: NlCorpus,
    catalog: SourceCatalog,
    *,
    client: Any | None = None,
    deterministic: bool = True,
    few_shot_retriever: FewShotRetriever | None = None,
    few_shot_top_k: int = 3,
) -> NlEvalReport:
    """Score corpus questions without source execution or live-provider access."""
    router = DeterministicCorpusRouter(corpus) if deterministic else None
    retriever = few_shot_retriever or LexicalFewShotRetriever(corpus)
    return NlEvalReport(
        corpus_version=corpus.corpus_version,
        cases=tuple(
            _score_case(
                example,
                catalog,
                client=client,
                router=router,
                retriever=retriever,
                few_shot_top_k=few_shot_top_k,
            )
            for example in corpus.examples
        ),
    )


class _FixtureClient:
    """Question-keyed fake client used by the CLI's ``--responses`` option."""

    provider = "fixture"
    model = "fixture"

    def __init__(self, responses: Mapping[str, str]) -> None:
        self._responses = {
            normalize_question(question): response for question, response in responses.items()
        }

    def generate(self, messages: Sequence[Mapping[str, str]]) -> Any:
        original_question = next(
            message["content"] for message in messages if message.get("role") == "user"
        )
        content = self._responses.get(normalize_question(original_question), "")
        return type(
            "FixtureResponse",
            (),
            {
                "content": content,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
            },
        )()


def _load_catalog(csi_dir: str | Path) -> SourceCatalog:
    documents = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path(csi_dir).glob("*.json"))
    ]
    if not documents:
        raise ValueError(f"no CSI documents found in {csi_dir}")
    return SourceCatalog.from_csi_documents(documents)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the offline CDF NL evaluation corpus")
    parser.add_argument("--corpus", help="NL corpus JSON (default: packaged v1 corpus)")
    parser.add_argument("--csi-dir", default="deploy/csi", help="CSI documents for partitioning")
    parser.add_argument(
        "--responses",
        help="JSON object of question -> fake LLM response; never calls a live provider",
    )
    parser.add_argument(
        "--no-deterministic",
        action="store_true",
        help="Bypass exact corpus routing to exercise a fixture client",
    )
    parser.add_argument("--few-shot-top-k", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.few_shot_top_k < 0:
        raise SystemExit("--few-shot-top-k must be non-negative")
    corpus = load_nl_corpus(args.corpus)
    catalog = _load_catalog(args.csi_dir)
    client = None
    if args.responses:
        raw = json.loads(Path(args.responses).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
        ):
            raise ValueError("--responses must contain a JSON object of string responses")
        client = _FixtureClient(raw)
    report = evaluate_nl_corpus(
        corpus,
        catalog,
        client=client,
        deterministic=not args.no_deterministic,
        few_shot_top_k=args.few_shot_top_k,
    )
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        for case in report.cases:
            mark = "PASS" if case.passed else "FAIL"
            print(f"{mark}  {case.id}  path={case.actual_path}")
            for error in case.errors:
                print(f"      - {error}")
        print(f"\nnl-eval: {report.passed}/{report.total} passed")
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

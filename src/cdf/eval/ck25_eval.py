"""Metered multi-repetition wrapper for the external CK25 NL→SPARQL benchmark.

CK25 is vendored by the owned ``arango-sparql-py`` sibling under CC BY 4.0.
That repository owns the execution-based answer-set judge. This module reuses
the judge without copying its 27k-triple dataset, adding repetition-level token,
cost, latency, corpus-revision, and tamper-evident report metadata for CDF.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import median
from types import ModuleType
from typing import Any

from cdf.service.metering import MeteredLLMClient

SCHEMA_VERSION = 1
CORPUS_NAME = "CK25"
CORPUS_LICENSE = "CC BY 4.0"
UPSTREAM_COMMIT = "cb928b2f201e4bdbbde9a1cd0653152779736395"
DEFAULT_CONFIG = "openai-gpt4o-mini-ck25"


@dataclass(frozen=True)
class Ck25CaseRun:
    name: str
    passed: bool
    elapsed_ms: float
    judge_note: str | None
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float | None


@dataclass(frozen=True)
class Ck25Repetition:
    repetition: int
    started_at: str
    passed: int
    total: int
    pass_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float | None
    cases: tuple[Ck25CaseRun, ...]


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_metadata(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ("git", "-C", str(root), *args),
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    return {
        "commit": run("rev-parse", "HEAD") or None,
        "dirty": bool(run("status", "--porcelain")),
        "benchmark_paths_dirty": bool(
            run(
                "status",
                "--porcelain",
                "--",
                "arango_sparql/nl2sparql",
                "tests/helpers/oxi.py",
                "tests/nl2sparql/eval",
            )
        ),
    }


def _dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for distribution in ("arango-sparql-py", "arango-query-core", "pyoxigraph"):
        try:
            versions[distribution] = version(distribution)
        except PackageNotFoundError:
            versions[distribution] = None
    return versions


def _load_runner(library_root: Path) -> tuple[ModuleType, Path]:
    eval_dir = library_root / "tests/nl2sparql/eval"
    runner_path = eval_dir / "runner.py"
    corpus_path = eval_dir / "vendored/ck25/corpus.yml"
    notice_path = eval_dir / "vendored/ck25/NOTICE.md"
    for path in (runner_path, corpus_path, notice_path):
        if not path.is_file():
            raise FileNotFoundError(f"required CK25 asset is missing: {path}")
    notice = notice_path.read_text(encoding="utf-8")
    if CORPUS_LICENSE not in notice or UPSTREAM_COMMIT not in notice:
        raise ValueError("CK25 attribution notice is missing pinned license/provenance")
    spec = importlib.util.spec_from_file_location("cdf_ck25_owned_runner", runner_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import CK25 runner from {runner_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, corpus_path


def run_repetition(
    runner: ModuleType,
    *,
    config: str,
    repetition: int,
) -> Ck25Repetition:
    """Run one 49-case provider sweep with a fresh meter per corpus case."""

    runner_api: Any = runner
    original_factory = runner_api._client_for
    meters: list[MeteredLLMClient] = []

    def metered_factory(config_document: dict[str, Any], case: dict[str, Any]) -> Any:
        meter = MeteredLLMClient(original_factory(config_document, case))
        meters.append(meter)
        return meter

    runner_api._client_for = metered_factory
    try:
        started_at = datetime.now(timezone.utc).isoformat()
        report = runner.run(config)
    finally:
        runner_api._client_for = original_factory
    if len(report.cases) != 49:
        raise ValueError(f"CK25 must contain 49 cases, got {len(report.cases)}")
    if len(meters) != len(report.cases):
        raise RuntimeError(
            f"meter/client count mismatch: {len(meters)} != {len(report.cases)}"
        )
    cases = []
    for result, meter in zip(report.cases, meters, strict=True):
        metrics = meter.metrics()
        cases.append(
            Ck25CaseRun(
                name=result.name,
                passed=result.passed,
                elapsed_ms=result.elapsed_ms,
                judge_note=result.judge_note,
                llm_calls=metrics.llm_calls,
                prompt_tokens=metrics.prompt_tokens,
                completion_tokens=metrics.completion_tokens,
                cached_tokens=metrics.cached_tokens,
                cost_usd=metrics.cost_usd,
            )
        )
    latencies = [case.elapsed_ms for case in cases]
    known_costs = [case.cost_usd for case in cases]
    passed = sum(case.passed for case in cases)
    return Ck25Repetition(
        repetition=repetition,
        started_at=started_at,
        passed=passed,
        total=len(cases),
        pass_rate=passed / len(cases),
        latency_p50_ms=median(latencies),
        latency_p95_ms=_percentile(latencies, 0.95),
        llm_calls=sum(case.llm_calls for case in cases),
        prompt_tokens=sum(case.prompt_tokens for case in cases),
        completion_tokens=sum(case.completion_tokens for case in cases),
        cached_tokens=sum(case.cached_tokens for case in cases),
        cost_usd=(
            sum(cost for cost in known_costs if cost is not None)
            if all(cost is not None for cost in known_costs)
            else None
        ),
        cases=tuple(cases),
    )


def _case_aggregates(runs: Sequence[Ck25Repetition]) -> list[dict[str, Any]]:
    names = tuple(case.name for case in runs[0].cases)
    if any(tuple(case.name for case in run.cases) != names for run in runs):
        raise ValueError("CK25 case ordering changed between repetitions")
    aggregates = []
    for index, name in enumerate(names):
        cases = [run.cases[index] for run in runs]
        known_costs = [case.cost_usd for case in cases]
        pass_count = sum(case.passed for case in cases)
        latencies = [case.elapsed_ms for case in cases]
        aggregates.append(
            {
                "name": name,
                "pass_count": pass_count,
                "repetitions": len(cases),
                "pass_rate": pass_count / len(cases),
                "latency_p50_ms": median(latencies),
                "latency_p95_ms": _percentile(latencies, 0.95),
                "llm_calls": sum(case.llm_calls for case in cases),
                "prompt_tokens": sum(case.prompt_tokens for case in cases),
                "completion_tokens": sum(case.completion_tokens for case in cases),
                "cached_tokens": sum(case.cached_tokens for case in cases),
                "cost_usd": (
                    sum(cost for cost in known_costs if cost is not None)
                    if all(cost is not None for cost in known_costs)
                    else None
                ),
            }
        )
    return aggregates


def build_evidence(
    *,
    library_root: Path,
    corpus_path: Path,
    config: str,
    requested_repetitions: int,
    runs: Sequence[Ck25Repetition],
) -> dict[str, Any]:
    """Build a JSON-safe, hashed evidence document, including partial runs."""

    all_cases = [case for run in runs for case in run.cases]
    latencies = [case.elapsed_ms for case in all_cases]
    known_costs = [run.cost_usd for run in runs]
    completed = len(runs)
    passed = sum(run.passed for run in runs)
    total = sum(run.total for run in runs)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "cdf-ck25-live-evidence",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "complete": completed == requested_repetitions,
        "config": config,
        "model": "gpt-4o-mini",
        "temperature": 0.1,
        "judge": "execution answer-set comparison",
        "refusal_cases": 0,
        "requested_repetitions": requested_repetitions,
        "completed_repetitions": completed,
        "total_case_evaluations": total,
        "passed": passed,
        "pass_rate": passed / total if total else None,
        "latency_p50_ms": median(latencies) if latencies else None,
        "latency_p95_ms": _percentile(latencies, 0.95) if latencies else None,
        "llm_calls": sum(run.llm_calls for run in runs),
        "prompt_tokens": sum(run.prompt_tokens for run in runs),
        "completion_tokens": sum(run.completion_tokens for run in runs),
        "cached_tokens": sum(run.cached_tokens for run in runs),
        "cost_usd": (
            sum(cost for cost in known_costs if cost is not None)
            if runs and all(cost is not None for cost in known_costs)
            else None
        ),
        "corpus": {
            "name": CORPUS_NAME,
            "case_count": 49,
            "license": CORPUS_LICENSE,
            "source": "https://github.com/eccenca/ck25-dataset",
            "upstream_commit": UPSTREAM_COMMIT,
            "sha256": _sha256(corpus_path),
        },
        "owned_harness": {
            "repository": "arango-sparql-py",
            **_git_metadata(library_root),
        },
        "dependencies": _dependency_versions(),
        "runs": [asdict(run) for run in runs],
        "cases": _case_aggregates(runs) if runs else [],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    payload["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def _run_from_dict(document: dict[str, Any]) -> Ck25Repetition:
    return Ck25Repetition(
        repetition=document["repetition"],
        started_at=document["started_at"],
        passed=document["passed"],
        total=document["total"],
        pass_rate=document["pass_rate"],
        latency_p50_ms=document["latency_p50_ms"],
        latency_p95_ms=document["latency_p95_ms"],
        llm_calls=document["llm_calls"],
        prompt_tokens=document["prompt_tokens"],
        completion_tokens=document["completion_tokens"],
        cached_tokens=document["cached_tokens"],
        cost_usd=document.get("cost_usd"),
        cases=tuple(Ck25CaseRun(**case) for case in document["cases"]),
    )


def _write_evidence(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_evidence(path: str | Path) -> dict[str, Any]:
    """Validate a completed report without invoking a provider."""

    document = json.loads(Path(path).read_text(encoding="utf-8"))
    digest = document.pop("report_sha256", None)
    canonical = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    errors = []
    if digest != hashlib.sha256(canonical).hexdigest():
        errors.append("report_sha256")
    if document.get("kind") != "cdf-ck25-live-evidence":
        errors.append("kind")
    if not document.get("complete"):
        errors.append("complete")
    if document.get("completed_repetitions", 0) < 3:
        errors.append("completed_repetitions")
    if document.get("total_case_evaluations", 0) < 147:
        errors.append("total_case_evaluations")
    if (document.get("corpus") or {}).get("case_count") != 49:
        errors.append("corpus.case_count")
    if document.get("prompt_tokens", 0) <= 0:
        errors.append("prompt_tokens")
    if document.get("completion_tokens", 0) <= 0:
        errors.append("completion_tokens")
    if document.get("cost_usd") is None:
        errors.append("cost_usd")
    return {
        "valid": not errors,
        "errors": errors,
        "model": document.get("model"),
        "completed_repetitions": document.get("completed_repetitions"),
        "total_case_evaluations": document.get("total_case_evaluations"),
        "passed": document.get("passed"),
        "pass_rate": document.get("pass_rate"),
        "latency_p50_ms": document.get("latency_p50_ms"),
        "latency_p95_ms": document.get("latency_p95_ms"),
        "llm_calls": document.get("llm_calls"),
        "prompt_tokens": document.get("prompt_tokens"),
        "completion_tokens": document.get("completion_tokens"),
        "cost_usd": document.get("cost_usd"),
        "report_sha256": digest,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run metered repetitions of the owned CK25 execution benchmark",
    )
    parser.add_argument(
        "--library-root",
        default=os.getenv(
            "CDF_ARANGO_SPARQL_ROOT",
            str(Path.cwd().parent / "arango-sparql-py"),
        ),
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output")
    parser.add_argument(
        "--validate-evidence",
        help="validate an existing report instead of calling a provider",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="continue compatible completed repetitions from --output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.validate_evidence:
        summary = validate_evidence(args.validate_evidence)
        print(json.dumps(summary, indent=2))
        return 0 if summary["valid"] else 1
    if not args.output:
        raise SystemExit("--output is required for a live run")
    if args.repetitions <= 0:
        raise SystemExit("--repetitions must be positive")
    library_root = Path(args.library_root).resolve()
    output = Path(args.output)
    runner, corpus_path = _load_runner(library_root)
    runs: list[Ck25Repetition] = []
    if args.resume and output.is_file():
        existing = json.loads(output.read_text(encoding="utf-8"))
        if (
            existing.get("kind") != "cdf-ck25-live-evidence"
            or existing.get("config") != args.config
            or existing.get("requested_repetitions") != args.repetitions
            or (existing.get("corpus") or {}).get("sha256") != _sha256(corpus_path)
        ):
            raise ValueError("existing CK25 evidence is incompatible with this run")
        runs.extend(_run_from_dict(run) for run in existing.get("runs") or [])
    for repetition in range(len(runs) + 1, args.repetitions + 1):
        runs.append(run_repetition(runner, config=args.config, repetition=repetition))
        evidence = build_evidence(
            library_root=library_root,
            corpus_path=corpus_path,
            config=args.config,
            requested_repetitions=args.repetitions,
            runs=runs,
        )
        _write_evidence(output, evidence)
        print(
            f"CK25 repetition {repetition}/{args.repetitions}: "
            f"{runs[-1].passed}/{runs[-1].total} passed"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

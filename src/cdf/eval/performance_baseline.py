"""Disclosed synthetic planning, latency, and concurrency baseline.

This is an internal repeatability benchmark, not a public scale claim. It uses
fixed CSI cardinalities and deterministic fixture results under two declared
network-delay profiles so regressions in planning and orchestration can be
tracked without depending on cloud noise or credentials.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

from cdf.query import SourceCatalog, SourceResult, estimate_plan, partition_query
from cdf.service import FederationService

SCHEMA_VERSION = 1
WORKLOAD_VERSION = "synthetic-federation-v1"
QUERY = """PREFIX c: <urn:arango-sparql:concept#>
SELECT ?accountName ?queryVolume ?source WHERE {
  ?account a c:PerfAccount ; c:accountId ?account_id ; c:accountName ?accountName .
  ?usage a c:PerfUsage ; c:accountId ?account_id ; c:queryVolume ?queryVolume .
  ?document a c:PerfDocument ; c:accountId ?account_id ; c:source ?source .
}"""


@dataclass(frozen=True)
class NetworkProfile:
    name: str
    source_delay_ms: float


@dataclass(frozen=True)
class LatencySummary:
    samples: int
    min_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float


@dataclass(frozen=True)
class PerformanceProfileResult:
    profile: str
    source_delay_ms: float
    sequential_requests: int
    concurrent_workers: int
    concurrent_requests: int
    planning_latency: LatencySummary
    request_latency: LatencySummary
    concurrent_wall_ms: float
    throughput_qps: float
    result_rows: int
    source_rows_returned: int
    bytes_processed: int
    cost_usd: float
    statuses: tuple[str, ...]
    passed: bool


@dataclass(frozen=True)
class PerformanceBaselineReport:
    schema_version: int
    workload_version: str
    evidence_class: str
    dataset: dict[str, dict[str, int | float]]
    query_source_count: int
    profiles: tuple[PerformanceProfileResult, ...]
    all_passed: bool


PROFILES = (
    NetworkProfile("in-process", 0.0),
    NetworkProfile("simulated-lan", 2.0),
)
DATASET = {
    "postgresql:perf-accounts": {
        "estimated_rows": 10_000,
        "estimated_bytes": 1_600_000,
        "cost_per_gb_usd": 0.02,
    },
    "snowflake:perf-usage": {
        "estimated_rows": 1_000,
        "estimated_bytes": 96_000,
        "cost_per_gb_usd": 0.04,
    },
    "arango:perf-documents": {
        "estimated_rows": 100,
        "estimated_bytes": 24_000,
        "cost_per_gb_usd": 0.03,
    },
}


def _csi(
    kind: str,
    ref: str,
    entity: str,
    properties: tuple[str, ...],
    *,
    rows: int,
    estimated_bytes: int,
    cost_per_gb_usd: float,
) -> dict[str, Any]:
    return {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {
                    "name": entity,
                    "properties": [{"name": name} for name in properties],
                }
            ]
        },
        "provenance": {
            "producer": "performance-baseline",
            "direction": "forward",
            "source": {"kind": kind, "ref": ref},
        },
        "statistics": {
            "version": "1",
            "snapshotId": f"{ref}-{WORKLOAD_VERSION}",
            "asOf": "2026-08-05T00:00:00Z",
            "source": {
                "rowCount": rows,
                "estimatedBytes": estimated_bytes,
                "costPerGbUsd": cost_per_gb_usd,
            },
            "classes": {
                entity: {
                    "rowCount": rows,
                    "estimatedBytes": estimated_bytes,
                    "properties": {"accountId": {"ndv": rows}},
                }
            },
        },
    }


def _catalog() -> SourceCatalog:
    return SourceCatalog.from_csi_documents(
        [
            _csi(
                "postgresql",
                "perf-accounts",
                "PerfAccount",
                ("accountId", "accountName"),
                rows=10_000,
                estimated_bytes=1_600_000,
                cost_per_gb_usd=0.02,
            ),
            _csi(
                "snowflake",
                "perf-usage",
                "PerfUsage",
                ("accountId", "queryVolume"),
                rows=1_000,
                estimated_bytes=96_000,
                cost_per_gb_usd=0.04,
            ),
            _csi(
                "arango",
                "perf-documents",
                "PerfDocument",
                ("accountId", "source"),
                rows=100,
                estimated_bytes=24_000,
                cost_per_gb_usd=0.03,
            ),
        ]
    )


class _PerformanceExecutor:
    def __init__(
        self,
        rows: tuple[dict[str, Any], ...],
        *,
        delay_ms: float,
        bytes_processed: int,
        cost_usd: float,
    ) -> None:
        self.rows = rows
        self.delay_seconds = delay_ms / 1000
        self.bytes_processed = bytes_processed
        self.cost_usd = cost_usd

    def execute(self, _subquery: Any) -> SourceResult:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return SourceResult(
            rows=self.rows,
            bytes_processed=self.bytes_processed,
            cost_usd=self.cost_usd,
            as_of="2026-08-05T00:00:00Z",
        )


def _executors(delay_ms: float) -> dict[str, _PerformanceExecutor]:
    account_ids = tuple(f"A{index:03d}" for index in range(25))
    return {
        "postgresql:perf-accounts": _PerformanceExecutor(
            tuple(
                {"account_id": account_id, "accountName": f"Account {index}"}
                for index, account_id in enumerate(account_ids)
            ),
            delay_ms=delay_ms,
            bytes_processed=4_000,
            cost_usd=0.000_000_08,
        ),
        "snowflake:perf-usage": _PerformanceExecutor(
            tuple(
                {"account_id": account_id, "queryVolume": float(index)}
                for index, account_id in enumerate(account_ids)
            ),
            delay_ms=delay_ms,
            bytes_processed=2_400,
            cost_usd=0.000_000_096,
        ),
        "arango:perf-documents": _PerformanceExecutor(
            tuple(
                {"account_id": account_id, "source": "benchmark"}
                for account_id in account_ids
            ),
            delay_ms=delay_ms,
            bytes_processed=6_000,
            cost_usd=0.000_000_18,
        ),
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _latency_summary(values: list[float]) -> LatencySummary:
    return LatencySummary(
        samples=len(values),
        min_ms=min(values),
        p50_ms=median(values),
        p95_ms=_percentile(values, 0.95),
        max_ms=max(values),
    )


def _planning_samples(
    catalog: SourceCatalog,
    iterations: int,
) -> LatencySummary:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        estimate_plan(partition_query(QUERY, catalog), catalog)
        samples.append((time.perf_counter() - started) * 1000)
    return _latency_summary(samples)


def _one_request(service: FederationService) -> tuple[float, Any]:
    started = time.perf_counter()
    envelope = service.federate_sparql(QUERY)
    return (time.perf_counter() - started) * 1000, envelope


def evaluate_profile(
    profile: NetworkProfile,
    *,
    planning_iterations: int,
    sequential_requests: int,
    concurrent_workers: int,
    requests_per_worker: int,
) -> PerformanceProfileResult:
    catalog = _catalog()
    service = FederationService(
        catalog=catalog,
        executors=_executors(profile.source_delay_ms),
    )
    planning = _planning_samples(catalog, planning_iterations)
    sequential = [_one_request(service) for _ in range(sequential_requests)]
    concurrent_requests = concurrent_workers * requests_per_worker
    concurrent_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrent_workers) as pool:
        concurrent = list(
            pool.map(lambda _index: _one_request(service), range(concurrent_requests))
        )
    concurrent_wall_ms = (time.perf_counter() - concurrent_started) * 1000
    envelopes = [envelope for _, envelope in (*sequential, *concurrent)]
    representative = envelopes[-1]
    metrics = representative.execution_metrics
    if metrics is None:
        raise AssertionError("performance workload did not emit execution metrics")
    statuses = tuple(sorted({envelope.status for envelope in envelopes}))
    passed = (
        statuses == ("grounded",)
        and len(representative.bindings) == 25
        and metrics.bytes_processed == 12_400
        and metrics.cost_usd is not None
    )
    return PerformanceProfileResult(
        profile=profile.name,
        source_delay_ms=profile.source_delay_ms,
        sequential_requests=sequential_requests,
        concurrent_workers=concurrent_workers,
        concurrent_requests=concurrent_requests,
        planning_latency=planning,
        request_latency=_latency_summary([latency for latency, _ in sequential]),
        concurrent_wall_ms=concurrent_wall_ms,
        throughput_qps=concurrent_requests / (concurrent_wall_ms / 1000),
        result_rows=len(representative.bindings),
        source_rows_returned=sum(leg.row_count for leg in metrics.legs),
        bytes_processed=metrics.bytes_processed,
        cost_usd=metrics.cost_usd,
        statuses=statuses,
        passed=passed,
    )


def run_performance_baseline(
    *,
    planning_iterations: int = 100,
    sequential_requests: int = 20,
    concurrent_workers: int = 4,
    requests_per_worker: int = 10,
) -> PerformanceBaselineReport:
    """Run the fixed synthetic workload under every declared network profile."""

    parameters = (
        planning_iterations,
        sequential_requests,
        concurrent_workers,
        requests_per_worker,
    )
    if any(value <= 0 for value in parameters):
        raise ValueError("benchmark iteration and concurrency values must be positive")
    profiles = tuple(
        evaluate_profile(
            profile,
            planning_iterations=planning_iterations,
            sequential_requests=sequential_requests,
            concurrent_workers=concurrent_workers,
            requests_per_worker=requests_per_worker,
        )
        for profile in PROFILES
    )
    return PerformanceBaselineReport(
        schema_version=SCHEMA_VERSION,
        workload_version=WORKLOAD_VERSION,
        evidence_class="internal synthetic; not a public scale or competitor claim",
        dataset=DATASET,
        query_source_count=3,
        profiles=profiles,
        all_passed=all(profile.passed for profile in profiles),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the disclosed synthetic CDF performance baseline",
    )
    parser.add_argument("--planning-iterations", type=int, default=100)
    parser.add_argument("--sequential-requests", type=int, default=20)
    parser.add_argument("--concurrent-workers", type=int, default=4)
    parser.add_argument("--requests-per-worker", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_performance_baseline(
        planning_iterations=args.planning_iterations,
        sequential_requests=args.sequential_requests,
        concurrent_workers=args.concurrent_workers,
        requests_per_worker=args.requests_per_worker,
    )
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        for profile in report.profiles:
            print(
                f"PASS  {profile.profile}  "
                f"request p50={profile.request_latency.p50_ms:.3f}ms "
                f"p95={profile.request_latency.p95_ms:.3f}ms "
                f"throughput={profile.throughput_qps:.1f}qps"
            )
        print(f"\nperformance-baseline: {'passed' if report.all_passed else 'failed'}")
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

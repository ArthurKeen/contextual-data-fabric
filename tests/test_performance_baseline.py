"""Disclosed internal performance baseline contracts."""

from __future__ import annotations

from cdf.eval.performance_baseline import run_performance_baseline


def test_synthetic_performance_baseline_reports_required_dimensions() -> None:
    report = run_performance_baseline(
        planning_iterations=5,
        sequential_requests=2,
        concurrent_workers=2,
        requests_per_worker=2,
    )

    assert report.all_passed
    assert report.workload_version == "synthetic-federation-v1"
    assert report.query_source_count == 3
    assert set(report.dataset) == {
        "postgresql:perf-accounts",
        "snowflake:perf-usage",
        "arango:perf-documents",
    }
    assert {profile.profile for profile in report.profiles} == {
        "in-process",
        "simulated-lan",
    }
    for profile in report.profiles:
        assert profile.planning_latency.samples == 5
        assert profile.request_latency.samples == 2
        assert profile.request_latency.p95_ms >= profile.request_latency.p50_ms
        assert profile.concurrent_workers == 2
        assert profile.concurrent_requests == 4
        assert profile.throughput_qps > 0
        assert profile.result_rows == 25
        assert profile.source_rows_returned == 75
        assert profile.bytes_processed == 12_400
        assert profile.cost_usd > 0

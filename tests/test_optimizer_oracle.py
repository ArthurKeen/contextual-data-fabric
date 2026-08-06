"""Exhaustive physical-planner oracle benchmark."""

from __future__ import annotations

from cdf.eval.optimizer_oracle import (
    evaluate_optimizer_corpus,
    load_optimizer_corpus,
)


def test_optimizer_oracle_corpus_is_versioned_and_covers_dp_boundary() -> None:
    version, cases = load_optimizer_corpus()
    assert version == "optimizer-oracle-v1"
    assert len(cases) == 8
    assert max(len(case.sources) for case in cases) == 8
    assert sum(len(case.sources) >= 4 for case in cases) >= 4


def test_optimizer_matches_exhaustive_oracle_for_every_fixed_case() -> None:
    report = evaluate_optimizer_corpus()
    assert report.all_passed
    assert report.passed == report.total == 8
    assert report.total_feasible_plans == 182
    assert report.max_objective_ratio == 1.0
    assert all(case.order_optimal for case in report.cases)
    assert all(case.golden_order_match for case in report.cases)
    assert all(case.seed_directions_match for case in report.cases)
    assert all(case.estimated_bytes_match for case in report.cases)
    assert all(case.estimated_cost_match for case in report.cases)

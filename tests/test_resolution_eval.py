"""Versioned resolution corpus and deterministic precision gate."""

from __future__ import annotations

import json

from cdf.eval.resolution_eval import (
    CorpusFakeResolver,
    evaluate_resolution_corpus,
    load_resolution_corpus,
    main,
)


def test_resolution_corpus_reports_precision_recall_abstention_and_evidence() -> None:
    corpus = load_resolution_corpus()
    backend = CorpusFakeResolver(corpus)
    report = evaluate_resolution_corpus(
        corpus,
        backend,
        clock=lambda: 100.0,
    )
    assert report.corpus_version == "resolution-v1"
    assert report.precision == 1.0
    assert report.recall == 2 / 3
    assert report.abstention_rate == 3 / 8
    assert report.cross_scope_violations == 0
    assert report.evidence_completeness == 1.0
    assert report.passed
    assert "oracle-input-refused" not in backend.calls

    by_id = {case.id: case.result for case in report.cases}
    assert by_id["ambiguous-near-tie"].reason == "ambiguous_margin"
    assert by_id["cross-account-provider-breach"].reason == "cross_account_candidate"
    assert by_id["missing-canonical-id"].reason == "candidate_canonical_id_required"


def test_resolution_eval_cli_emits_machine_readable_report(capsys) -> None:
    assert main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["precision"] == 1.0
    assert payload["cross_scope_violations"] == 0
    assert payload["passed"] is True

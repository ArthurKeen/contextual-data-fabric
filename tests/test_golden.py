"""Golden seed-question regression gate (F1).

Discovers every JSON golden under ``cdf/eval/goldens`` and runs it through the
real M5 pipeline. A failing case prints the exact field-level diff.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cdf.eval import golden, load_goldens, run_golden

GOLDENS_DIR = Path(golden.__file__).parent / "goldens"
CASES = load_goldens(GOLDENS_DIR)


def test_golden_set_is_non_empty():
    # A regression gate that silently runs zero cases is a liar.
    assert CASES, f"no golden cases found under {GOLDENS_DIR}"


@pytest.mark.parametrize("case", CASES, ids=[c.get("name", "?") for c in CASES])
def test_golden_case(case):
    outcome = run_golden(case)
    assert outcome.passed, (
        f"golden {outcome.name!r} failed:\n  " + "\n  ".join(outcome.mismatches)
    )


def test_partial_failure_is_declared_never_silent() -> None:
    """The CC-5 case the old harness couldn't express: one leg down, strict
    mode -> refusal that *names* the failed leg; concierge mode -> a partial
    answer with the shortfall declared."""
    from cdf.eval.golden import run_golden

    case = {
        "name": "partial-failure",
        "question": (
            "PREFIX c: <urn:arango-sparql:concept#> SELECT ?name ?subject WHERE { "
            "?a a c:accounts ; c:account_name ?name ; c:account_id ?acct . "
            "?t a c:Ticket ; c:subject ?subject ; c:account_id ?acct . }"
        ),
        "sources": [
            {
                "csi": {
                    "csiVersion": "1",
                    "conceptualModel": {"entities": [{"name": "accounts", "properties": [
                        {"name": "account_id"}, {"name": "account_name"}]}]},
                    "physicalMapping": {"entities": {"accounts": {"tableName": "accounts"}}},
                    "provenance": {"producer": "r2g", "direction": "forward",
                                   "source": {"kind": "postgresql", "ref": "crm"}},
                },
                "data": {"rows": [{"a": "urn:1", "name": "Meridian", "acct": "001"}]},
            },
            {
                "csi": {
                    "csiVersion": "1",
                    "conceptualModel": {"entities": [{"name": "Ticket", "properties": [
                        {"name": "subject"}, {"name": "account_id"}]}]},
                    "arangoPhysicalMapping": {"entities": {"Ticket": {
                        "style": "COLLECTION", "collectionName": "tickets"}},
                        "relationships": {}},
                    "provenance": {"producer": "analyzer", "direction": "reverse",
                                   "source": {"kind": "arango", "ref": "tickets"}},
                },
                "data": {"fail": True, "error": "container stopped"},
            },
        ],
        "expect": {
            "status": "refused",
            "failed_sources": ["arango:tickets"],
            "refusal_contains": ["arango:tickets"],
        },
    }
    outcome = run_golden(case)
    assert outcome.passed, outcome.mismatches

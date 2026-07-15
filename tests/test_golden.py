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

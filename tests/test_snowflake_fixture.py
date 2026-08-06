"""Integrity checks for the deterministic hosted-CI Snowflake corpus."""

from __future__ import annotations

import json
from pathlib import Path


def test_snowflake_ci_fixture_matches_demo_join_spine() -> None:
    root = Path(__file__).resolve().parents[1] / "deploy/snowflake/fixtures"
    files = sorted(root.glob("*/snowflake/*_snowflake_usage_metrics.json"))
    assert len(files) == 3

    rows = [
        row
        for path in files
        for row in json.loads(path.read_text(encoding="utf-8"))
    ]
    assert len(rows) == 46
    assert {row["AccountId"] for row in rows} == {
        "001Qwvb5LAnzy3yVgi",
        "001bbkuFW1b7KegAZT",
        "001LxbLlyzNOfmaOHp",
    }
    assert all(
        {
            "AccountId",
            "period",
            "edition",
            "query_volume_m",
            "volume_trend",
            "is_peak_period",
        }
        <= row.keys()
        for row in rows
    )
    assert all(
        any(row["is_peak_period"] for row in rows if row["AccountId"] == account_id)
        for account_id in {row["AccountId"] for row in rows}
    )

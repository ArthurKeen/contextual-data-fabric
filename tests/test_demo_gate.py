"""Pre-demo/live-CI golden selection contracts."""

from __future__ import annotations

from cdf.eval.golden import filter_goldens


def test_partial_live_stack_excludes_only_cases_requiring_missing_source() -> None:
    cases = [
        {"name": "local", "expect": {"sources_touched": ["postgresql:crm"]}},
        {
            "name": "cloud",
            "expect": {
                "sources_touched": ["postgresql:crm", "snowflake:telemetry"]
            },
        },
        {"name": "refusal", "expect": {"status": "refused"}},
    ]

    selected, skipped = filter_goldens(cases, ["snowflake:telemetry"])

    assert [case["name"] for case in selected] == ["local", "refusal"]
    assert [case["name"] for case in skipped] == ["cloud"]

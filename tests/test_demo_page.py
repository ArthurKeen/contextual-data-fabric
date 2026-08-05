"""The demo page is a server-rendered string template (deploy/demo/server.py).

Importing the module boots FederationService.from_env(), which needs the live
stack — so these tests assert on the template source itself: the invariants
the browser UI depends on, and the suggestion-dropdown contract (prepared
questions pop up on focus instead of permanently occupying the page).
"""

from pathlib import Path

import pytest

PAGE_SOURCE = (Path(__file__).parent.parent / "deploy" / "demo" / "server.py").read_text()


def test_page_still_injects_questions_and_sparql() -> None:
    assert "__QUESTIONS__" in PAGE_SOURCE
    assert "__SPARQL__" in PAGE_SOURCE


def test_suggestions_are_a_dropdown_not_inline_chips() -> None:
    # The dropdown is anchored to the input and hidden until focus.
    assert 'id="suggest"' in PAGE_SOURCE
    assert 'role="combobox"' in PAGE_SOURCE
    assert 'role="listbox"' in PAGE_SOURCE
    assert 'id="clear-query"' in PAGE_SOURCE
    assert 'onclick="clearQuestion()"' in PAGE_SOURCE
    # The old always-visible chip row must not come back.
    assert 'id="examples"' not in PAGE_SOURCE
    assert '"chip"' not in PAGE_SOURCE


@pytest.mark.parametrize(
    "wiring",
    [
        "addEventListener('focus', openSuggestions)",
        "addEventListener('input', openSuggestions)",
        "addEventListener('blur', closeSuggestions)",
        "function clearQuestion()",
        "input.value = '';",
        "input.focus();",
        # mousedown (not click) so selection wins the race against the
        # input's blur closing the list — the bug this UI pattern invites.
        "item.onmousedown",
        "e.preventDefault(); pickSuggestion(q);",
    ],
)
def test_dropdown_wiring_present(wiring: str) -> None:
    assert wiring in PAGE_SOURCE


@pytest.mark.parametrize("key", ["ArrowDown", "ArrowUp", "Escape"])
def test_keyboard_navigation_handled(key: str) -> None:
    assert f"e.key === '{key}'" in PAGE_SOURCE


def test_ask_window_renders_llm_metrics() -> None:
    assert 'id="metrics"' in PAGE_SOURCE
    assert "renderMetrics(d.nl_metrics, d.execution_metrics)" in PAGE_SOURCE
    assert "LLM compute time" in PAGE_SOURCE
    assert "prompt_tokens" in PAGE_SOURCE
    assert "completion_tokens" in PAGE_SOURCE
    assert "cost_usd" in PAGE_SOURCE


def test_metrics_separate_plan_wall_time_from_source_execution() -> None:
    assert "plan wall time" in PAGE_SOURCE
    assert "execution.total_duration_ms" in PAGE_SOURCE
    assert "execution.legs" in PAGE_SOURCE
    assert "leg.duration_ms" in PAGE_SOURCE


def test_provenance_panel_renders_actual_execution_workflow() -> None:
    assert "Provenance &amp; Execution" in PAGE_SOURCE
    assert 'aria-label="Query provenance workflow"' in PAGE_SOURCE
    for step in ("Conceptual query", "Federate", "Join", "Grounded answer"):
        assert step in PAGE_SOURCE
    assert "legs.map(s =>" in PAGE_SOURCE
    assert "srcTag(s.kind, s.source_id)" in PAGE_SOURCE
    assert "s.row_count" in PAGE_SOURCE


def test_provenance_labels_and_renders_generated_postgresql_sql() -> None:
    assert "generated ${nativeLanguage(c.kind)}" in PAGE_SOURCE
    assert "kind === 'postgresql'" in PAGE_SOURCE
    assert "native query unavailable for this source" in PAGE_SOURCE
    assert "c.native_query" in PAGE_SOURCE

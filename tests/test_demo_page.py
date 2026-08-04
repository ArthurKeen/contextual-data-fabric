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
    # The old always-visible chip row must not come back.
    assert 'id="examples"' not in PAGE_SOURCE
    assert '"chip"' not in PAGE_SOURCE


@pytest.mark.parametrize(
    "wiring",
    [
        "addEventListener('focus', openSuggestions)",
        "addEventListener('input', openSuggestions)",
        "addEventListener('blur', closeSuggestions)",
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

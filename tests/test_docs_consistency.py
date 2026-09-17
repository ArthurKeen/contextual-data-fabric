"""Documentation consistency — the checks that would have caught the S1 close-out drift.

Three failure modes, one each (PR #37 review, 2026-09-16):

1. **ADR status split**: ADR-0006 said ``status: accepted`` in its frontmatter
   and ``**Status:** proposed`` twelve lines down for eight days.
2. **Append-only "Done" stamps**: a completion note was appended to a roadmap
   item while the baseline, the item's own plan text and a risk entry kept
   asserting the retired fact. ``docs/retired-claims.yaml`` is the ledger of
   statements that stopped being true; none may reappear.
3. **Resolved item, live risk**: a roadmap risk that cites a sprint item
   stamped Done must be struck through or say it is resolved.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
LEDGER = DOCS / "retired-claims.yaml"
ROADMAP = DOCS / "roadmap-2026H2.md"
ADRS = sorted(DOCS.glob("architecture/**/adr/ADR-*.md"))
SWEPT = [p for p in DOCS.rglob("*.md") if "archive" not in p.parts] + [
    ROOT / "README.md",
    ROOT / "SOP.md",
]


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), "expected YAML frontmatter"
    return yaml.safe_load(text.split("\n---\n", 1)[0][4:]) or {}


def _first_word(value: str) -> str:
    return re.split(r"[^A-Za-z]", value.strip(), maxsplit=1)[0].lower()


# ── 1. ADR status lives in two places; they must agree ──────────────────────


@pytest.mark.parametrize("adr", ADRS, ids=[p.stem for p in ADRS])
def test_adr_frontmatter_status_matches_body_status(adr: Path) -> None:
    text = adr.read_text(encoding="utf-8")
    front = str(_frontmatter(text).get("status", ""))
    body = re.search(r"^\*\*Status:\*\*\s*(.+)$", text, re.M)
    assert body, f"{adr.name}: no '**Status:**' line in the body"
    assert _first_word(front) == _first_word(body.group(1)), (
        f"{adr.name}: frontmatter status {front!r} but body says {body.group(1)!r}"
    )


# ── 2. Retired claims never come back ───────────────────────────────────────


def _ledger() -> list[dict]:
    doc = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    assert doc.get("version") == 1 and isinstance(doc.get("claims"), list)
    for entry in doc["claims"]:
        assert set(entry) >= {"pattern", "retired", "why"}, entry
        re.compile(entry["pattern"])
    return doc["claims"]


def test_retired_claims_ledger_is_well_formed() -> None:
    assert _ledger(), "the ledger exists to be used — it should not be empty"


def test_retired_claims_do_not_reappear() -> None:
    hits: list[str] = []
    for entry in _ledger():
        pattern = re.compile(entry["pattern"])
        for path in SWEPT:
            if path == LEDGER or not path.exists():
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    hits.append(
                        f"{path.relative_to(ROOT)}:{lineno}: {entry['pattern']!r} "
                        f"(retired {entry['retired']}: {entry['why']})"
                    )
    assert not hits, "retired claims reappeared:\n  " + "\n  ".join(hits)


# ── 3. A Done item may not leave its risk standing ──────────────────────────


def _numbered_items(block: str) -> dict[int, str]:
    items: dict[int, str] = {}
    current: int | None = None
    for line in block.splitlines():
        m = re.match(r"^(\d+)\. \*\*", line)
        if m:
            current = int(m.group(1))
            items[current] = line
        elif current is not None and (line.startswith("   ") or line.startswith("\t")):
            items[current] += "\n" + line
        elif line.strip() == "" or line.startswith("**") or line.startswith("#"):
            current = None
    return items


def _section(text: str, heading_regex: str) -> str:
    m = re.search(heading_regex, text, re.M)
    assert m, f"roadmap: heading {heading_regex!r} not found"
    rest = text[m.end() :]
    nxt = re.search(r"^#{2,3} ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def test_roadmap_risks_citing_done_items_are_resolved() -> None:
    text = ROADMAP.read_text(encoding="utf-8")
    risks = _numbered_items(_section(text, r"^## \d+\. Risks\s*$"))
    assert risks, "roadmap: no numbered risks parsed"
    stale: list[str] = []
    for number, risk in risks.items():
        for sprint, item_no in re.findall(r"S(\d) item #(\d+)", risk):
            items = _numbered_items(_section(text, rf"^### S{sprint} "))
            item = items.get(int(item_no), "")
            done = "**Done" in item
            # Struck through, or a DATED resolution — "until resolved" is a live risk.
            body = risk.lstrip("0123456789. ")
            resolved = body.startswith("~~") or bool(
                re.search(r"resolved \d{4}-\d{2}-\d{2}", risk, re.I)
            )
            if done and not resolved:
                stale.append(
                    f"risk {number} cites S{sprint} item #{item_no}, which is stamped Done, "
                    "but the risk is neither struck through nor marked resolved"
                )
    assert not stale, "\n".join(stale)

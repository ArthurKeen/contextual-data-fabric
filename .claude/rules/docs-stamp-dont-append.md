# Docs: Rewrite What You Retire

> **Scope:** Any edit that records a fact changing — a task stamped Done, a status flipped, a number updated, a risk closed — in `docs/`, `README.md`, `SOP.md`, PRD, roadmap, ADRs.

> **"A 'Done' stamp beside a sentence that says otherwise is a contradiction, not a record."**

Written after the S1 close-out (PR #37, 2026-09-16): item stamps were appended to the
roadmap while the baseline, the items' own plan text and a risk entry still said
Snowflake was broken; ADR-0006's frontmatter said *accepted* while its body said
*proposed* for eight days; a "12.72M rows" figure was quoted as corpus size when the
evidence file measured result rows. All three were caught by a human reviewer reading
the whole file. `tests/test_docs_consistency.py` now catches them in CI.

## When a fact changes

| Do | Not |
| --- | --- |
| **Rewrite** the sentences the new fact retires — the plan text, the baseline, the risk | Append "**Done:** …" and leave the old sentence standing |
| **Read the whole file**, then `grep -rn` the docs tree for the retired claim | Read only the section you are stamping |
| **Add the retired phrase to `docs/retired-claims.yaml`** with the date and why — a pattern on the *claim*, never the topic | Trust that nobody will paste the old wording back; ban a subject so history can't be told |
| **Quote a retired claim on purpose** (a retrospective, a plan recorded as wrong) by ending the line with `<!-- quotes-retired-claim -->` | Paraphrase the historical record into words it never used, just to clear a regex |
| **Strike or resolve the risk** that cited the item (`~~…~~ — resolved <date>`) | Leave risk entries to age |
| **Change status in every place it lives** (ADR frontmatter *and* body `**Status:**`) | Flip the frontmatter and stop |
| **Quote numbers with the measured quantity named**, read from the evidence file's field (`result_rows`, `p50_ms`), and carry its disclosure caveat | Copy a headline sentence and drop its qualifier |

## Closing a sprint

1. Verify each exit-gate item against evidence (PR, file, issue) — cite it.
2. For each item stamped Done: rewrite its plan text as history, sweep §0 Baseline and
   the Risks table, sweep the PRD/README for the claim, add ledger entries.
3. Run `pytest tests/test_docs_consistency.py` before pushing.

**Structure is memory; a stamp is not.**

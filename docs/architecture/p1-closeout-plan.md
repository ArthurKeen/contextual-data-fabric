---
title: "P1 Close-Out Plan — from proven toy to credible demo"
type:
  - internal
  - implementation-plan
status: draft
version: 0.2
date: 2026-07-17
related:
  - "[[contextual-data-fabric/docs/architecture/module-05-federated-query-engine/implementation-plan|M5 implementation plan]]"
  - "[[contextual-data-fabric-prd|PRD]] §7"
  - "[[contextual-data-fabric/docs/use-cases|Use cases]]"
---

# P1 Close-Out Plan

> **Situation.** The engine is real: E1→E2→E3→F1 code, both live legs proven
> (Ontop/Postgres + arango-sparql-py/ArangoDB), an HTTP seam (`cdf.service`),
> a browser demo, and a live NL question returning a grounded, cited envelope.
> But it's proven over a **toy** (2 accounts, 2 tickets, hand-authored CSI, a
> cartesian "join", a 1-entry question registry). This plan closes the gap
> between *"the machine works"* and *"the machine demonstrates the PRD's
> claims"* — auto-derived mappings, the real corpus, the locked join design,
> the locked question arc, and a gate that proves it stays working.
>
> **v0.2 (2026-07-17) — reworked against PJ's recovered locked docs** (local
> reference: `docs/questions_answers/`, deliberately git-ignored). Three
> corrections: (1) the cross-graph join is **document-level and deterministic**
> — `Chunk → Document.account_id ↔ Account`, stamped at build time; *"no fuzzy
> entity matching at runtime"* is the locked design, so runtime AER is
> **explicitly P2 (M6)**, not P1 debt. (2) The golden set encodes the
> **eval-lock contracts** (reconciliation, groundingScore, faithfulness floor,
> the Q12/Q13 naming regexes, refusal invariants) — not expected answer text.
> (3) The demo is a **six-question arc in a locked order** (Q7 anchor → Q2 →
> Q12★ → Q9 → Q5 → Q8, + Helio Q13/Q14/Q15), with six adversarial/refusal
> cases where refusing *is* the pass.

## What's already done (don't re-plan it)

| Piece | State |
|---|---|
| Engine pipeline (E1/E2/E3) + grounding + golden-gate code | shipped, 57 tests |
| Both live legs + HTTP seam + browser demo | proven end-to-end 2026-07-16 |
| `arango-sparql-py` A2/A3/C1/C2 (incl. `seed_bindings` bind-join) | shipped |
| **r2g A1/A4** — `mapping_to_csi` (schema-validated, deterministic) + R2RML emitter + `export-r2rml` CLI | **shipped** (verified 2026-07-17) |
| Structured synthetic corpus per account (`customer-context/data_gen/output/structured/{helio,meridian,northwind}`) | exists |

## Gap analysis → work packages

The remaining distance is **data, joins, honesty about NL, and repeatability** — not engine code.

### WP-P1.1 — Data spine: structured half into live Postgres
**Owner: PJ · dep: none · ~½ day.**
Load `data_gen/output/structured/*` (3 accounts) into the `deploy/ontop` Postgres — a `deploy/ontop/load_corpus.py` (or generated SQL) replacing the toy `seed.sql`. The corpus already bakes the deterministic `entity_id` (UUID v5) into every row — **preserve those columns**; they are the join spine.
**Accept:** row counts match the data-gen manifest; every account/contact/contract row carries its `entity_id`; read-only demo role + `statement_timeout` applied (CC-7/CC-11).

### WP-P1.2 — Real mappings: r2g emits the CSI + R2RML (retire hand-authored files)
**Owner: Arthur · dep: P1.1 (schema exists) · ~½ day.**
Run r2g against the corpus Postgres: `ingest-schema → generate-config → mapping_to_csi → deploy/csi/postgresql-crm.json` and `export-r2rml → deploy/ontop/input/mapping.ttl`. Delete the hand-authored stand-ins. This is the moment the demo's mapping story becomes **"auto-emitted, not hand-crafted"** — the PRD §7.4 "visibly auto-derived" criterion for the structured side. *(Stretch, not gating: show RSA's conceptual bundle + the AOE confirm step for the B2 story.)*
**Accept:** `/health` sources come from r2g-emitted CSI; Ontop answers through the generated R2RML; `git log` shows the hand-authored files removed.

### WP-P1.3 — Unstructured KG into ArangoDB + the `account_id` stamp
**Owner: PJ (pipeline knowledge) · dep: none (parallel to P1.1) · ~1 day, needs LLM/API keys.**
Build the unstructured KG in the demo ArangoDB from `data_gen/output/unstructured/*` (whichever build path PJ runs today — the v3 `ingestion/` pipeline or the AutoGraph-hybrid). **The locked join design is the acceptance bar, not any particular pipeline:** every built `Chunk` reaches a `Document` carrying the **`account_id`** that matches the structured `Account` — if the build drops import metadata (AutoGraph does; only `citable_url` survives), apply the locked **post-build AQL UPSERT** (keyed on `filename`/`citable_url`) to stamp it. Then produce the **reverse CSI** with `arango-schema-analyzer`, replacing the second hand-authored CSI.
**Accept:** chunks/documents populated for all 3 accounts; `Chunk → Document.account_id` resolves for 100% of chunks (spot-check per account); the analyzer's reverse CSI validates and drives the Arango leg.

### WP-P1.4 — The locked join: document-level `account_id` via seed pushdown
**Owner: PJ · dep: P1.1 + P1.3 · ~½ day.**
Per the locked data map, the cross-graph join is **deterministic and document-level**: `Chunk → Document.account_id ↔ structured Account.account_id` — *"no fuzzy entity matching at runtime."* Wire exactly that: the relational leg returns `account_id` as a column (it's in the rows); the Arango leg receives it via **C2's `seed_bindings`** (the CC-11/FR-13 bind-join — keys ship into the AQL leg as a `VALUES` clause, so the unstructured side never over-fetches). `deploy/demo/federation.py` already joins on `account_id` — it had the locked design right; converge it onto `FederationService` + the catalog rather than hand wiring. **Runtime AER resolution is explicitly out of P1 scope** — it's M6's P2/P3 ladder (semantic, then federation-aware ER), not missing P1 work.
**Accept:** a Q2-shaped query for Meridian returns rows joined on `account_id`; the retrieval path shows the seeded key in the Arango leg's query; no leg returns unbounded rows.

### WP-P1.5 — D1-thin: NL front-end with the registry as the demo's safety net
**Owner: shared · dep: P1.2 (schema card from real CSI) · ~1 day.**
An LLM translator behind the existing `federate_question` seam: render a schema card from the catalog (concept names/properties — sample *values* only through the redaction gate), few-shot to conceptual SPARQL, validate by running `partition_query` (a plan that fails admission → one retry with the error, then refusal). The prepared-questions registry stays as **M9 FR-2's pre-run mode** — and it now holds the **exact locked prompts** (which name the account *and* the sources; PJ's caveat is explicit that this scaffolding is part of the proven green path). The live demo runs those; free-form phrasing *without* the scaffolding is the residual risk — show it as the stretch act, not the script.
**Accept:** the six locked prompts produce valid plans deterministically via the registry; ≥2 free-form re-phrasings of Q12/Q2 produce valid plans ≥4/5 runs; unknown/unanswerable questions still refuse.

### WP-P1.6 — Golden set content: encode the eval-lock **contracts** *(unblocked — PJ's docs recovered)*
**Owner: PJ authors, Arthur signs off · dep: P1.3 (corpus queryable) · ~½ day.**
`docs/questions_answers/` (local reference, git-ignored) now holds the exact prompts, expected-answer narratives, and — the part the golden set encodes — **what the gate actually locks**, which is a *contract*, not answer text:
- **All 9 questions:** non-refusal · `groundingScore === 1.0` · faithfulness ≥ 0.6 · every claim cited.
- **Q7 / Q15 (anchors):** every citation `graph === 'structured'` — the trust-building baseline, twice.
- **Q2/Q5/Q8/Q9/Q12/Q13/Q14 (dual):** reconciliation — ≥1 structured **and** ≥1 unstructured citation.
- **Q12 ★:** answer matches **both** `green|healthy|usage|metric` **and** `red|risk|sentiment|…|contradict`; **Q13:** matches `declin|contract|downgrad|churn|at.risk`.
- **The 6 adversarial/refusal cases** (PII ×2, non-existent account, non-existent fields, injection ×2): `refused === true` + **zero fabricated `_id`** — pure-code invariants, never the stochastic judge.
- **Plus the fabric-specific case** the old harness couldn't have: **partial-failure** (stop a container mid-run; assert the declared-partial envelope per CC-5).
P1 gates on the **Q7 → Q2 → Q12 slice + 2 refusal cases**; the rest of the arc fills in as data lands.
**Accept:** golden run green against the live stack; deliberately breaking a mapping turns it red; the run is the mandated pre-demo step (P1.7).

### WP-P1.7 — One-command demo (CC-8) + demo/service convergence
**Owner: Arthur (me) · dep: P1.1–P1.4 · ~½ day.**
A top-level `make demo` (or `deploy/docker-compose.yml`): both stacks + corpus loads + the service + the browser demo, ports parameterized, fresh-clone-to-answer in one command. Converge `deploy/demo/federation.py` onto `FederationService.from_env` (one wiring path, not two). **The demo script is the locked six-question arc in its locked order** — Q7 (anchor: clean structured-only sourcing builds trust) → Q2 → **Q12 ★** → Q9 → Q5 → Q8 — followed by an adversarial refusal, plus the fabric's own act: kill Postgres mid-demo → the declared-partial envelope. Per PJ's own runbook rule: **the golden gate runs before any demo, no exceptions.**
**Accept:** fresh clone → `make demo` → browser walks the arc; the failure + refusal acts are scripted in the demo notes; a pre-demo `make gate` target exists.

### WP-P1.8 — CI (the repo has lint/type config now, but no gate)
**Owner: Arthur (me) · dep: none — do first, it protects everything else · ~2 hrs.**
GitHub Actions: ruff + mypy + the 57 unit tests on every push; a second job with ArangoDB/Postgres/Ontop service containers running the live tests + (post-P1.6) the golden gate — mirroring `arango-sparql-py`'s CI pattern. This is CC-9's enforcement hook: pin bumps re-run the gate.
**Accept:** green badge; a broken test blocks merge.

## Sequencing (3 working days)

```
Day 1:  P1.8 (CI, morning) ║ P1.1 (structured→Postgres) ║ P1.3 starts (ingestion, keys)
Day 2:  P1.2 (r2g CSI/R2RML) → P1.4 (canonical join)    ║ P1.3 completes + reverse CSI
Day 3:  P1.6 (golden content) → P1.7 (one-command demo + rehearsal) ║ P1.5 (D1-thin, parallel)
```

**Cut lines if time runs out (in order):** drop P1.5's free-form act (registry runs the locked prompts — the script doesn't change); trim the arc to its gated slice **Q7 → Q2 → Q12** (the anchor + the risk question + the centerpiece carry the whole story); **never cut** P1.6's refusal/partial cases or P1.7's pre-demo gate run — a flaky demo is the named failure mode (M9 NFR), and "run the eval gate before any demo" is PJ's own runbook rule.

## Risks

1. **Ingestion cost/time (P1.3):** the build needs LLM keys and hours, and the first run into a fresh DB is where customer-context RE-1 friction will surface. Two mitigations from the locked docs: the join stamp is a **known, documented post-build UPSERT** (not discovery work), and the acceptance bar is the document-level join — not any particular pipeline. Start it Day 1, not Day 2.
2. **r2g mapping-config authoring (P1.2):** `mapping_to_csi` consumes a `MappingConfig`; someone must run/accept the generated config for the corpus schema. Bounded, but it's the first real use of A1 — expect a round of fixes.
3. **Free-form NL (P1.5):** PJ's caveat is explicit — the locked prompts name the account and sources, and that scaffolding is part of the proven green path. Free-form phrasing is the residual risk; it is staged as a stretch act, never the demo's spine.
4. **Two wiring paths drift (P1.7):** until `federation.py` uses `FederationService`, demo and service can disagree about ports/seeds — exactly what burned us on 2026-07-16 (stale ports → two-leg refusal). Converge early.
5. **Provenance of the locked docs:** `docs/questions_answers/` is a local, git-ignored reference (not committed here; canonical home remains `customer-context/docs/research/`, still absent upstream). Anything the fabric *repo* must depend on (the golden contracts) gets encoded into `cdf.eval.golden` — committed code — so the ignored directory never becomes a hidden dependency.

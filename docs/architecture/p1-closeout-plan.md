---
title: "P1 Close-Out Plan — from proven toy to credible demo"
type:
  - internal
  - implementation-plan
status: draft
version: 0.1
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
> claims"* — auto-derived mappings, the real corpus, a canonical-entity join,
> Q12/Q2, and a gate that proves it stays working.

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

### WP-P1.3 — Unstructured half + canonical hub into ArangoDB
**Owner: PJ (pipeline knowledge) · dep: none (parallel to P1.1) · ~1 day, needs LLM/API keys.**
Run the customer-context `ingestion/` pipeline (chunking → extraction → span gate → coref → embeddings → **AER** → survivorship) into the demo ArangoDB; confirm `canonical_entities` + `same_as` land. Then produce the **reverse CSI** with `arango-schema-analyzer` — replacing the second hand-authored CSI.
**Accept:** chunks/entities/canonical hub populated for all 3 accounts; the analyzer's reverse CSI validates and drives the Arango leg; Meridian's account resolves to one canonical id reachable from both graphs.

### WP-P1.4 — Canonical-entity join (replace the toy cartesian)
**Owner: PJ · dep: P1.1 + P1.3 · ~½ day.**
The seed questions join structured facts and unstructured signals **about one account**. Wire the join on the canonical `entity_id`: the relational leg returns it as a column (it's in the rows); the Arango leg receives it via **C2's `seed_bindings`** (the bind-join CC-11/FR-13 specified — keys ship into the AQL leg as a `VALUES` clause, so the unstructured side never over-fetches). `deploy/demo/federation.py` already joins on `account_id` — converge it onto `FederationService` + the catalog rather than hand wiring.
**Accept:** a Q2-shaped query for Meridian returns rows joined on the canonical id; the retrieval path shows the seeded key in the Arango leg's query; no leg returns unbounded rows.

### WP-P1.5 — D1-thin: NL front-end with the registry as the demo's safety net
**Owner: shared · dep: P1.2 (schema card from real CSI) · ~1 day.**
An LLM translator behind the existing `federate_question` seam: render a schema card from the catalog (concept names/properties — sample *values* only through the redaction gate), few-shot to conceptual SPARQL, validate by running `partition_query` (a plan that fails admission → one retry with the error, then refusal). The prepared-questions registry stays as **M9 FR-2's pre-run mode** — the live demo runs the registry; the free-form path is shown when it behaves.
**Accept:** Q12 + Q2 free-form phrasings produce valid plans ≥4/5 runs; unknown/unanswerable questions still refuse; registry fallback demonstrated.

### WP-P1.6 — Golden set content (F1/M10 has code, no truth)
**Owner: PJ authors, Arthur signs off · dep: P1.3 (corpus queryable) · ~½ day.**
Fill `cdf.eval.golden` with **Q12 and Q2**: expected answer facts, expected sources, expected canonical join entity, citation expectations (Q12 must *name the contradiction*), plus one **refusal** case and one **partial-failure** case (stop a container; assert the declared-partial envelope).
**Blocked-mitigation:** PJ's `locked-questions-expected-answers.md` is *still* uncommitted (re-checked today). Path A: PJ pushes it (5 minutes, ask again). Path B: derive expected answers from the corpus and have PJ correct — don't let the file block the gate.
**Accept:** golden run green against the live stack; deliberately breaking a mapping turns it red.

### WP-P1.7 — One-command demo (CC-8) + demo/service convergence
**Owner: Arthur (me) · dep: P1.1–P1.4 · ~½ day.**
A top-level `make demo` (or `deploy/docker-compose.yml`): both stacks + corpus loads + the service + the browser demo, ports parameterized, fresh-clone-to-answer in one command. Converge `deploy/demo/federation.py` onto `FederationService.from_env` (one wiring path, not two). Rehearse the failure act: kill Postgres mid-demo → the declared-partial envelope *is* the trust pitch.
**Accept:** fresh clone → `make demo` → browser answers Q12; the failure rehearsal is scripted in the demo notes.

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

**Cut lines if time runs out (in order):** drop P1.5 (registry-only demo — the script doesn't change); drop Q2 (Q12 alone carries the story); **never cut** P1.6's refusal/partial cases or P1.7's rehearsal — a flaky demo is the named failure mode (M9 NFR).

## Risks

1. **Ingestion cost/time (P1.3):** the pipeline needs LLM keys and hours, and was built against its own DB conventions — the first run into a fresh DB is where customer-context RE-1 friction will surface. Start it Day 1, not Day 2.
2. **r2g mapping-config authoring (P1.2):** `mapping_to_csi` consumes a `MappingConfig`; someone must run/accept the generated config for the corpus schema. Bounded, but it's the first real use of A1 — expect a round of fixes.
3. **PJ's uncommitted docs (P1.6):** mitigation in the WP; do not let it gate.
4. **Two wiring paths drift (P1.7):** until `federation.py` uses `FederationService`, demo and service can disagree about ports/seeds — exactly what burned us on 2026-07-16 (stale ports → two-leg refusal). Converge early.

---
title: "Module 10 — Evaluation & Golden Set — Specification"
module: 10-evaluation
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: TBD (proposed: PJ authors harness, Arthur reviews expected answers)
building_block: "—"
depends_on_modules: ["05-federated-query-engine", "07-grounding-provenance"]
depends_on_repos: ["customer-context", "ontology-extractor"]
requires_repo_enhancements: []
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 10 — Evaluation & Golden Set

> Make "trust is structural" **testable**: a golden set of seed questions with expected answers, expected sources touched, and expected citations, run against every planner change. Added in PRD v0.2 (§10.1 / CC-1) — the fabric's pitch is correctness, so correctness needs a harness, not a demo.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
Own the correctness bar for federated answers. A grounded, cited answer can still be *wrong* (bad decomposition, wrong join, missed source); citations prove traceability, not correctness. This module holds the golden set, the runner, and the regression gate — so LLM-planner changes, mapping edits, and ontology revisions can't silently degrade answers. It is internal tooling (like M9, not sold).

**Methodology alignment (v0.3):** the golden set is the fabric-level instance of the **competency-question (CQ) program AOE has committed as PRD §6.19** (ORSD-style requirements spec; CQs human-authored/LLM-assisted; each CQ formalized to a test query; coverage validated and gap-fed back). Adopt the same model end-to-end: the seed questions are the CQs, this module's runner is the CQ coverage validator at federation level, and AOE's FR-19.5/19.8 coverage reports gate the *ontology* while M10 gates the *answers*. The runner itself should reuse `arango-cypher-py`'s proven eval harness + regression gate (M5 plan WPs D2/F1) rather than building new scaffolding.

## 2. Scope
**In scope:** the golden-set format (question, expected answer facts, expected sources, expected join entity, expected citation shape); a runner that executes each question through M5→M7 and scores the result; decomposition scoring (did the plan hit the right sources/join keys); regression gating for planner/mapping changes; per-run reports.
**Out of scope:** ER match-quality evaluation (the AER enhancement spec's harness — coordinate, don't duplicate); ontology-extraction quality (AOE's LLM-as-judge + qualitative evaluation agent already cover it); UI (M9 may render reports).

## 3. Interfaces (inputs / outputs)
- **Consumes:** M5 `federate(...)` + the M7 envelope for each golden question; the golden-set file (YAML, versioned in this repo).
- **Produces:** a scored run report `{question, pass|fail, plan_score, answer_score, citation_score, diff}`; a red/green regression signal for CI.
- **Contract:** `evaluate(golden_set, fabric) -> report` — runnable from CI and locally.

## 4. Functional requirements
- **FR-1 (P1):** Golden set covering the CQ table in [`docs/use-cases.md`](../../use-cases.md) (start with the proposed P1 questions **Q12 + Q2**; Q7/Q15 as single-leg smoke tests): expected answer facts, expected sources touched, expected canonical join entity, expected citation count/shape. Q12 additionally asserts the answer **names the contradiction**. Baseline contract inherited from `customer-context/agent/test/questions.eval.test.ts` (envelope well-formed, groundingScore 1.0, faithfulness ≥ 0.6).
- **FR-2 (P1):** Runner executes each golden question end-to-end and reports pass/fail per dimension (answer facts present, sources correct, citations complete, refusal-when-expected).
- **FR-3 (P1):** **Refusal cases** — at least one deliberately uncitable question per set; the correct result is a refusal (tests the grounding gate, M7 FR-3).
- **FR-4 (P2):** **Decomposition scoring** — compare the plan (sources, join keys) against the expected plan; catches "right answer by accident."
- **FR-5 (P2):** **LLM-as-judge scoring** for answer faithfulness, reusing AOE's judge patterns (faithfulness + qualitative evaluation agent) rather than building new judges.
- **FR-6 (P2):** Regression gate wired into CI: a planner/mapping/ontology change that fails the golden set blocks merge.
- **FR-7 (P2):** Partial-failure cases — simulate a failed leg and assert CC-5 behavior (declared partial answer or refusal, never silent omission).

## 5. Non-functional requirements
Deterministic where possible (fixed seeds/temperature-0 for LLM legs, recorded fixtures for sources); cheap enough to run per-PR; golden set versioned alongside the ontology/mapping versions it was authored against (CC-3).

## 6. Dependencies
- **Modules:** M5 (execution), M7 (envelope to score), M9 (optional report rendering).
- **Repos:** `customer-context` (the demo questions + corpus the P1 set is authored against); `ontology-extractor`/AOE (judge patterns for FR-5; the §6.19 CQ/ORSD model + coverage reports); `arango-cypher-py` (the eval harness + regression gate to reuse — M5 plan D2/F1).

## 7. Phase mapping
- **P1:** golden set for the seed questions + runner + refusal case (FR-1–FR-3).
- **P2:** decomposition scoring, LLM-as-judge, CI gate, partial-failure cases (FR-4–FR-7).
- **P3:** portfolio-scale question sets; cost/latency budgets as assertions (ties to M5 FR-9).

## 8. Acceptance criteria / demo (P1)
- Running the evaluator against the P1 build scores every seed question green (facts, sources, citations) and the refusal case refuses — and deliberately breaking a mapping turns the run red.

## 9. Open questions
- Who authors expected answers (PRD §9.7) — proposal: PJ drafts from the corpus, Arthur signs off. **Partially resolved (v0.3.1): PJ already authored expected answers** in `locked-questions-expected-answers.md` — uncommitted; recovering it is the action in `docs/use-cases.md` §6.
- Fixture strategy: record/replay source responses vs live-DB runs in CI (live Postgres is cheap; live Snowflake in P2 may not be).
- Where the golden set lives once the repo-shape decision (PRD §9.5) lands.

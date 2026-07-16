---
title: "Module 05 — Federated Query Engine — Implementation Plan"
module: 05-federated-query-engine
type:
  - internal
  - implementation-plan
status: draft
version: 0.1
owner: PJ (Paul Losiewicz)
build_gatekeeper: Arthur Keen
depends_on_modules: ["04-mapping-layer", "01-connectors", "06-entity-resolution", "07-grounding-provenance"]
depends_on_repos: ["r2g", "arango-sparql-py", "arango-cypher-py", "arangodb-schema-analyzer", "relational-schema-analyzer", "customer-context"]
related:
  - "[[contextual-data-fabric/docs/architecture/module-05-federated-query-engine/specification|M5 spec]]"
  - "[[contextual-data-fabric/docs/architecture/module-05-federated-query-engine/adr/ADR-0001-conceptual-query-language|ADR-0001]]"
  - "[[contextual-data-fabric-prd]]"
---

# Module 05 — Federated Query Engine — Implementation Plan

> Sequences the M5 build set by [[adr/ADR-0001-conceptual-query-language|ADR-0001]]
> and the [[specification|M5 spec]] into dependency-ordered work packages, cut
> into the P1 (≈1-week demo) slice vs. P2/P3. Every WP traces to a spec FR
> and/or an ADR decision.

## Implementation status (2026-07)

First code has landed:

- **A1** — r2g forward-`CSI v1` emitter + `export-csi` CLI (`r2g/src/r2g/csi.py`).
- **A2** — `arango-sparql-py` accepts the analyzer `phys:` namespace (canonical).
- **A3** — `CSI → MappingBundle` adapter (`arango-sparql-py`
  `translate/csi.py`); resolver round-trip proven (A1→A3→AQL leg).
- **A4** — r2g `R2RML` emitter + `export-r2rml` CLI (`r2g/src/r2g/r2rml.py`);
  concept IRIs default to the shared `urn:arango-sparql:concept#` namespace so
  both legs share one vocabulary.
- **E1** — query-graph partition planner, this module's first code:
  `cdf/query/` (`catalog.py` = concept→source index from CSI; `planner.py` =
  `partition_query`). Class-binding routing; cross-source join keys fall out
  naturally; unknown concepts surface as `unresolved`; unsupported constructs
  (`FILTER`/`OPTIONAL`/`UNION`/…) **refuse** rather than silently drop.
- **E2** — federated executor (`cdf/query/executor.py` `execute_plan`): runs
  each leg through a pluggable `SourceExecutor`, inner-joins on the plan's join
  keys, projects, and assembles a **retrieval path** with **partial-failure**
  (failed/unroutable legs declared, never dropped — FR-11) and **as-of** stamps
  (FR-12). Real Ontop/AQL adapters wire into this protocol at B1/C-work.
- **E3** — grounding envelope + cite-or-refuse gate (`cdf/query/grounding.py`
  `ground`): wraps the federated result into a cited `AnswerEnvelope`
  (per-leg citations carry the actual SQL/AQL, source objects, and as-of) and
  **refuses over guesses** — a requested variable with no source, or (strict
  default) any failed leg / unroutable pattern, refuses; `allow_partial=True`
  (concierge) returns a declared partial only when every projected variable is
  still available. Feeds M7 (customer-context adds the NL answer + citation UI).

- **F1** — golden seed-question regression gate (`cdf/eval/`): declarative JSON
  cases (`goldens/*.json`) run end-to-end through partition→execute→ground
  against fixture source data; each pins the expected answer, sources touched,
  citations, and grounded/refused status. Locks the engine contract so the real
  adapters (B1/C1) can't silently regress it.

- **B1** — Ontop relational leg (Apache-2.0; the buy-vs-build fork resolved to
  **integrate Ontop**, it's free OSS). `cdf/adapters/ontop.py` `OntopExecutor`
  sends an E1 sub-query to an Ontop SPARQL endpoint (SPARQL→SQL over live
  Postgres via the A4 R2RML) and parses results into a `SourceResult`; proven in
  the full pipeline with a mocked transport. Runnable stack in `deploy/ontop/`
  (compose + seed + R2RML + properties) with an opt-in live integration test
  (`ONTOP_SPARQL_ENDPOINT`). **Code-complete; live bring-up pending a Docker
  host.**

- **Arango graph adapter** — `cdf/adapters/arango.py` `ArangoExecutor`: transpiles
  an E1 sub-query to AQL via the owned `arango-sparql-py` engine over a
  `MappingBundle` derived from the source `CSI` (A3), runs it against ArangoDB,
  and maps the (already bare-var-keyed) AQL rows into a `SourceResult`. Proven
  against the **real** transpiler (CSI→bundle→resolver→AQL) with an injected
  transport (no DB), plus in the full pipeline. Runnable stack in `deploy/arango/`
  + opt-in live test (`ARANGO_URL`). **Code-complete; live bring-up pending a
  Docker host.** *(NB: the plan's separately-tracked WP-C1 — promoting
  arango-sparql-py's eval-correctness to a CI gate + the variable-predicate bug —
  remains distinct work in that repo.)*

Both source adapters (Ontop relational, arango-sparql-py graph) are code-complete.
Swapping them in for the golden fixtures turns the F1 goldens into the live P1
federated demo — that's the remaining step, and it just needs a Docker host to
run the two `deploy/` stacks. Then: **F1 live variant** + M7 UI (customer-context)
and the arango-sparql-py eval-CI hardening (WP-C1).

## Guiding constraints (from ADR-0001 + PRD)

- **IR = SPARQL** (canonical), generated by **reusing `arango-cypher-py`'s NL
  engine** (swap the ~5 Cypher seams). Relational leg = **Ontop** (SPARQL→SQL,
  buy); Arango leg = **`arango-sparql-py`** (SPARQL→AQL, owned, finish it).
- **Mapping hub = `CSI v1`** (owned, in `arango-schema-analyzer`); **r2g is the
  forward producer**. Everything drives off CSI → R2RML (SQL) / MappingBundle
  (AQL).
- **No data movement**; **cite-or-refuse**; **deterministic-capable, cost-
  inspectable**; join cross-source on **AER canonical keys** (M6).
- **Integrate owned components; the genuinely net-new work is the federation
  layer** (partition planner, join, provenance) — everything else is finish/adapt.

## Component readiness (recap)

| Component | State | Gap for M5 |
| :--- | :--- | :--- |
| RSA / `arangodb-schema-analyzer` (mappings) | shipped; shared `{conceptualSchema, physicalMapping, metadata}` envelope; **CSI v1** exists | RSA doesn't emit CSI; contract is *copied*, not shared |
| r2g (mapping + pushdown) | mapping export exists; Phase 12 planned | **forward-CSI + R2RML emitter** (P12.1); pushdown (P12.2) is buy-vs-build vs Ontop |
| Ontop (relational SPARQL→SQL) | mature, non-materializing, covers all our SQL sources | stand up + drive from our R2RML; infra decision |
| `arango-sparql-py` (Arango SPARQL→AQL) | **A2, A3, C1, C2 landed (2026-07-15, `b26f35d`)**: eval correctness CI-gated (live-Arango + W3C execution suites; variable-predicate IRI bug fixed), `phys:` namespace accepted, CSI→MappingBundle adapter, and `translate_partition` federation entry (canonical keys as subject-IRI columns; **`seed_bindings` VALUES pushdown = the FR-13 bind-join mechanism**; `as_of` executor-stamped). Contract: `arango-sparql-py/docs/architecture/proposals/federation-entry-point.md` — renegotiable before pinning (one consumer today). | **none blocking** — E1 partition contract consumes shape 1 (sub-SELECT string) as shipped |
| `arango-cypher-py` (NL engine) | mature transpiler + **proven NL→query engine** | reuse the NL engine to emit **SPARQL** (seam swaps) |
| AER (M6) | canonical entities / resolution | expose a **join-key resolver** to the executor |
| M7 (grounding) | envelope + refuse-if-uncited | consume M5's retrieval path |

## Workstreams & work packages

IDs are `WP-M5-n`. **Owner**: repo WPs → **Arthur** (build gatekeeper); engine
WPs → **PJ**; NL-engine reuse → **shared**. **Dep** = hard prerequisite.

### A. Mapping alignment (unblockers — the CSI hub)
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **A1** | **r2g → forward `CSI v1` emitter** — emit `{conceptualModel, arangoPhysicalMapping, provenance:direction=forward}` from `MappingConfig` + RSA conceptual bundle | r2g | Arthur | — | ADR #3.1; r2g P12.1 |
| **A2** ✅ | **DONE (2026-07-15, `64027b6`)** — analyzer `phys:` namespace accepted as canonical | arango-sparql-py | Arthur | — | ADR #3.4 |
| **A3** ✅ | **DONE (2026-07-15, `a1ef785`)** — CSI v1 → `MappingBundle` adapter (landed in `arango-sparql-py`, not r2g as planned) | arango-sparql-py | Arthur | ~~A1~~, A2 | ADR #3.3 |
| **A4** | **CSI → R2RML serializer** for Ontop | r2g | Arthur | A1 | ADR #3.2; r2g P12.1 |

### B. Relational leg (SPARQL→SQL)
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **B1** | **Stand up Ontop** over our R2RML; verify SPARQL→SQL pushdown vs Postgres, no materialization | infra / CDF | Arthur | A4 | FR-2; ADR #2 |
| **B1-alt** | *(fallback)* **r2g P12.2 pushdown-SQL generation** if Ontop infra is rejected | r2g | Arthur | A1 | FR-2; ADR #2 / r2g P12.2 |

### C. Arango leg (SPARQL→AQL) — finish `arango-sparql-py`
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **C1** ✅ | **DONE (2026-07-15, `acf6892` / WP-BE-EVALGATE)** — variable-predicate→IRI bug + 4 AQL runtime bugs fixed; live-ArangoDB + W3C execution suites run in CI (service container) + nightly | arango-sparql-py | Arthur | A2 | ADR #1 cost; FR-3 |
| **C2** ✅ | **DONE (2026-07-15, `b26f35d`)** — `translate_partition(PartitionSpec, resolver, canonical_keys)`: wire shape 1 (sub-SELECT string), canonical key = subject IRI as its own result column, **`seed_bindings` VALUES pushdown** (hostile-seed escaping tested), `as_of` executor-stamped; two-leg federation parity test (partition + pushdown + engine-join == whole-query). **Contract doc: `arango-sparql-py/docs/architecture/proposals/federation-entry-point.md`** — E1 may renegotiate before pinning | arango-sparql-py | Arthur | C1 | ADR #1/#4; FR-3/4 |

### D. NL → SPARQL IR (reuse the owned engine)
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **D1** | **SPARQL NL front-end** by reusing `arango-cypher-py`'s NL engine — swap the ~5 seams: system prompt, schema-card renderer (→ SPARQL/BGP), response extractor, parser (→ rdflib), EXPLAIN-grounded validator (→ translate-and-explain via C/B) | arango-cypher-py → shared | shared | A3 (grounding), C1/B1 (validator) | ADR #1; FR-6 |
| **D2** | **Port the eval harness + few-shot corpus** to SPARQL seed questions | arango-cypher-py → CDF | shared | D1 | FR-6; PRD §10.1 |

### E. Federation engine (the net-new heart of M5)
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **E1** | **Query-graph partition planner** — resolve question→ontology concepts; partition the SPARQL query graph by the source each concept/property maps to (via CSI); emit per-source sub-queries + the join keys. Defines the **partition contract** C2/B consume | CDF (M5) | PJ | A1/A3 | FR-1 |
| **E2** | **Executor + reassembly** — run partitions (Ontop / arango-sparql-py), join on **AER canonical keys** (M6); parallelize independent legs | CDF (M5) | PJ | E1, B1, C2, M6 | FR-2/3/4 |
| **E3** | **Retrieval path + provenance + as-of + partial-failure** — emit `{source, query_text, source_objects, rows, as_of}`; declare failed legs; refuse when load-bearing; feed M7 | CDF (M5) | PJ | E2 | FR-5/11/12 |

### F. Eval & agent surface
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **F1** | **Golden seed-question set + regression gate** (M10) — expected answer, sources touched, citations; reuse `arango-cypher-py`'s harness | CDF | PJ | E3 | PRD §10.1 |
| **F2** | *(P2)* **MCP `federate(question) → cited envelope`** external surface (PRD §9.8) | CDF | PJ | E3 | FR (agent I/F) |

### Deferred (P2/P3)
| WP | Work | Phase | Trace |
| :-- | :-- | :-- | :-- |
| **G1** | Deterministic planner (mapping-driven; LLM as safety net) | P2 | FR-7 |
| **G2** | Cost/latency instrumentation per plan | P2 | FR-9 |
| **G3** | Assembled execution pattern (bounded materialized subgraph) | P2 | FR-8 |
| **G4** | Snowflake leg (Ontop dialect / r2g P12.7) | P2 | r2g P12.7 |
| **G5** | Multi-source (≥3) planner + cost-based join optimization (Calcite-style) | P3 | FR-10 |
| **G6** | RSA → CSI adapter (if RSA output must reach the hub without r2g) | P3 | ADR #3.5 |

## Critical path & parallelization

```
A1 ─┬─▶ A4 ─▶ B1 ─────────────┐
    ├─▶ A3 ─────────────┐     │
A2 ─┘                   │     │
                        ▼     ▼
                 D1 ─▶ D2   E1 ─▶ E2 ─▶ E3 ─▶ F1
C1 ─▶ C2 ───────────────────▲ (partition contract from E1)
```

- **Status (2026-07-15): A2, A3, C1, C2 are DONE** — the entire Arango leg and
  both mapping shims. The remaining graph is: A1 → A4 → B1 (or B1-alt directly
  off A1), and A1 → E1 → E2 → E3 → F1.
- **Gating chain (unchanged):** A1 → E1 (planner needs CSI mappings) → E2 → E3
  → F1. **A1 (r2g forward-CSI) is now the only unstarted unblocker** — it gates
  everything left.
- ~~C2 and E1 co-design the partition contract~~ — **contract shipped ahead of
  E1** (`translate_partition`, wire shape 1, seed pushdown; see the contract
  doc). E1 consumes it as-is and may renegotiate any of its four decisions
  before pinning (CC-9) — it has one consumer today.

## P1 — the 1-week walking skeleton (honest scope)

**Goal (M5 spec §8 / PRD B4):** one seed CSM question answered end-to-end,
federating **live Postgres + the Arango unstructured graph**, joined on the
canonical entity, returning an answer whose **retrieval path shows the actual
SQL + AQL**; no bulk copy; refuse if a leg can't be cited.

A *full* SPARQL-OBDA engine is **not** a 1-week build. P1 is a **thin vertical
slice** for **1–2 seed questions**, using owned components, with the general
engine following in P2:

- **A1** (r2g forward-CSI) — minimal, for the one source. *(A2 ✅ done.)*
- **Relational leg:** **B1-alt** (r2g P12.2 pushdown for the one query) is the
  fastest P1 path; stand up **B1** (Ontop) in P2. *(Pick per how fast Ontop
  stands up — Arthur's call.)*
- **Arango leg: ✅ ready, no fallback needed.** C1 + C2 landed (2026-07-15) —
  eval-gated `translate_partition` with canonical keys, seed pushdown, and a
  two-leg federation parity test. Use `arango-sparql-py` directly in P1; the
  `arango-cypher-py` Cypher→AQL fallback is retired (its NL engine remains the
  D1 asset).
- **D1 (thin):** LLM emits the query for the seed question(s); reuse the retry
  loop, not necessarily the full seam swap.
- **E1/E2/E3 (thin):** a **hand-scoped planner for the seed question(s)** (2
  legs, known join key) — not the general partition planner — plus the real
  join-on-AER-key and the real retrieval-path/citation/refuse behavior (these
  are the demo's whole point, so build them for real even at n=1).
- **F1:** the 1–2 seed questions as the golden set from day one.

**P1 exit:** the demo question returns a grounded, cited answer whose retrieval
path shows the real SQL + AQL and the AER join, with no Postgres data copied
into Arango, and a clean refusal when a leg is uncitable.

## P2 / P3

- **P2:** converge P1 stopgaps to the canonical architecture — Ontop (B1)
  *(the Arango leg already converged: C1/C2 shipped in P1 timeframe)*, the
  general partition planner (E1 full), the SPARQL seam-swap NL front-end (D1
  full) + eval harness (D2/F1), deterministic planner (G1), cost/latency (G2),
  assembled pattern (G3), Snowflake (G4), MCP surface (F2).
- **P3:** multi-source (≥3) planner + cost-based join optimization (G5).

## Risks (carried from ADR-0001)

1. ~~**`arango-sparql-py` evaluation correctness is unproven**~~ — **RETIRED
   (2026-07-15):** C1 landed (variable-predicate IRI fix + 4 AQL runtime bugs;
   live-Arango + W3C execution suites CI-gated) and C2 shipped the federation
   entry with a two-leg parity test. The P1 Cypher→AQL fallback is withdrawn.
2. **Ontop infra vs r2g P12.2** (ADR #2) is unresolved — B1 vs B1-alt. P1 can
   sidestep with B1-alt; P2 should decide.
3. **SPARQL LLM-generation** is harder than Cypher; D1/D2 must prove the seam
   swap keeps `arango-cypher-py`'s eval quality.
4. **Reasoning at build vs query time** (ADR #5): materialize `sameAs`/
   `equivalentClass` in M2/M3 so M5 stays fast/deterministic.

## Acceptance

- **P1:** M5 spec §8 + PRD B4 — one seed question, live Postgres + Arango graph,
  real SQL+AQL in the retrieval path, AER join, no bulk copy, clean refusal.
- **P2:** canonical SPARQL-OBDA loop (Ontop + arango-sparql-py + general
  planner) passes the golden set with the deterministic planner as default and
  the LLM as safety net; cost/latency surfaced.
- **P3:** ≥3-source federated question with cross-source join optimization.

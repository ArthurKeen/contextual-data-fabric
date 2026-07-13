---
title: Contextual Data Fabric — PRD
categories:
  - "[[Projects]]"
type:
  - internal
  - PRD
date: 2026-07-13
org:
  - "[[Arango]]"
project:
  - "[[Arango Contextual Data Fabric]]"
related:
  - "[[Customer360]]"
  - "[[ZScaler]]"
  - "[[2026-07-13 Zscaler Customer Context Roadmap]]"
  - "[[ZScaler Feedback Summary]]"
people:
  - "[[Arthur Keen]]"
  - "[[Michael Fonseca]]"
  - "[[Michael Gillespie]]"
  - "[[Daniel Blake Morris]]"
topics:
  - "[[Ontology]]"
  - "[[Graph]]"
status: draft
version: 0.1
---

# Contextual Data Fabric — Product Requirements Document

> **Status:** Draft v0.1 for team review. Posted per the [[2026-07-13 Zscaler Customer Context Roadmap]] action item ("PJ to draft PRD"). Arthur needs this before refactoring r2g / the ontology extractor so we scope the build rather than over-build.
>
> **Reviewers:** [[Arthur Keen]] (build gatekeeper), [[Michael Fonseca]], [[Michael Gillespie]], [[Daniel Blake Morris]].
>
> **How to read this:** §1–§5 are the general vision and architecture. **§6 is the phased plan; §7 is the detailed Phase 1 (the 1-week near-term goal).** §8 lists the repos to pull into Claude Code context. §9 is open decisions for the team.
>
> **Companion:** [[contextual-data-fabric-north-star|North Star]] — the end-state vision every phase ladders toward. This PRD is the near-term contract; the North Star is the horizon to check scope against.

---

## 1. Summary

**Contextual Data Fabric** repositions Arango from a *data store* to an **ontology-based metadata / agent-brain hub**. Enterprises have data scattered across many warehouses, lakes, apps, and unstructured sources, each with its own local semantics. Instead of moving that data, the Contextual Data Fabric:

1. **Auto-derives a use-case-driven ontology** from both structured (schemas, catalogs, Snowflake/Databricks/Postgres) and unstructured (docs, Slack, email, transcripts) sources, and **aligns** them into one master conceptual model; and
2. **Executes federated queries** — an English question hits the ontology, the ontology's functional mappings decompose it into per-source queries (SQL pushdown, AQL, agent calls), results are reassembled, and the answer comes back **grounded and cited** with the retrieval path spanning every source it touched.

Arango holds the ontology, the entity resolution / canonical entities, the mappings, and *selected* context — **not** the bulk of the raw data. Everything else stays at the source and is fetched on demand. (Mental model: the PubMed/NIH ~16 TB **metadata** graph that stores linkages, not raw data.)

This sits under [[Arango Contextual Data Fabric]]and is being built for (and pressure-tested against) the [[ZScaler]] customer-context engagement, but the fabric is a general, composable platform capability.

---

## 2. Problem & Context

### 2.1 The general problem
In the agent era, the bottleneck is no longer storing data — it is giving agents a **single, governed, semantically-normalized view** across many systems without copying everything into one place. If every agent talks directly to every system (agent-to-agent, "A2A"), you get an **N² translation problem** and you must re-implement business rules and access control on every edge. An **ontology** turns that into a wagon-wheel (linear): translate once to a shared representation; enforce rules once, in one place.

### 2.2 The customer signal ([[ZScaler]])
From the [[2026-07-10 - C360 ZScaler Demo]], [[2026-07-09 - C360 Review & Feedback with Matthew]], and [[ZScaler Feedback Summary]]:

- **Current state:** Snowflake medallion (bronze/silver/gold), **data mesh** with team-owned marts; a "Customer 360 view" exists but **each domain re-creates the same semantics/metrics** — duplication is the pain.
- **Ask #1 — auto-derive the ontology.** Rah Raman: *"the biggest challenge is defining these entities… we want to do it in a programmatic way, as new things pop up, not rely on someone's knowledge."* Our hand-modeled graph was flagged as **unrealistic at scale**. Framing: **"structured data in, ontology out."**
- **Ask #2 — Arango as the routing brain, no data duplication.** *"We don't want to move the data… the brain has to be on this side."* Agents hit Arango first; if it can't answer, the ontology routes to the source, fetches live, resolves the entity, returns.
- **Hard constraints:** no bulk materialization into Arango; **cost and latency are political** (Rah has discouraged his team from Arango over token costs — see [[2026-07-09 - C360 Review & Feedback with Matthew]]); ontology overlap across domains must be reconciled; **they want to SEE it working**, not conceptual.

### 2.3 The competitive question we must answer
**If a customer already has a Snowflake agent and can orchestrate via A2A, what does Arango add?** The prototype must *demonstrate*, not assert:
1. **Ontology normalization at the complexity threshold** — A2A can't reconcile `customer account` vs `client account` vs `account`; an ontology does.
2. **Governance the agents inherit for free** — business rules, security, access control enforced at the ontology level (the Palantir / IAM-via-ontology pattern).
3. **Grounded, traceable routing** — the hub knows what it can answer and, when it can't, routes with a **cited** retrieval path that spans Arango *and* the source system. Nobody doing pure A2A has this.

---

## 3. Goals & Non-Goals

### 3.1 Goals
- Auto-derive and **align** a use-case-driven ontology across structured + unstructured sources.
- **Federated query** across sources with **no bulk data duplication** (loosely-coupled first; assembled/materialized when analytics demand it).
- Preserve our existing differentiator: **grounded, cited answers** with a full retrieval path — extended across the federation boundary (SQL + AQL + source object in the citation).
- Ship as **composable building blocks** (Lego model) — each independently publishable, LLM *or* deterministic implementations both valid — not one monolith.
- Be **OSI**-aligned (open semantic interface; already implemented in the relational analyzer / ontology extractor — worth touting; 40+ companies signed on).

### 3.2 Non-Goals (for now)
- We are **not** building the customer's agent application or their agent-orchestration layer — we are the **data/ontology hub** they consult. (Demos may use a thin agent layer we don't sell.)
- We are **not** mirroring whole warehouses into Arango.
- We are **not** shipping ontology-based access control (OBAC/IAM-via-ontology) in early phases — it is a high-value **future** capability and partnership angle, flagged for research.
- **Synthetic data is deferred** — architecture first, data second (per the roadmap). Phase 1 uses the minimum synthetic footprint needed to demo.

---

## 4. Users & Use Cases

**Primary persona:** an enterprise **agent** (and the teams building agents) that needs a governed, cross-source view — e.g. a Customer-Success / sales agent reasoning over a customer's full journey.

**Seed use cases** (drive the ontology — use-case-driven, not boil-the-ocean). From [[C360 Example Questions]]:
- *Across my accounts, how should I prioritize my attention?*
- *An executive wants to meet a client — which account benefits most from C-suite time?*
- *Product has new offerings and wants feedback — which account and which people should I nominate?*
- *Across my portfolio, which accounts are missing information I should collect? Stack-rank it.*

These are the questions a CSM actually asks, they require joining structured metrics with unstructured sentiment/signals, and they are the natural anchor for the ontology's scope.

---

## 5. Architecture Overview

Two clean separation points (the team's own framing) — likely two publishable libraries / building blocks:

### 5.1 Building block A — Onto Extract layer (ontology extraction + alignment)
- Extract a **source ontology** from each source: relational schemas / catalogs (via **r2g** + the relational-schema analyzer), Snowflake/Databricks/Postgres metadata, and unstructured corpora (via the **ontology extractor**).
- **Align** per-source ontologies into a master conceptual model: extract per source → compute **diffs/deltas** → accept/reject into the master → **iterative/cyclic refinement** to convergence. Curation by agent or human per policy.
- **Belief management:** track which ontology element came from which document/schema; cascade updates/deletions on source change; **time-travel** across ontology versions; **change control** (a human or agent blesses expansions — important because the ontology drives access and business rules).
- Output is a **conceptual schema** (not an academic BFO-style ontology) with **functional mappings** to each source: concept→table, property→attribute, and value transforms (e.g. inches↔mm). The mapping *is* what becomes the query.

### 5.2 Building block B — Federated query layer
- English question → resolve concepts against the ontology → the mappings determine **which sources** must be queried → **decompose** into per-source queries → execute (SQL pushdown, AQL over Arango, or agent calls) → **reassemble** → return a validated, **cited** answer envelope.
- Analogue: the Cypher/SPARQL **transpiler** (English → Cypher → AQL), but general-purpose and federated — it must *break the query up*, run the parts, and stitch results.
- **Two query patterns:** **loosely-coupled** (pointers on the entity, fetch on demand — the Phase-1 default) and **assembled** (temporarily materialize a subgraph into Arango for graph analytics like PageRank / persistent case files, à la the JLR "contextual data fabric" case Arthur built).
- **Decomposition stance (composable):** offer both an **LLM** quick-and-dirty decomposer *and* a **deterministic** pipeline; the deterministic path is the long-term target, with the **LLM as the safety net**. The PRD does not force one — it's a blueprint choice.

### 5.3 The hub
Arango stores the master ontology, the mappings, the **canonical entities / entity resolution** (via **AER**), and *selected* context (including the already-ingested unstructured graph). It is the **router**: answer locally when possible; otherwise federate out with a cited path.

### 5.4 Reconciliation with our existing differentiator
Auto-derive the ontology (the T-box) → **compile it into the same clean named-graph + curated-AQL layer we already own** (the [[Customer360]] v3 pipeline). This automates the modeling step ("don't hand-craft") **without** losing deterministic named traversals or the citation/AQL grounding ("no black box"). The ontology then does double duty as the Arango-vs-source router.

---

## 6. Phased Approach

The near-term goal (per Arthur) drives Phase 1; later phases widen source coverage and add governance/analytics.

| Phase | Theme | Outcome |
|-------|-------|---------|
| **Phase 1 (≈1 week)** | **Federated query to one database + unstructured docs in Arango** | An English question answered by federating **one relational DB (live, not mirrored)** with the **unstructured graph already in Arango**, unified by a small use-case-driven ontology, returned **grounded + cited** with a retrieval path spanning both. Proves the "what does Arango add over A2A" story at small scale. |
| **Phase 2** | Highest-value connector + assembled pattern | **Snowflake** connector (pending free-tier check); the **assembled/materialized** query pattern for analytics; richer ontology **alignment** across ≥2 structured sources + unstructured; cost/latency instrumentation to directly answer Rah's token objection. |
| **Phase 3** | Governance + change management | **Ontology-based access control** (IAM-via-ontology / Palantir pattern) via declarative mappings; belief-management **change control**, curation workflows, and **time-travel** surfaced; **Databricks** connector. |
| **Cross-cutting** | Packaging & standards | Composable **pip-library** packaging of both building blocks; **deterministic** query-pipeline hardening; **OSI** compliance surfaced; synthetic-data generation (deferred) once the architecture is proven. |

---

## 7. Phase 1 — Detailed (the 1-week near-term goal)

**Objective:** Demonstrate a **federated query across one relational database and the unstructured documents already ingested in Arango**, unified by an auto-derived, use-case-scoped ontology, returning a grounded, cited answer whose retrieval path spans both sources — **with no bulk data moved out of the relational DB**.

### 7.1 The "one database"
**Proposed: PostgreSQL.** Rationale: Arthur already has a Postgres connector with substantial capability built in; it is the fastest path to a working proof (the roadmap explicitly says "start with Postgres for the proof"). **Snowflake is the higher-value target but is gated on a free-tier check (PJ, in progress)** and moves to Phase 2. *(Open decision §9 if the team wants to attempt Snowflake directly in Phase 1.)*

### 7.2 The unstructured side (already in Arango)
Reuse the [[Customer360]] v3 pipeline (repo `customer-context`): connectors/chunking → LangGraph extractor (A-box instances against a hand-authored T-box) → span gate → person coreference → embeddings (BM25 + vector) → **AER** entity resolution → survivorship. This graph is already query-ready; federating over it just needs **AQL generation** driven by the ontology (deterministic).

### 7.3 Phase 1 building blocks (work breakdown)

| # | Block | What it does | Proposed owner |
|---|-------|--------------|----------------|
| **B1** | **Structured → ontology** | Run r2g / relational-schema analyzer on the Postgres schema → source ontology (concepts, properties, keys). Confirm/produce the unstructured-side ontology from the Arango doc graph. | Arthur |
| **B2** | **Ontology alignment (minimal)** | Align the Postgres ontology + the unstructured ontology into a small **use-case-scoped** master (seed use cases from §4). Show the human-in-the-loop **"confirm ~2%"** step. Hand-construction of the central ontology is acceptable at this size. | Arthur + PJ |
| **B3** | **Functional mappings** | Master-ontology concept/property → Postgres table/column (with any value transforms) **and** → Arango collection/AQL. The mapping is the query. | Arthur |
| **B4** | **Federated query executor** | English → resolve concepts via ontology → decompose → generate **Postgres SQL (pushdown)** + **Arango AQL** → execute → reassemble. LLM decomposer, deterministic mapping execution, LLM as safety net. | PJ |
| **B5** | **Grounded, cited retrieval path** | Validated answer envelope + citations + retrieval path spanning **actual SQL + AQL + source objects** (extend the existing customer-360 citation/envelope + traversal viz). | PJ |
| **B6** | **Thin demo harness** | Minimal UI (reuse the customer-360 Vercel app pattern) to run 1–3 seed questions end-to-end. | PJ |

### 7.4 Phase 1 success criteria (demo)
- At least **one** (target 2–3) seed question answered **end-to-end**, federating live Postgres + the Arango unstructured graph.
- **No bulk Postgres data mirrored** into Arango — the relational facts are fetched live via pushdown; the citation shows the **actual SQL** and source object.
- The **ontology is visibly auto-derived** from the Postgres schema (+ unstructured side) and **aligned**, with the human-confirm step shown.
- The answer is **grounded**: exact citations + retrieval path across both sources; refuses if it can't cite (existing grounding gate).
- (Nice-to-have) A note/measurement on **cost/latency** vs a naive approach, to pre-empt Rah's objection.

### 7.5 Phase 1 out-of-scope
Snowflake/Databricks; assembled/materialized pattern; OBAC; belief-management change control & time-travel UX; full synthetic-data program; multi-structured-source alignment. All deferred to Phase 2+.

### 7.6 Phase 1 dependencies & risks
- **★ Make-or-break: does the ontology extractor already do structured→ontology?** OntoExtract is unstructured-only in its original form; r2g covers relational schema→ontology. **Arthur to confirm the structured→ontology path exists / is demoable before we commit.** This gates B1–B3.
- **Postgres vs Snowflake** — Phase 1 assumes Postgres; Snowflake free-tier check pending (PJ).
- **Minimal synthetic footprint** — Phase 1 needs a small synthetic Postgres schema + the existing unstructured corpus; full synthetic-data work stays deferred.
- **Repo/stabilization** — Arthur to stabilize r2g + the ontology extractor to demoable state this week (in flight).

---

## 8. Referenced Repositories (for Claude Code context)

*Pull these into Claude Code when building so it has full context (per Arthur's request). Exact GitHub URLs/handles to be confirmed by Arthur — he owns repo creation for this project and noted that the `arango-solutions` copies may lag his personal `arangodb` repos.*

- **`Contextual Data Fabric`** — the new project repo Arthur is creating (private for now). Home for the composable building blocks.
- **`customer-context`** (`arango-solutions/customer-context`) — the [[Customer360]] v3 pipeline: connectors/chunking, LangGraph extractor, span gate, person coref, embeddings (BM25 + vector), **AER** integration, survivorship, grounded/cited answer envelope + Vercel demo app. Basis for the unstructured side + demo harness (B4–B6).
- **`r2g`** (relational-to-graph) — Arthur's; the **reference application** for relational schema → ontology/graph; **implements OSI**; composes the **relational-schema analyzer (RSA)** and **Arango-schema analyzer** pip libraries. RSA is the production-grade dependency and carries the production bar; r2g itself is a well-tested reference app (CI: ruff + mypy + large unit suite + Dockerized integration) but is not held to a production operational bar (single-node; no scale/HA). **B1–B3 depend on RSA (pinned) + named, tested r2g modules — not on r2g as a whole.**
- **Ontology extractor / Arango OntoExtract (AOE)** — Arthur's; ontology extraction from unstructured (and, per the roadmap, evolved to target schemas/catalogs/Snowflake/Databricks); belief management, time-travel, SHACL/constraint extraction, cyclic refinement. Basis for the Onto Extract layer.
- **Arango Entity Resolution (AER)** — `CrossCollectionMatchingService` (blocking + Levenshtein/Jaro-Winkler → `resolvedTo`) + `WCCClusteringService`; the cross-source ER engine used in `customer-context`.
- **Relational-schema analyzer (RSA)** / **Arango-schema analyzer** — versioned pip libraries consumed by r2g and the ontology extractor. RSA is the **production-grade core** for structured→ontology; the fabric's structured building blocks (B1, M1/M2/M4) pin RSA's PyPI release + stable tool-contract bundle rather than depending on r2g internals.

---

## 9. Open Decisions for the Team

1. **Phase-1 database:** Postgres (proposed — fastest proof) vs attempt Snowflake directly (higher value, gated on free-tier). → §7.1
2. **Structured→ontology readiness (★):** confirm the ontology extractor / r2g path is demoable before committing B1–B3. → §7.6
3. **Query decomposition:** are we happy presenting LLM *and* deterministic as a composable choice, with deterministic as the long-term target? → §5.2
4. **Ontology scope:** confirm the seed CSM use cases (§4) as the Phase-1 ontology scope.
5. **Repo shape:** one repo with modules, or separate repos per building block (Onto Extract layer vs Query layer)? Arthur's call as gatekeeper.
6. **Naming:** confirm **"Contextual Data Fabric"** (also JLR's term; channel `#contextual-fabric`).

---

## 10. Process & Ownership

Composable-blueprint build: identify sub-modules → PR per sub-module → reconcile with the super-module → iterate (catches requirements drift). **Arthur is the build gatekeeper**; others review, test, and contribute per module. Standups + on-demand check-ins for decisions. This PRD is the shared contract Arthur refactors against — comment inline / via PR.

*Sources: [[2026-07-13 Zscaler Customer Context Roadmap]], [[ZScaler Feedback Summary]], [[2026-07-10 - C360 ZScaler Demo]], [[2026-07-09 - C360 Review & Feedback with Matthew]], [[2026-07-10 - ZScaler Feedback Brainstorm]], [[C360 Example Questions]].*

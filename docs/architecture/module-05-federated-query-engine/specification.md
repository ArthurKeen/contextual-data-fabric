---
title: "Module 05 — Federated Query Engine — Specification"
module: 05-federated-query-engine
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: PJ (Paul Losiewicz)
building_block: Query
depends_on_modules: ["04-mapping-layer", "01-connectors", "06-entity-resolution", "07-grounding-provenance"]
depends_on_repos: ["r2g", "customer-context"]
requires_repo_enhancements: ["r2g-federated-query"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 05 — Federated Query Engine

> Turn an English question into a **decomposed, multi-source query plan**, execute the parts against the sources that hold the data (SQL pushdown, AQL, agent calls), and **reassemble** a single grounded, cited answer — **without moving the data**.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
This is the runtime heart of the Query building block. Given a natural-language question and the master ontology + its functional mappings (from [[contextual-data-fabric/docs/architecture/module-04-mapping-layer/specification|M4]]), it decides **which sources** must be touched, **generates the per-source queries**, executes them, and **stitches** the results into one answer. It is the module that operationalizes the North Star line "ask anything, in English, across everything — without copying the data in."

## 2. Scope
**In scope:**
- Concept resolution: map question → ontology concepts/properties.
- **Query decomposition / planning:** split into per-source sub-queries using the mappings.
- **Per-source query generation:** SQL (pushdown) for relational sources, AQL for the Arango unstructured graph, agent calls where a source only exposes an agent.
- **Execution + reassembly:** run sub-queries (parallel where independent), join/reconcile results via the canonical entity hub ([[contextual-data-fabric/docs/architecture/module-06-entity-resolution/specification|M6]]).
- Two execution patterns: **loosely-coupled** (fetch on demand via pointers) and **assembled** (temporarily materialize a subgraph into Arango for analytics).
- Both a **deterministic** planner and an **LLM** planner, selectable per deployment.

**Out of scope:**
- Producing the mappings themselves → [[contextual-data-fabric/docs/architecture/module-04-mapping-layer/specification|M4 Mapping Layer]].
- The citation/answer-envelope format and refuse-if-uncited gate → [[contextual-data-fabric/docs/architecture/module-07-grounding-provenance/specification|M7 Grounding & Provenance]] (this module *feeds* it the retrieval path).
- Source connection/credentials/metadata → [[contextual-data-fabric/docs/architecture/module-01-connectors/specification|M1 Connectors]].

## 3. Interfaces (inputs / outputs)
- **Consumes:**
  - Question (string) + optional context (persona, account scope).
  - Master ontology + functional mappings (OSI/YAML) from M4.
  - Live source handles from M1 (Postgres cursor, Arango DB handle, …).
  - Canonical-entity resolution from M6 for cross-source joins.
- **Produces:**
  - An **answer payload** + a **retrieval path** object listing every sub-query executed: `{source, query_text (SQL/AQL), source_objects, rows/ids}`. M7 wraps this into the validated cited envelope.
- **Contract (proposed):** a `federate(question, ontology, mappings, sources) -> {answer, retrieval_path[]}` library call; the query plan is an inspectable intermediate object (for debugging and for the deterministic/LLM swap).

## 4. Functional requirements
- **FR-1 (P1):** Resolve a question to ontology concepts and produce a **query plan** naming the sources to hit and the join keys.
- **FR-2 (P1):** Generate and execute **SQL pushdown** against one relational DB (Postgres) using M4 mappings — filters pushed down; no bulk pull into Arango.
- **FR-3 (P1):** Generate and execute **AQL** against the Arango unstructured graph for the same question.
- **FR-4 (P1):** **Reassemble** structured + unstructured results into one answer, joined via the canonical entity hub.
- **FR-5 (P1):** Emit a complete **retrieval path** (actual SQL + AQL + source objects) for M7 to cite; refuse (via M7) if any leg is uncitable.
- **FR-6 (P1):** **LLM planner** path (quick-and-dirty decomposition) with the plan surfaced for inspection.
- **FR-7 (P2):** **Deterministic planner** path (mapping-driven decomposition; LLM only as safety net).
- **FR-8 (P2):** **Assembled** execution pattern — materialize a bounded subgraph into Arango and run graph analytics (e.g. PageRank) when the use case needs it.
- **FR-9 (P2):** **Cost/latency instrumentation** per plan (tokens, wall-clock, per-source) — directly addresses the customer's cost objection.
- **FR-10 (P3):** Multi-source planner across ≥3 sources with parallelized independent legs and cross-source join optimization.

## 5. Non-functional requirements
- **No data movement** (loosely-coupled default; assembled only on demand, bounded, temporary).
- **Grounded/cited or refused** — every fact traces to a real sub-query result.
- **Deterministic path is the long-term target; LLM is the safety net** (North Star principle 5).
- **Cost & latency are first-class** — the plan must be inspectable and measurable, not a black box.

## 6. Dependencies
- **Modules:** M4 (mappings), M1 (connectors), M6 (canonical hub), M7 (grounding).
- **Repos:** **r2g** — requires the **[[contextual-data-fabric/docs/architecture/_repo-enhancements/r2g-federated-query|r2g federated-query enhancement]]** (emit runtime mappings + per-source query generation, not just batch load). Reuses agent/query patterns from `customer-context`.

## 7. Phase mapping
- **P1:** loosely-coupled, one relational DB (Postgres) + Arango unstructured graph, LLM planner, full retrieval path.
- **P2:** assembled pattern; deterministic planner hardening; cost/latency instrumentation; Snowflake via M1.
- **P3:** multi-source planner; join optimization.

## 8. Acceptance criteria / demo (P1)
- A seed CSM question (see [[contextual-data-fabric-prd]] §4) is answered end-to-end: the engine hits **Postgres live** and the **Arango unstructured graph**, joins on the canonical entity, and returns an answer whose **retrieval path shows the actual SQL and AQL** and the source objects. No Postgres bulk copy into Arango. Refuses cleanly if a leg can't be cited.

## 9. Open questions
- LLM vs deterministic **for P1** — default to LLM planner to hit the 1-week goal, deterministic in P2? (Matches PRD §5.2.)
- Join placement: reconcile in the engine vs push a join key to the source. Start with engine-side join via canonical hub.
- Plan representation: is the "query graph over the ontology" (à la a TigerGraph-style query graph) the right intermediate? (Raised in the roadmap transcript.)

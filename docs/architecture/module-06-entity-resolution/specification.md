---
title: "Module 06 — Entity Resolution / Canonical Hub — Specification"
module: 06-entity-resolution
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: PJ (Paul Losiewicz)
building_block: Both
depends_on_modules: ["01-connectors", "05-federated-query-engine"]
depends_on_repos: ["arango-entity-resolution", "customer-context"]
requires_repo_enhancements: ["aer-semantic-federated"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 06 — Entity Resolution / Canonical Hub

> Resolve the same real-world entity across sources into a **canonical entity** in Arango, so a federated answer can join structured + unstructured facts about one thing.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
The join fabric. When the query engine pulls a fact from Postgres and a signal from the Arango graph, this module guarantees they're about the *same* account/person/contract via a **canonical entities** collection. Wraps **AER** (`CrossCollectionMatchingService` + `WCCClusteringService`) with the guards and survivorship the library lacks (e.g. `assert_no_cross_account`, per-domain authority + `whyLost` provenance) — reusing what already exists in `customer-context`.

## 2. Scope
**In scope:** cross-source matching (blocking + similarity), clustering into canonical entities, no-cross-account guard, survivorship/provenance for citations, the canonical-hub collection the query engine joins through.
**Out of scope:** query decomposition (M5); ontology/mappings (M2–M4); the citation envelope format (M7).

## 3. Interfaces (inputs / outputs)
- **Consumes:** entities/records surfaced by connectors + the unstructured graph.
- **Produces:** canonical entities + `resolvedTo` linkages, and a `resolve(entity) -> canonical_id` surface the query engine uses to join across sources.

## 4. Functional requirements
- **FR-1 (P1):** Reuse AER + the `customer-context` canonical-hub pattern to resolve entities between the Postgres side and the Arango unstructured graph for the seed use cases.
- **FR-2 (P1):** No-cross-account over-merge guard (fail closed).
- **FR-3 (P1):** Survivorship + `whyLost` provenance available for M7 citations.
- **FR-4 (P2):** **Semantic** (non-deterministic) matching beyond exact/deterministic — see enhancement.
- **FR-5 (P3):** **Federation-aware ER** — resolve against records fetched live from a source at query time, not only pre-ingested ones.

## 5. Non-functional requirements
Precision-first (over-merge in front of a customer is the failure mode); every merge explainable (provenance for citations); no bulk data movement (resolve on the keys/attributes needed).

## 6. Dependencies
- **Repos:** `arango-entity-resolution` (AER v3.5.1) — see [[contextual-data-fabric/docs/architecture/_repo-enhancements/aer-semantic-federated|enhancement]]; `customer-context` (existing canonical-hub + survivorship + guards). **v0.2 notes:** AER already ships vector/ANN blocking + LLM match verification, so FR-4 is configuration + demo-safety hardening rather than a build. Also: **AOE's internal ER is hand-rolled** (its full AER integration is deferred), so two ER implementations coexist in the fabric — AER for the canonical hub (this module), AOE's for extraction-time concept dedup. Acceptable short-term; converge on AER when AOE's integration lands.

## 7. Phase mapping
- **P1:** reuse existing deterministic ER + canonical hub.
- **P2:** semantic matching.
- **P3:** federation-aware ER at query time.

## 8. Acceptance criteria / demo (P1)
- A seed question joins a Postgres account fact with an unstructured sentiment signal via a single canonical entity; the merge is guarded and explainable.

## 9. Open questions
- Batch (pre-resolve) vs runtime (resolve during federation) — the industry-unsolved placement question; start batch, move toward runtime.
- Threshold/quality bar for semantic matching before it's demo-safe.

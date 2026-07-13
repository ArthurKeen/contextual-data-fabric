---
title: "Module 04 — Mapping Layer — Specification"
module: 04-mapping-layer
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: Arthur Keen
building_block: Both
depends_on_modules: ["03-ontology-alignment", "01-connectors"]
depends_on_repos: ["r2g"]
requires_repo_enhancements: ["r2g-federated-query"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 04 — Mapping Layer

> The **functional mappings** from the master ontology to each source: concept→table, property→attribute, value→transform. **The mapping is the query.**
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
Hold the bridge between the conceptual model (master ontology) and physical sources. For every ontology concept/property, record how it is realized in each source — including value transformations (e.g. inches↔mm, scale/units). These mappings are what the Federated Query Engine (M5) compiles into pushdown queries; they are also what makes the same concept resolvable across multiple systems.

## 2. Scope
**In scope:** concept→table, property→column, value-transform functions; multi-source mappings for one concept (a concept realized in Postgres *and* the Arango graph); OSI/YAML representation.
**Out of scope:** aligning the ontology (M3); generating/executing the actual queries at runtime (M5/r2g); connection specifics (M1).

## 3. Interfaces (inputs / outputs)
- **Consumes:** master ontology (M3); source metadata (M1).
- **Produces:** a **mapping artifact** (OSI/YAML) keyed by ontology element → per-source realization + transforms, consumed by M5 (and r2g's query generation).

## 4. Functional requirements
- **FR-1 (P1):** Concept/property → Postgres table/column mappings for the seed use cases.
- **FR-2 (P1):** Concept/property → Arango collection/field (AQL) mappings for the unstructured side.
- **FR-3 (P1):** Represent a **single concept mapped to >1 source** (the join point the canonical hub resolves).
- **FR-4 (P2):** **Value-transform** functions (units/scale/format), expressed general-purpose then compiled per dialect.
- **FR-5 (P2):** Snowflake mappings.
- **FR-6 (P2):** OSI-compliant export/import so the mappings interoperate with the open semantic interface.

## 5. Non-functional requirements
Declarative, inspectable (the mapping is cited alongside the query it becomes); OSI-aligned; deterministic compilation to source dialects, LLM assist only where needed.

## 6. Dependencies
- **Repos:** `r2g` — emits/consumes the mappings; requires the [[contextual-data-fabric/docs/architecture/_repo-enhancements/r2g-federated-query|federated-query enhancement]] (mappings usable at runtime, not just for batch load).

## 7. Phase mapping
- **P1:** Postgres + Arango mappings for seed use cases; single-concept-multi-source.
- **P2:** value transforms; Snowflake; full OSI export.
- **P3:** transform library; governance annotations feed M8.

## 8. Acceptance criteria / demo (P1)
- For each seed-use-case concept, a mapping artifact resolves it to the correct Postgres columns and/or Arango fields, including the cross-source join point — and M5 can compile it into a working pushdown query.

## 9. Open questions
- Mapping expression language: source-native (SQL) vs a general-purpose expression (spreadsheet-formula style) compiled per dialect? (Roadmap transcript leaned general-purpose.)
- Where value transforms execute — at the source, in the engine, or at ingest.

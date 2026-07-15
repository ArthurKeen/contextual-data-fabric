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
- **Produces:** a **mapping artifact** keyed by ontology element → per-source realization + transforms, consumed by M5 (and r2g's query generation). **Canonical form (ADR-0001 #3): `CSI v1`** — the cross-tool interchange defined in `arangodb-schema-analyzer` (`{conceptualModel, physicalMapping, provenance{direction}}`), with **r2g as the forward producer** (P12.1) — plus two serializations: **CSI → R2RML** for the SQL/Ontop leg and **CSI → MappingBundle/OWL-Turtle** for the AQL transpilers (`arango-sparql-py` / `arango-cypher-py`). OSI/YAML remains the interop export (FR-6). Known bug to absorb: the `phys:` namespace mismatch between `arango-sparql-py` and the analyzers (ADR-0001 #3.4).

## 4. Functional requirements
- **FR-1 (P1):** Concept/property → Postgres table/column mappings for the seed use cases.
- **FR-2 (P1):** Concept/property → Arango collection/field (AQL) mappings for the unstructured side.
- **FR-3 (P1):** Represent a **single concept mapped to >1 source** (the join point the canonical hub resolves).
- **FR-4 (P2):** **Value-transform** functions (units/scale/format), expressed general-purpose then compiled per dialect.
- **FR-5 (P2):** Snowflake mappings.
- **FR-6 (P2):** OSI-compliant export/import so the mappings interoperate with the open semantic interface.
- **FR-7 (P1 minimal / P2 full):** **Mapping versioning** (PRD §10.3 / CC-3) — since the mapping *is* the query, an unversioned mapping is an unversioned query. P1: the artifact carries a version/hash that is cited in the answer envelope. P2: mapping versions align with ontology versions via the same temporal pattern AOE already uses.

## 5. Non-functional requirements
Declarative, inspectable (the mapping is cited alongside the query it becomes); OSI-aligned; deterministic compilation to source dialects, LLM assist only where needed. **Credential-free by construction (CC-7):** every mapping artifact (CSI v1, R2RML, MappingBundle) references sources by **logical name only** — never a connection string, JDBC URL, or token — because mappings are versioned, cited in envelopes, and shared with external engines (Ontop); credential resolution is exclusively M1's `SecretResolver` (M1 FR-7).

## 6. Dependencies
- **Repos:** `r2g` — emits/consumes the mappings; requires the [[contextual-data-fabric/docs/architecture/_repo-enhancements/r2g-federated-query|federated-query enhancement]] (mappings usable at runtime, not just for batch load).

## 7. Phase mapping
- **P1:** Postgres + Arango mappings for seed use cases; single-concept-multi-source.
- **P2:** value transforms; Snowflake; full OSI export.
- **P3:** transform library; governance annotations feed M8.

## 8. Acceptance criteria / demo (P1)
- For each seed-use-case concept, a mapping artifact resolves it to the correct Postgres columns and/or Arango fields, including the cross-source join point — and M5 can compile it into a working pushdown query.

## 9. Open questions
- ~~Mapping expression language: source-native (SQL) vs general-purpose compiled per dialect?~~ **Largely answered (v0.2): general-purpose exists** — r2g's field-expression engine (Phase 5c; reused by Phase 9b masking) already expresses transforms source-agnostically. Remaining: adopt it as-is for M4 or wrap it behind the OSI/YAML artifact.
- ~~Mapping artifact representation~~ **Answered (v0.3) by ADR-0001 #3 code-read: `CSI v1` is the hub** (already designed in `arangodb-schema-analyzer` with r2g named as forward producer); the four adapters (r2g forward-CSI emitter, CSI→R2RML, CSI→MappingBundle, `phys:` namespace fix) are M5 plan WPs A1–A4.
- Where value transforms execute — at the source, in the engine, or at ingest.
- Where mapping artifacts live at runtime (file vs Arango collection) — coordinate with the master-ontology store decision (PRD §9.9 / §10.3).

---
title: "Module 02 — Ontology Extraction — Specification"
module: 02-ontology-extraction
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: Arthur Keen
building_block: Onto Extract
depends_on_modules: ["01-connectors"]
depends_on_repos: ["r2g", "ontology-extractor"]
requires_repo_enhancements: ["ontology-extractor-structured"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 02 — Ontology Extraction

> Produce a **per-source ontology** from each source — relational schemas/catalogs via **r2g**, unstructured corpora via the **ontology extractor** — as OSI/YAML conceptual schemas. "Structured data in, ontology out."
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
The extraction half of the Onto Extract building block. For each source, emit a **source ontology** (concepts, properties, keys, relationships) plus provenance (which schema/doc each element came from). Extraction is **use-case-driven** — scoped to the seed use cases, not boil-the-ocean.

## 2. Scope
**In scope:** relational/catalog → ontology (r2g); unstructured docs → ontology (AOE); use-case scoping of extraction; provenance capture per element.
**Out of scope:** merging the per-source ontologies (that's M3 Alignment); mappings to sources (M4); instance-level entity resolution (M6).

## 3. Interfaces (inputs / outputs)
- **Consumes:** metadata bundles + sample rows from M1 (structured); the ingested doc graph (unstructured).
- **Produces:** one **source ontology** per source (OSI/YAML) + element provenance, handed to M3.

## 4. Functional requirements
- **FR-1 (P1):** Structured→ontology from a Postgres metadata bundle (r2g) — concepts, properties, keys, FK relationships.
- **FR-2 (P1):** Unstructured→ontology from the Arango doc graph (AOE) — the domains present in the corpus.
- **FR-3 (P1):** **Use-case-scoped** extraction driven by the seed CSM use cases ([[contextual-data-fabric-prd]] §4).
- **FR-4 (P1):** Capture **provenance** per ontology element (source schema/column or document) — required by M3's belief management.
- **FR-5 (P2):** Multi-structured-source extraction (Snowflake) + catalog/semantic-layer (dbt) inputs.
- **FR-6 (P2):** LLM-as-judge scoring of extracted elements (importance/weights), as demoed in the ontology extractor.

## 5. Non-functional requirements
Oracle-free / grounded to the source; OSI/YAML output; conceptual schemas (agent-usable), not academic ontologies; deterministic where possible with LLM assist.

## 6. Dependencies
- **Repos:** **RSA** (`relational-schema-analyzer`, structured introspection — the production-grade core), `ontology-extractor`/AOE (SQL→OWL/SHACL mapping + unstructured), `r2g` (reference application; Phase 10 LLM-assisted derivation + human review UI). **Resolved (v0.2):** the structured→ontology path **exists** — AOE owns the SQL→OWL/SHACL mapping, RSA is the read-only physical-schema introspector; the remaining P1 work is wiring the RSA-bundle→AOE handoff — see [[contextual-data-fabric/docs/architecture/_repo-enhancements/ontology-extractor-structured|enhancement]] RE-1.

## 7. Phase mapping
- **P1:** Postgres schema + unstructured corpus → two source ontologies.
- **P2:** Snowflake + catalog inputs; scoring.
- **P3:** —

## 8. Acceptance criteria / demo (P1)
- Feeding the Phase-1 Postgres metadata bundle produces a reviewed source ontology; the unstructured corpus produces its own; both carry element provenance. The human-in-the-loop "confirm ~2%" step is visible.

## 9. Open questions
- ~~Does the ontology extractor already ingest structured metadata, or does r2g fully own structured→ontology?~~ **Answered (v0.2): AOE ingests structured metadata and owns the SQL→OWL/SHACL mapping; RSA introspects; r2g is the composing reference app.** The gate is lifted.
- Which path B1 demos with: RSA bundle → AOE mapping (proposed, matches the documented split) vs r2g Phase 10 derivation. → PRD §9.2.
- Extraction scoping mechanism — how the use cases constrain what's extracted.

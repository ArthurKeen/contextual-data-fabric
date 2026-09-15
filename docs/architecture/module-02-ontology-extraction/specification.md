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
depends_on_repos: ["r2g", "relational-schema-analyzer", "arangodb-schema-analyzer", "ontology-extractor"]
requires_repo_enhancements: []
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 02 — Ontology Extraction

> Produce a **per-source ontology** from each source — relational schemas/catalogs via **r2g + RSA** (emitted as CSI v1 + R2RML), the Arango hub via **ASA** (reverse CSI), unstructured corpora via the **ontology extractor** (AOE) — as conceptual schemas. "Structured data in, ontology out."
> **Corrected 2026-09-14 (code-read):** CDF's structured path never calls AOE. See §6 and §9.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
The extraction half of the Onto Extract building block. For each source, emit a **source ontology** (concepts, properties, keys, relationships) plus provenance (which schema/doc each element came from). Extraction is **use-case-driven** — scoped to the seed use cases, not boil-the-ocean.

## 2. Scope
**In scope:** relational/catalog → ontology (r2g, deriving through RSA; CSI v1 + R2RML out); Arango graph → reverse CSI (ASA, `deploy/arango/export_csi.py`); unstructured docs → ontology (AOE); use-case scoping of extraction; provenance capture per element.
**Out of scope:** merging the per-source ontologies (that's M3 Alignment); mappings to sources (M4); instance-level entity resolution (M6).

## 3. Interfaces (inputs / outputs)
- **Consumes:** metadata bundles + sample rows from M1 (structured); the ingested doc graph (unstructured).
- **Produces:** one **source ontology** per source (OSI/YAML) + element provenance, handed to M3.

## 4. Functional requirements
- **FR-1 (P1, shipped):** Structured→ontology from a relational schema via **r2g → RSA → CSI v1 + R2RML** — concepts, properties, keys, FK relationships. Every relational CSI in `deploy/csi/` carries `provenance.producer: r2g`. This is the path CDF runs; AOE is not on it.
- **FR-2 (P1):** Unstructured→ontology from the Arango doc graph (AOE) — the domains present in the corpus.
- **FR-3 (P1):** **Use-case-scoped** extraction driven by the seed CSM use cases ([[contextual-data-fabric-prd]] §4) — mechanism: **competency questions** per AOE PRD §6.19 (FR-19.4 scope injection): the CQ term set is injected into extraction as required/priority concepts, uniformly across relational, graph, and unstructured adapters.
- **FR-4 (P1):** Capture **provenance** per ontology element (source schema/column or document) — required by M3's belief management.
- **FR-5 (P2):** Multi-structured-source extraction (Snowflake) + catalog/semantic-layer (dbt) inputs.
- **FR-5a (P2, new 2026-07-22):** **Purpose-scoped extraction at enterprise scale** — an integration carries a declared **purpose** (ORSD purpose statement + CQs, AOE FR-19.1); at thousands-of-tables scale the extractor **ranks tables by relevance** (CQ-term/COMMENT embedding similarity, FK-neighborhood expansion, `ACCESS_HISTORY` usage signals, governance tags) and introspects only the curator-confirmed set — the ~2% human-confirm pattern applied to table selection before extraction, not just to concepts after it. Mechanism: schema-analyzers RE-6.
- **FR-6 (P2):** LLM-as-judge scoring of extracted elements (importance/weights), as demoed in the ontology extractor.

## 5. Non-functional requirements
Oracle-free / grounded to the source; OSI/YAML output; conceptual schemas (agent-usable), not academic ontologies; deterministic where possible with LLM assist.

## 6. Dependencies
- **Repos (as built, verified 2026-09-14):** **r2g** (the forward producer CDF consumes: introspects via RSA's connectors, derives the conceptual model through RSA in `rsa_ontology.py`, emits CSI v1 + R2RML; RSA is a core dependency, band `>=0.8.0,<0.9.0`), **ASA** (`arangodb-schema-analyzer`, reverse CSI for the Arango hub via `deploy/arango/export_csi.py`), **RSA** (`relational-schema-analyzer`, reached only through r2g; CDF also carries an optional RSA-bundle→CSI adapter, `src/cdf/catalog/adapters/rsa.py`, unused by default). `ontology-extractor`/AOE is the **unstructured** producer only. CDF imports none of these at runtime; it reads their artifacts.
- **Superseded (v0.2 → 2026-09-14):** the v0.2 resolution "AOE owns the SQL→OWL/SHACL mapping, RSA introspects, wire the RSA-bundle→AOE handoff" described AOE's own scope, not CDF's pipeline. AOE's SQL→OWL path exists (`relational_schema_extraction.py`, RSA behind the optional `[relational]` extra) but CDF never calls it, so the estate holds **two independent structured→ontology derivations** (RSA's conceptual model via r2g; AOE's table→`owl:Class` mapping) that can drift on naming and relationship inference with nothing to catch it. The [[contextual-data-fabric/docs/architecture/_repo-enhancements/ontology-extractor-structured|enhancement]] RE-1 handoff is therefore not what CDF depends on; the reconciliation plan is `docs/research/unified-ontology-mapping-architecture.md` §9 steps 3–4 (AOE reads CSI; type detection converges on ASA).

## 7. Phase mapping
- **P1:** Postgres schema + unstructured corpus → two source ontologies.
- **P2:** Snowflake + catalog inputs; scoring.
- **P3:** —

## 8. Acceptance criteria / demo (P1)
- Feeding the Phase-1 Postgres metadata bundle produces a reviewed source ontology; the unstructured corpus produces its own; both carry element provenance. The human-in-the-loop "confirm ~2%" step is visible.

## 9. Open questions
- ~~Does the ontology extractor already ingest structured metadata, or does r2g fully own structured→ontology?~~ **Answered (v0.2), corrected (2026-09-14):** both exist independently. **CDF runs r2g → RSA**; AOE's structured path is a parallel derivation CDF never calls. The gate is lifted, but the two-derivations drift is now the open item below.
- ~~Which path B1 demos with: RSA bundle → AOE mapping vs r2g Phase 10 derivation.~~ **Settled by what shipped:** r2g → RSA → CSI/R2RML (every relational CSI in `deploy/csi/` is r2g-produced).
- **Two structured→ontology derivations, no drift detector.** RSA's conceptual model (via r2g, the one CDF uses) and AOE's table→`owl:Class` mapping (over RSA's `PhysicalSchema`) can disagree on naming (CC-12) and on inferred relationships. **Progress (2026-09-14):** paper steps 3–4 are done upstream — AOE imports CSI v1 as an ontology (`app/services/csi_import.py`; `POST /api/v1/ontology/schema/csi/preview` and `/import`; MCP tools), so curation can happen over the same artifact CDF consumes, and ASA 0.14.0 converged LPG type detection. **Still open:** (a) retire AOE's own structured type detector in favour of the ASA-derived one, and (b) a drift detector that compares the two structured→ontology derivations for the same source. Until both land, treat any AOE-curated ontology as *not* the one the fabric queries.
- ~~Extraction scoping mechanism~~ **Answered (v0.3) by AOE PRD §6.19:** use cases are formalized as **competency questions** (ORSD-style, human-authored/LLM-assisted); the CQ term set scopes extraction (FR-19.4) and CQ test queries validate coverage afterward (FR-19.5) — the same spec drives M10's golden set.

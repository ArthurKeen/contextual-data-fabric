---
title: "Module 03 — Ontology Alignment — Specification"
module: 03-ontology-alignment
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: Arthur Keen
building_block: Onto Extract
depends_on_modules: ["02-ontology-extraction"]
depends_on_repos: ["ontology-extractor"]
requires_repo_enhancements: ["ontology-extractor-structured"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 03 — Ontology Alignment

> Merge the per-source ontologies into **one master conceptual model**: compute diffs/deltas, accept/reject into the master, refine iteratively to convergence — with belief management, versioning, and change control.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
The alignment half of the Onto Extract building block, and the answer to the customer's "ontology overlap across domains" concern. Takes N source ontologies (from M2) and reconciles them into the master that everything else queries against — resolving `customer account` vs `client account` vs `account` into one concept, wiring taxonomies so there are no orphan classes.

## 2. Scope
**In scope:** diff/delta computation between a source ontology and the master; accept/reject decisions (agent or human per policy); iterative/cyclic refinement to convergence; **belief management** (element provenance, dependency-directed cascades on source change); **versioning/time-travel**; change control on expansion.
**Out of scope:** extracting the source ontologies (M2); mappings to sources (M4); access-control policy on the ontology (M8).

## 3. Interfaces (inputs / outputs)
- **Consumes:** per-source ontologies + provenance from M2.
- **Produces:** the **master ontology** (OSI/YAML) + a change log/belief state, consumed by M4 (mappings) and M5/M8.

## 4. Functional requirements
- **FR-1 (P1, minimal):** Align the Postgres ontology + the unstructured ontology into a **small, use-case-scoped master** — hand-construction of the central ontology is acceptable at this size.
- **FR-2 (P1):** Surface diffs/deltas + the human-confirm ("~2%") step.
- **FR-3 (P2):** Automated diff→accept/reject with **iterative refinement** across ≥2 structured sources + unstructured.
- **FR-4 (P3):** **Belief management** — track element provenance; cascade updates/removals when a source schema/doc changes.
- **FR-5 (P3):** **Time-travel** across ontology versions + **change control** (bless expansions before release; agent or human per policy).

## 5. Non-functional requirements
Small-but-high-value ontology (taxonomies consistent, no orphan classes); every element traceable to its source(s); expansion is governed (the ontology drives access + business rules downstream).

## 6. Dependencies
- **Repos:** `ontology-extractor`/AOE — provides alignment, belief-management, time-travel, and cyclic-refinement primitives; see [[contextual-data-fabric/docs/architecture/_repo-enhancements/ontology-extractor-structured|enhancement]].

## 7. Phase mapping
- **P1:** minimal alignment (small master, possibly hand-constructed).
- **P2:** automated diff/refinement across multiple sources.
- **P3:** belief management, time-travel, change control.

## 8. Acceptance criteria / demo (P1)
- The two Phase-1 source ontologies are merged into one small master that the seed use cases can be answered against; overlapping concepts are unified; the confirm step is shown.

## 9. Open questions
- How much hand-construction is acceptable for P1 vs waiting on automated alignment.
- Curation policy: when do agents auto-accept deltas vs require a human? (Company-policy dependent.)

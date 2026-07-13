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
- **FR-1 (P1):** Align the Postgres ontology + the unstructured ontology into a **small, use-case-scoped master** — **hand-construction of the central ontology is the P1 plan** (not a fallback): the AOE alignment API is confirmed *not built* (v0.2; building blocks exist — union + conflict flagging, overlap-candidate finder, pairwise merge — but no orchestration).
- **FR-2 (P1):** Surface diffs/deltas + the human-confirm ("~2%") step (r2g Phase 10's review UI and AOE's curation workspace are existing surfaces for this).
- **FR-3 (P2):** Automated diff→accept/reject with **iterative refinement** across ≥2 structured sources + unstructured — delivered by the AOE alignment API (`arango-ontoextract/docs/multi-source-alignment.md`, Part A).
- **FR-4 (P3, narrowed v0.2):** **Source-change cascade** — belief revision on new evidence is already built in AOE (§6.16: verdicts, Levi-identity revisions, Revisions Inbox, MCP tools); the remaining build is cascading updates/retractions when a **source schema/doc changes or is deleted** (AOE alignment spec, Part B).
- **FR-5 (P3, narrowed v0.2):** Time-travel **exists** (AOE VCR timeline, snapshots, diffs). Remaining: **programmatic change-control hooks** — bless-before-release on expansions, agent or human per policy.

## 5. Non-functional requirements
Small-but-high-value ontology (taxonomies consistent, no orphan classes); every element traceable to its source(s); expansion is governed (the ontology drives access + business rules downstream).

## 6. Dependencies
- **Repos:** `ontology-extractor`/AOE — provides belief-revision, time-travel, and curation primitives **today**; the **alignment orchestration is the build** (v0.2 verified — see [[contextual-data-fabric/docs/architecture/_repo-enhancements/ontology-extractor-structured|enhancement]] §1 and `arango-ontoextract/docs/multi-source-alignment.md`). Master-ontology storage proposal: AOE's ArangoRDF-PGT + temporal substrate (PRD §10.3).

## 7. Phase mapping
- **P1:** minimal alignment (small master, possibly hand-constructed).
- **P2:** automated diff/refinement across multiple sources.
- **P3:** belief management, time-travel, change control.

## 8. Acceptance criteria / demo (P1)
- The two Phase-1 source ontologies are merged into one small master that the seed use cases can be answered against; overlapping concepts are unified; the confirm step is shown.

## 9. Open questions
- How much hand-construction is acceptable for P1 vs waiting on automated alignment.
- Curation policy: when do agents auto-accept deltas vs require a human? (Company-policy dependent.)

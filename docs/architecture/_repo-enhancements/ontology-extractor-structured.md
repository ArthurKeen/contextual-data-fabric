---
title: "Repo Enhancement — ontology-extractor (AOE) — Structured Input + Alignment/Belief APIs"
repo: ontology-extractor
type:
  - internal
  - repo-enhancement-spec
status: draft
version: 0.1
owner: Arthur Keen
serves_modules: ["02-ontology-extraction", "03-ontology-alignment", "08-governance-obac"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Repo Enhancement — ontology-extractor (AOE): structured input + alignment/belief APIs

> **Requirement (one line):** confirm/complete the **structured-data → ontology** path, and expose **alignment**, **belief-management**, and **time-travel** as APIs the fabric's M2/M3 (and later M8) can call.
>
> **This is the make-or-break dependency** flagged in the [[ZScaler Feedback Summary]] — gate Phase-1 commitments on RE-1.

## 1. Current state (as understood — Arthur to confirm)
The ontology extractor (AOE) extracts ontologies from **unstructured** corpora, with belief management (element provenance, dependency-directed cascades), time-travel across versions, cyclic/iterative refinement, LLM-as-judge scoring, and SHACL/constraint extraction. Per the roadmap it has **evolved** to also target schemas, data catalogs, Snowflake, and Databricks — but the structured→ontology path needs confirmation and may be partially owned by r2g.

## 2. Why the change
Customer Ask #1 is "structured data in, ontology out" and auto-derivation across **both** structured and unstructured sources. The fabric needs a single extraction surface (M2) and an alignment surface (M3) that treat structured and unstructured uniformly, plus the belief/time-travel machinery for change management (M3/P3).

## 3. Required enhancements
- **RE-1 (P1) ★:** Confirm/complete **structured metadata → ontology** (schema, keys, catalog, sample rows) producing OSI/YAML with element provenance. If r2g owns this, define the clean handoff so M2 has one surface.
- **RE-2 (P1):** **Alignment API** — given N source ontologies, compute diffs/deltas and produce/refine a master (the primitive M3 wraps). Minimal for P1.
- **RE-3 (P2):** Iterative/cyclic refinement across ≥2 structured + unstructured, callable programmatically (agent-curatable).
- **RE-4 (P3):** **Belief-management API** — element provenance + dependency-directed cascade on source change; expose to M3.
- **RE-5 (P3):** **Time-travel API** — enumerate/diff/roll ontology versions; change-control hooks (bless before release).
- **RE-6 (future):** SHACL/constraint export for governance (M8).

## 4. Interface contract (with M2 / M3)
- **Input:** metadata bundle (structured, from M1) or corpus handle (unstructured).
- **Output:** source ontology (OSI/YAML) + provenance; alignment ops return a master + change log/belief state.

## 5. Phase mapping
- **P1:** RE-1 (★), RE-2 (minimal).
- **P2:** RE-3.
- **P3:** RE-4, RE-5; RE-6 future.

## 6. Acceptance criteria (P1)
- Feeding the Phase-1 Postgres metadata bundle yields a reviewed source ontology with provenance; the alignment API merges it with the unstructured ontology into a small master.

## 7. Open questions / for Arthur
- Does AOE already do structured→ontology, or is that r2g's job with AOE doing alignment only? Define the split.
- Which repo copy to build against (`arango-solutions` vs personal `arangodb`; solutions may lag).

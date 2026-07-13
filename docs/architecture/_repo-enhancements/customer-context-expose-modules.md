---
title: "Repo Enhancement — customer-context — Expose Unstructured Graph, Grounding Envelope & AQL Generation"
repo: customer-context
type:
  - internal
  - repo-enhancement-spec
status: draft
version: 0.1
owner: PJ (Paul Losiewicz)
serves_modules: ["01-connectors", "05-federated-query-engine", "07-grounding-provenance", "09-demo-harness"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Repo Enhancement — customer-context: expose the unstructured graph, grounding envelope & ontology-driven AQL

> **Requirement (one line):** refactor the [[Customer360]] v3 pipeline so its **unstructured graph**, its **grounding/citation envelope + gate**, and its **traversal UI** are consumable as **modules** by the fabric — and add **ontology-driven AQL generation** so the query engine can federate over it.

## 1. Current state
`arango-solutions/customer-context` is the v3 Customer 360 pipeline: connectors/chunking → LangGraph extractor (A-box against a hand-authored T-box) → span gate → person coref → embeddings (BM25 + vector) → **AER** entity resolution → survivorship, plus a grounded/cited answer envelope, a deterministic grounding gate, and a Vercel citation/traversal UI. Today it is a self-contained app with a hand-modeled structured graph.

## 2. Why the change
The fabric reuses three assets from this repo — the **unstructured graph** (as a federation source, M1/M5), the **grounding envelope + gate** (M7), and the **demo UI** (M9). They need to be callable modules rather than app-internal, and AQL over the unstructured side must be **driven by the master ontology/mappings** rather than curated per question.

## 3. Required enhancements
- **RE-1 (P1):** Expose the **unstructured graph** as a federation source with an `execute(AQL)` surface (consumed by M1/M5).
- **RE-2 (P1):** Extract the **grounding gate + validated envelope schema** as a reusable module (M7), extended to carry **multi-source** citations (SQL + AQL + source object).
- **RE-3 (P1):** Extract the **traversal/citation UI** for the demo harness (M9), able to render a cross-source retrieval path.
- **RE-4 (P1):** **Ontology-driven AQL generation** — generate the unstructured-side AQL from the master ontology/mappings (M4) instead of hand-curated queries.
- **RE-5 (P2):** Retire/parameterize the hand-modeled structured graph in favor of the federated Postgres/Snowflake path (the structured side becomes federated, not mirrored).

## 4. Interface contract
- **Provides:** AQL execution over the unstructured graph; `groundedEnvelope(retrievalPath) -> {answer, claims, citations[], retrievalPath[]}`; a UI component for cross-source paths.
- **Consumes:** master ontology + mappings (M4) for AQL generation.

## 5. Phase mapping
- **P1:** RE-1..RE-4 (the demo runs on this + Postgres).
- **P2:** RE-5.

## 6. Acceptance criteria (P1)
- The query engine federates over the customer-context unstructured graph via generated AQL, and answers render through the reused grounding envelope + UI with cross-source citations.

## 7. Open questions / for PJ
- How much of the current app is refactored into modules vs wrapped in place for P1 speed.
- Envelope schema changes for multi-source citations (coordinate with M7).

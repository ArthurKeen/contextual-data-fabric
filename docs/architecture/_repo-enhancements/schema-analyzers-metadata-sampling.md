---
title: "Repo Enhancement — schema-analyzers — Metadata Sampling API"
repo: schema-analyzers (relational-schema-analyzer + arango-schema-analyzer)
type:
  - internal
  - repo-enhancement-spec
status: draft
version: 0.1
owner: Arthur Keen
serves_modules: ["01-connectors", "02-ontology-extraction", "04-mapping-layer"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Repo Enhancement — schema-analyzers: metadata sampling API

> **Requirement (one line):** expose a clean **metadata-sampling API** (schema, keys, catalog, sample rows) that the Connectors (M1) call and that feeds Ontology Extraction (M2) and the Mapping Layer (M4) — without pulling bulk data.

## 1. Current state
The **relational-schema analyzer** and **arango-schema analyzer** are pip libraries consumed by r2g and the ontology extractor. They analyze schema/structure to support ontology construction.

## 2. Why the change
The fabric needs a **source-agnostic metadata bundle** so M2 can extract ontologies uniformly across sources and M1 can offer a "metadata sampling connector" (raised in the roadmap) that hydrates our mappings/ontology without moving the underlying data.

## 3. Required enhancements
- **RE-1 (P1):** A stable **metadata-bundle output** (tables, columns, types, keys/FKs, sample rows, catalog/semantic-layer defs where present) in a documented, source-agnostic shape.
- **RE-2 (P1):** **Sampling controls** (row-sample size/limits) so analysis is cheap and no bulk data is read.
- **RE-3 (P2):** Snowflake/Databricks metadata support behind the same bundle shape.
- **RE-4 (P2):** Incremental re-analysis on schema change (feeds M3 belief management).

## 4. Interface contract (with M1 / M2 / M4)
- **Input:** a source connection (from M1).
- **Output:** the metadata bundle (RE-1) consumed by M2 (extraction) and M4 (mappings).

## 5. Phase mapping
- **P1:** RE-1, RE-2 for Postgres + Arango.
- **P2:** RE-3, RE-4.

## 6. Acceptance criteria (P1)
- The Postgres connector calls the analyzer and gets a complete metadata bundle (schema + keys + sampled rows) that M2 turns into a source ontology — with no bulk table read.

## 7. Open questions / for Arthur
- Is the metadata-bundle shape already standardized across the two analyzers, or does it need unifying?
- Which repo copy is canonical for these pip libs.

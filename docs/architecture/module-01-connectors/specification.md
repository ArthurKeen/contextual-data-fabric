---
title: "Module 01 — Connectors — Specification"
module: 01-connectors
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: TBD
building_block: Both
depends_on_modules: []
depends_on_repos: ["schema-analyzers", "r2g", "customer-context"]
requires_repo_enhancements: ["schema-analyzers-metadata-sampling"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 01 — Connectors

> Source adapters that provide two things: **metadata/schema** (for ontology extraction) and **live query access** (for federated query) — **without bulk-copying data** into Arango.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
Own the boundary to each external source. Two distinct jobs: (a) a **metadata-sampling** path that hands schema/catalog/sample-rows to Ontology Extraction (M2), and (b) a **live query** path that lets the Federated Query Engine (M5) push a query down and stream results back. Credentials, dialects, and connection lifecycle live here so no other module deals with source specifics.

## 2. Scope
**In scope:** connection + auth; metadata sampling (schema, keys, catalog, sample rows); live query execution handles; per-source dialect concerns. Sources: **Postgres (P1)**, the **Arango unstructured graph (P1, in-process)**, **Snowflake (P2)**, **Databricks (P3)**.
**Out of scope:** mapping concepts→tables (M4); generating the queries (M5/r2g); ontology extraction itself (M2).

## 3. Interfaces (inputs / outputs)
- **Produces to M2:** a normalized **metadata bundle** (tables, columns, types, keys, sample rows, catalog/semantic-layer defs where available).
- **Produces to M5:** a **source handle** + an `execute(query) -> rows` surface; declares the source's query dialect.
- **Contract:** a small connector interface (`sample_metadata()`, `open()`, `execute()`, `close()`), one implementation per source.

## 4. Functional requirements
- **FR-1 (P1):** Postgres connector — metadata sampling (`information_schema`, keys, sample rows) + live parameterized query execution.
- **FR-2 (P1):** Arango unstructured-graph connector — expose the already-ingested graph (from `customer-context`) for AQL execution as a first-class federation source.
- **FR-3 (P1):** **Metadata-sampling** mode that returns schema without pulling bulk data (requires the [[contextual-data-fabric/docs/architecture/_repo-enhancements/schema-analyzers-metadata-sampling|schema-analyzers enhancement]]).
- **FR-4 (P2 — pulled forward, due 2026-07-24):** Snowflake connector — metadata + pushdown query. ~~Gated on the free-tier check~~ **Gate resolved (2026-07-21):** Snowflake's 30-day trial ($400 credits, no credit card, auto-suspends) is sufficient; see PRD §7.7 and the P1 close-out plan Sprint 2. Pushdown rides the second Ontop endpoint over `snowflake-jdbc`; metadata via r2g's Phase-6 connector (RSA fallback).
- **FR-5 (P3):** Databricks connector.
- **FR-6 (P1 floor / P2 hardened):** P1 security floor (PRD §10.7 / CC-7): every source connection uses a **read-only DB role**; credentials come from environment/secret store, never code or mapping artifacts; no raw-credential logging. P2: full credential management + per-source read-only enforcement.
- **FR-7 (P1):** **Logical source registry + SecretResolver seam.** Connectors are addressed by logical source name; M1 resolves name → credential at `open()` time. P1 backend: `.env`; P2 backend: a secret store (Vault / cloud manager) behind the same seam. Reuse r2g Phase 8's credential pattern (encrypted registry, `$ENV_VAR` resolution at use time, token redaction on read, DSN-scrubbed errors). Nothing outside M1 ever holds a raw credential; all read-back surfaces (incl. MCP tools) redact.
- **FR-8 (P2):** **Per-source auth hardening:** Snowflake **key-pair auth** (not passwords), Databricks **service principal + OAuth M2M**; rotation via the secret store with no code change. **No per-user passthrough** — deferred to M8 (PRD §10.7 identity model).

## 5. Non-functional requirements
No bulk data movement (sampling + pushdown only); read-only by default; connection reuse for latency; source errors surfaced (never silently swallowed).

## 6. Dependencies
- **Repos:** `schema-analyzers` (relational + arango) — see [[contextual-data-fabric/docs/architecture/_repo-enhancements/schema-analyzers-metadata-sampling|enhancement]]; `r2g` (schema→ontology consumes the metadata bundle); `customer-context` (the Arango unstructured graph).

## 7. Phase mapping
- **P1:** Postgres + Arango-unstructured connectors, metadata sampling.
- **P2:** Snowflake; credential/read-only hardening.
- **P3:** Databricks.

## 8. Acceptance criteria / demo (P1)
- The Postgres connector returns a metadata bundle that M2 turns into a source ontology, **and** executes a parameterized pushdown query for M5 — with no bulk table copy into Arango. The Arango connector serves AQL against the unstructured graph.

## 9. Open questions
- Standard metadata-bundle schema across sources (so M2 is source-agnostic).
- Do we need a "metadata sampling connector vs query connector" split, or one connector with two modes? (Roadmap transcript raised the sampling-connector idea.)

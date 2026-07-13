---
title: "Repo Enhancement — r2g — Federated Query Support"
repo: r2g
type:
  - internal
  - repo-enhancement-spec
status: draft
version: 0.1
owner: Arthur Keen
serves_modules: ["04-mapping-layer", "05-federated-query-engine"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Repo Enhancement — r2g must support Federated Query

> **Requirement (one line):** r2g must move from *batch-load* relational→graph to also **emitting runtime mappings and generating per-source queries**, so the [[contextual-data-fabric/docs/architecture/module-05-federated-query-engine/specification|Federated Query Engine (M5)]] can push queries down to the source **without materializing the data**.
>
> This is the concrete example Arthur gave ("tell r2g it needs to support federated query"). It is the highest-leverage enhancement for Phase 1.

## 1. Current state (as understood — Arthur to confirm)
r2g today takes a relational schema, produces an ontology (OSI/YAML) with **mappings to the source system**, and — in the Cambridge-Semantics style — can **hydrate/batch-load** the data into the graph. It uses the relational-schema analyzer and the Arango-schema analyzer (pip libs) and implements **OSI**. The mapping already exists; today it is expressed in the source system's language and used for a batch load.

## 2. Why the change
The customer constraint is **do not move the data** ([[ZScaler]]: "the brain has to be on this side"; no bulk materialization into Arango). The mapping r2g already produces *is effectively the query* — instead of running it once to hydrate, we need to run it **on demand, per question, pushed down to the source**, and to **break a query across multiple sources** when needed. This is the same transpile pattern as the Cypher/SPARQL translator (English → query), generalized and federated.

## 3. Required enhancements
- **RE-1 (P1):** Expose the r2g mappings as a **runtime artifact** the mapping layer (M4) and query engine (M5) can consume — concept→table, property→column, plus any value transforms — in **OSI/YAML**, decoupled from any batch-load step.
- **RE-2 (P1):** **Per-source query generation** — given a resolved concept/property + predicate (filter), generate a **pushdown query** in the source language (SQL for Postgres) rather than loading rows. Filters must push down to the source.
- **RE-3 (P1):** A **no-materialization mode** — r2g can produce mappings + queries without hydrating the graph (batch-load remains available as an option, not the default).
- **RE-4 (P2):** **Query fragmentation** — when a request spans >1 source, emit the per-source fragments + the join keys (the engine reassembles). Support the "break the query up, run it, reassemble" flow.
- **RE-5 (P2):** Emit mappings/queries via both a **deterministic** path and an **LLM** path (LLM as safety net), consistent with the composable-blueprint stance.
- **RE-6 (P2):** Snowflake dialect for RE-2 (Databricks in P3), gated on the connector work in [[contextual-data-fabric/docs/architecture/module-01-connectors/specification|M1]].

## 4. Interface contract (with M4 / M5)
- **Input:** relational schema/metadata (from the schema analyzer / M1) + the master-ontology concept being queried + predicate.
- **Output:** (a) OSI/YAML mapping artifact; (b) a **generated source query** (e.g. parameterized SQL) with declared bind params + the source objects it reads.
- **Guarantee:** the generated query is inspectable and returned alongside its results so M7 can cite the **actual SQL** and source object.

## 5. Phase mapping
- **P1:** RE-1, RE-2, RE-3 for **Postgres** — enough for the 1-week federated-query demo.
- **P2:** RE-4, RE-5; RE-6 Snowflake.
- **P3:** RE-6 Databricks; join optimization.

## 6. Acceptance criteria (P1)
- Given the Phase-1 Postgres schema, r2g emits an **OSI mapping** and, for a seed-question predicate, generates a **parameterized SQL pushdown query** that returns the right rows **without** hydrating them into Arango — and returns the SQL text + source objects for citation.

## 7. Open questions / for Arthur
- Does r2g already emit mappings independently of the batch load, or is that the refactor?
- Is the mapping expressed generally (e.g. spreadsheet-formula style, then compiled to SQL) or directly in SQL today? (Roadmap transcript suggested moving toward a general-purpose expression compiled per dialect.)
- Confirm the `arango-solutions/r2g` vs personal `arangodb` copy to build against (the roadmap noted solutions may lag).

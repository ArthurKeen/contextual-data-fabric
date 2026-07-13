# Contextual Data Fabric — Architecture & Module Index

> **This is the super-module.** It maps the whole system into modules, records how they depend on each other and on the existing Arango repos, and points to each module's own specification. Per our build process, **each module PRD/spec must reconcile against this index** (identify sub-modules → PR per sub-module → reconcile with the super-module → iterate). Arthur is the build gatekeeper.
>
> **Parents:** [[contextual-data-fabric-prd|PRD]] (near-term contract) · [[contextual-data-fabric-north-star|North Star]] (end-state vision).
> **Status:** Draft v0.1. Module set proposed for review — confirm before the full set is written out.

---

## Repo layout

```
contextual-data-fabric/
  docs/
    architecture/
      README.md                          ← this file (super-module / index)
      deployment-p1.md                   ← Phase-1 demo topology: what runs where (v0.2.1)
      _TEMPLATE-module-spec.md           ← copy this to author a module spec
      module-01-connectors/
        specification.md
      module-02-ontology-extraction/
        specification.md
      module-03-ontology-alignment/
        specification.md
      module-04-mapping-layer/
        specification.md
      module-05-federated-query-engine/
        specification.md                 ← written (exemplar)
      module-06-entity-resolution/
        specification.md
      module-07-grounding-provenance/
        specification.md
      module-08-governance-obac/
        specification.md
      module-09-demo-harness/
        specification.md
      module-10-evaluation/
        specification.md                 ← added v0.2 (PRD §10.1)
      _repo-enhancements/                ← requirement specs for EXISTING repos
        r2g-federated-query.md           ← written (exemplar)
        ontology-extractor-structured.md
        aer-semantic-federated.md
        schema-analyzers-metadata-sampling.md
        customer-context-expose-modules.md
```

*(These planning docs are staged in the vault and mirror the intended repo path so they lift straight into `contextual-data-fabric/docs/architecture/` once Arthur creates the repo.)*

---

## The module set

Two headline building blocks from the [[contextual-data-fabric-prd|PRD]] — the **Onto Extract layer** and the **Federated Query layer** — decomposed into buildable modules. Each is intended to be independently ownable and, where it makes sense, independently publishable (LLM *or* deterministic implementations both valid).

| # | Module | Responsibility | Building block |
|---|--------|----------------|----------------|
| **M1** | **Connectors** | Source adapters + **metadata-sampling** connectors (Postgres, Snowflake, Databricks, unstructured-in-Arango). Provide schema/metadata to extraction and live query access to the query engine. | Both |
| **M2** | **Ontology Extraction** | Structured (schemas/catalogs) + unstructured (docs) → **per-source ontologies**. Wraps **r2g** + the **ontology extractor**. | Onto Extract |
| **M3** | **Ontology Alignment** | Per-source ontologies → **master ontology**: diff/deltas → accept/reject → iterative refinement; belief management, time-travel, change control, curation (agent or human). | Onto Extract |
| **M4** | **Mapping Layer** | **Functional mappings** — ontology concept→table, property→attribute, value transforms — as **OSI/YAML**. The mapping *is* the query. | Both |
| **M5** | **Federated Query Engine** | English → resolve concepts → **decompose** → per-source query gen (SQL pushdown / AQL / agent) → execute → **reassemble**. Loosely-coupled + assembled; LLM + deterministic. | Query |
| **M6** | **Entity Resolution / Canonical Hub** | Cross-source ER → canonical entities in Arango. Wraps **AER**. | Both |
| **M7** | **Grounding & Provenance** | Validated answer envelope + **cited retrieval path across the federation boundary** (actual SQL + AQL + source object); refuse if uncited. | Query |
| **M8** | **Governance / OBAC** | Ontology-based access control + business rules (Palantir/IAM-via-ontology) via declarative mappings + SHACL. **Future.** | Both |
| **M9** | **Demo Harness** | Thin agent UI to run seed questions end-to-end (reuse the customer-360 Vercel pattern). Not sold. | — |
| **M10** | **Evaluation & Golden Set** | Golden seed questions with expected answers/sources/citations + runner + regression gate — makes "trust is structural" testable (PRD §10.1). Not sold. | — |

---

## Module → repo dependencies

Each module builds on one or more existing repos (see `_repo-enhancements/` for what each repo must add). Full repo details in [[contextual-data-fabric-prd]] §8.

| Module | Builds on repo(s) | Repo enhancement required |
|--------|-------------------|---------------------------|
| M1 Connectors | schema-analyzers, r2g, customer-context | `schema-analyzers-metadata-sampling` |
| M2 Ontology Extraction | r2g, ontology-extractor (AOE) | `ontology-extractor-structured` |
| M3 Ontology Alignment | ontology-extractor (AOE) | `ontology-extractor-structured` (alignment/belief APIs) |
| M4 Mapping Layer | r2g (OSI export) | `r2g-federated-query` |
| M5 Federated Query Engine | r2g, new code | `r2g-federated-query` |
| M6 Entity Resolution | arango-entity-resolution (AER) | `aer-semantic-federated` |
| M7 Grounding & Provenance | customer-context | `customer-context-expose-modules` |
| M8 Governance / OBAC | ontology-extractor (SHACL), mapping layer | (future) |
| M9 Demo Harness | customer-context | `customer-context-expose-modules` |
| M10 Evaluation | customer-context (corpus/questions), ontology-extractor (judge patterns) | — |

---

## Phase mapping (which slice of each module lands when)

Ladders to the [[contextual-data-fabric-prd|PRD §6]] phases.

| Module | Phase 1 (≈1 wk) | Phase 2 | Phase 3 |
|--------|-----------------|---------|---------|
| M1 Connectors | Postgres + unstructured-in-Arango | **Snowflake** | Databricks |
| M2 Extraction | Postgres schema + unstructured | multi-structured | — |
| M3 Alignment | minimal (small master, some hand-construction) | full diff/refinement | belief mgmt, time-travel, change control |
| M4 Mapping | Postgres + AQL mappings | Snowflake mappings | value-transform library |
| M5 Query Engine | loosely-coupled, one DB, LLM decompose | **assembled** pattern; deterministic hardening | multi-source planner |
| M6 ER | reuse AER (deterministic) | semantic matching | federation-aware ER |
| M7 Grounding | cited path across 2 sources | cost/latency instrumentation | — |
| M8 Governance | — | design | **OBAC/IAM-via-ontology** |
| M9 Demo | 1–3 seed questions | portfolio-scale | — |
| M10 Evaluation | golden set + runner + refusal case | decomposition scoring, LLM-judge, CI gate | portfolio-scale sets; cost budgets |

---

## Building-block version pins (CC-9)

The single source of truth for which version of each block the fabric builds against (PRD §10.9: Arthur bumps; a bump re-runs the M10 golden set; red = no merge). Pinned as of 2026-07-13:

| Block | Pin | Form |
|-------|-----|------|
| `relational-schema-analyzer` (RSA) | **v0.4.0** | PyPI |
| `arangodb-schema-analyzer` | **v0.10.0** | pip (repo `arango-schema-analyzer`) |
| `arango-entity-resolution` (AER) | **v3.5.1** | PyPI |
| `arango-ontoextract` (AOE) | **v1.2.0** (`1099b7f`) | git tag/SHA (not on PyPI) |
| `customer-context` | **`23b8ed8`** | git SHA (unversioned) |
| `arangodb-mcp-server` (`arango-solutions-mcp-server`) | **v2.0.0** | poetry/pyproject (dev/ops tooling + fabric MCP host pattern — PRD §10.2) |
| r2g Phase-12 module | — (not yet built) | will pin per P12.8 |

---

## Repo enhancement specs

Requirement specs telling each **existing** repo what it must add to serve this project (author with the same template discipline):

- **`r2g-federated-query`** — r2g must support **federated query**: emit runtime mappings + per-source query generation (not just batch load). *(Written — exemplar.)*
- **`ontology-extractor-structured`** — confirm/complete **structured→ontology**; expose **alignment** + **belief-management/time-travel** APIs.
- **`aer-semantic-federated`** — **semantic** (non-deterministic) matching; **federation-aware** ER; canonical-hub API.
- **`schema-analyzers-metadata-sampling`** — metadata-sampling API for connectors + the mapping layer.
- **`customer-context-expose-modules`** — expose the unstructured graph + the grounding/citation envelope as consumable modules; ontology-driven AQL generation.

---

## Open decisions (structure)

1. **Module granularity** — is this 9-module split the right cut, or collapse (e.g. merge M2+M3 into "Ontology" and M4 into M5)? → confirm before writing the full set.
2. **Repo layout** — one repo with module folders under `docs/architecture/` (shown here) vs a repo per building block. Arthur's call.
3. **Naming** — folder/spec convention: `module-NN-name/specification.md`. Confirm or adopt the convention from Arthur's example large-project structure.

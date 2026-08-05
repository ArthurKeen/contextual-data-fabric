# Contextual Data Fabric

ArangoDB as an **ontology-based metadata / agent-brain hub**: auto-derive a use-case-driven ontology across structured + unstructured sources, and answer English questions by **federating queries across systems** — grounded and cited — **without moving the data**.

Part of **Project Vantage**. Built for and pressure-tested against the Zscaler customer-context engagement.

> **Status:** a working **P1 system** alongside the planning docs (draft v0.3, team review). Beyond the PRD, North Star, use cases, and per-module / per-repo architecture specs, the repo now ships a **working federated-query engine** (`cdf.service` · `POST /federate`), **four source adapters** (Ontop/Postgres, native Snowflake, native ClickHouse, arango-sparql-py/ArangoDB), a **live browser demo** (`make demo` — three live sources joined and cited), a **golden gate** (`make gate`), and CI. Later modules still land per the phased plan.
> **v0.2** reconciled all specs against the actual repos — the structured→ontology gate is resolved (exists); PRD §10 added cross-cutting requirements (evaluation, agent interface/MCP, consistency, partial failure, caching, security/credentials, deployment, pinning, packaging, resource guardrails).
> **v0.3** absorbs the deep-analysis passes: **ADR-0001** decides the conceptual-query IR (typed graph-pattern → SPARQL; CSI v1 as the mapping hub; Ontop buy-vs-build open as PRD §9.10) — M5 is now mostly **integration of owned components** (`arango-sparql-py`, `arango-cypher-py`, RSA/`arangodb-schema-analyzer`); AOE PRD §6.17–§6.19 definitizes alignment / A-box / competency questions; use cases are formalized from PJ's 12 locked questions; `customer-context` is cloned + verified.

## Start here

- **[PRD](docs/contextual-data-fabric-prd.md)** — the near-term contract: vision → phased plan → **detailed Phase 1** (1-week federated-query goal) → referenced repos → open decisions → cross-cutting requirements (§10).
- **[North Star](docs/contextual-data-fabric-north-star.md)** — the end-state vision every phase ladders toward. The horizon to check scope against.
- **[Use Cases & Competency Questions](docs/use-cases.md)** — personas, interaction model, and the CQ table derived from the 12 locked questions (Q2 proposed for the P1 demo).
- **[Architecture Index](docs/architecture/README.md)** — the "super-module": module map, module→repo dependencies, phase mapping, and the **building-block version pins**.
- **[ADR-0001 — Conceptual-query language](docs/architecture/module-05-federated-query-engine/adr/ADR-0001-conceptual-query-language.md)** + **[M5 implementation plan](docs/architecture/module-05-federated-query-engine/implementation-plan.md)** — the decided query architecture and the sequenced work packages (incl. the honest 1-week P1 slice).
- **[ADR-0004 — Identity planes and policy enforcement](docs/architecture/module-05-federated-query-engine/adr/ADR-0004-identity-planes-and-policy-enforcement.md)** — the P3 asker/query identity, request-context, delegation, and future ReBAC enforcement decision.
- **[Phase-1 deployment topology](docs/architecture/deployment-p1.md)** — what physically runs where for the demo (build-time vs demo-time split).
- **[Running the demo](deploy/README.md)** — `make install && make demo`, a topology diagram of the three live sources (Postgres, Snowflake, ArangoDB; ClickHouse is a fourth catalog source) and what each contains, the example questions, and troubleshooting.

## Semantic MCP agent surface

Install the optional MCP v2 dependency and run the local stdio server:

```bash
python -m pip install -e ".[mcp]"
cdf-mcp
```

Configure the process with the same `CDF_*`, source, and NL environment variables
as the HTTP service. The tools are intentionally semantic: `federate(question,
allow_partial=False)` calls the same `FederationService.federate_question` path as
`POST /federate`; `list_sources`, `list_concepts`, and `nl_preview` expose safe
catalog/planning metadata. There are no raw SQL, AQL, or credential tools.

For example, an MCP host can launch:

```json
{
  "command": "/absolute/path/to/contextual-data-fabric/.venv/bin/cdf-mcp",
  "env": {
    "CDF_CSI_DIR": "/absolute/path/to/contextual-data-fabric/deploy/csi",
    "CDF_R2RML_DIR": "/absolute/path/to/contextual-data-fabric/deploy/r2rml"
  }
}
```

`create_mcp_server(service_factory=...)` is the injection seam for tests and
alternative wiring. Stdio is secured by the launching process, not bearer
headers. For a deployed Streamable HTTP server, the current MCP SDK provides the
supported OAuth 2.1 resource-server hook: pass its `TokenVerifier` and
`AuthSettings` together as `token_verifier=` and `auth=`, then call
`server.run(transport="streamable-http", ...)`. Do not expose unauthenticated
HTTP or implement ad-hoc static-token parsing; token issuance remains with the
deployment's authorization server.

MCP v2 authenticated HTTP tools map the SDK's official `get_access_token()`
context into the same immutable, bearer-free CDF `RequestContext` used by HTTP.
Set `auth_required=True` to refuse tools without an authenticated subject;
stdio/tests may inject a context factory. Catalog introspection validates the
context but is not policy-filtered until the next M8 increment.

Runtime entity resolution is optional and catalog-bound. A deployment that
enables a source's `runtimeResolution.mode: canonical_hub` must inject a
CDF-compatible guarded resolver directly, or set
`CDF_ENTITY_RESOLVER_FACTORY=package.module:function`; the factory receives the
environment mapping and returns the resolver. Call, batch, and deadline caps use
`CDF_MAX_RESOLUTION_CALLS`, `CDF_RESOLUTION_BATCH_SIZE`, and
`CDF_RESOLUTION_DEADLINE_MS`. CDF does not install or claim a released AER API;
the checked-in demo manifest keeps all sources at `mode: none`.

## How it works

Two flows, sharing one artifact: the **aligned master ontology + its functional mappings**. The build-time flow (second diagram) produces that artifact from the sources' schemas; the query-time flow (first diagram) uses it to answer questions — without moving the data.

### Query time — from English to a cited, federated answer

A natural-language question is lifted into a **conceptual query** — a typed graph-pattern IR over the master ontology, serializing to SPARQL ([ADR-0001](docs/architecture/module-05-federated-query-engine/adr/ADR-0001-conceptual-query-language.md)). Because every concept/property in the ontology carries a mapping to the source(s) that realize it, **decomposition is graph partitioning**: the planner (M5) splits the query graph by source. Each partition is then translated into the *native language of its source* — **SQL** pushed down to relational systems (via R2RML/Ontop, or r2g's generator), **AQL** against the unstructured graph already in ArangoDB (via the owned `arango-sparql-py` transpiler), or **rendered back into natural language** for sources that expose an agentic interface (a Snowflake/Databricks cortex) rather than a query endpoint. Results come back and are **joined on canonical entity keys** (M6/AER — the guarantee that the Postgres account row and the Slack sentiment are about the *same* account), then wrapped by M7 into a **grounded envelope**: every claim cited with the actual SQL/AQL/agent-prompt that produced it, source objects, and an as-of timestamp — or a clean **refusal** if a claim can't be cited.

Source **credentials never travel with any of this**: mappings, citations, and the hub reference sources by logical name only; the connector layer (M1) resolves names to credentials at connection time from a secret store. Existing/demo sources use one least-privilege read-only service identity. A P3 delegated-mode protocol now fails closed unless an external broker and context-aware adapter are supplied; this repo does not provision the STS or source policy.
Production Docker/Kubernetes deployments use the mounted-file
`SecretResolver`; generation changes atomically rotate executors after in-flight
calls finish. See the [deployment secret runbook](deploy/README.md#production-connector-secrets-and-rotation).

```mermaid
flowchart TB
    U(["User / Agent<br/>(natural-language question)"])
    U --> NLE["NL → conceptual query<br/>(LLM decomposer — arango-cypher-py NL engine)"]

    subgraph HUB["ArangoDB hub — the brain (no bulk source data)"]
        ONT[("Master ontology +<br/>functional mappings (CSI v1)")]
        CAN[("Canonical entities<br/>(AER hub, M6)")]
    end

    ONT -.->|"concepts + mappings"| NLE
    NLE -->|"typed graph-pattern IR (SPARQL)"| PLAN["Partition planner (M5):<br/>split the query graph by the<br/>source each concept maps to"]
    ONT -.-> PLAN

    PLAN -->|"relational partition"| TSQL["translate → SQL<br/>(R2RML → Ontop, or r2g P12.2)"]
    PLAN -->|"graph partition"| TAQL["translate → AQL<br/>(arango-sparql-py)"]
    PLAN -->|"agentic partition"| TNLQ["render → natural language<br/>(for the source-side agent)"]

    TSQL -->|"pushdown, live"| PG[("PostgreSQL / SQL Server / …")]
    TAQL --> ADB[("ArangoDB unstructured graph<br/>(ingested docs, Slack, email)")]
    TNLQ --> CTX["Snowflake / Databricks<br/>agentic cortex"]

    PG -->|"rows + SQL text + as-of"| ASM
    ADB -->|"docs/spans + AQL text + as-of"| ASM
    CTX -->|"answer + prompt text + as-of"| ASM["Reassembly (M5):<br/>join legs on canonical entity keys"]
    CAN -.->|"resolve(entity) → canonical_id"| ASM

    ASM --> ENV["Grounded envelope (M7):<br/>answer + per-claim citations +<br/>retrieval path (SQL / AQL / NL) + as-of"]
    ENV -->|"cited — or refused if uncitable"| U
```

> **Shipped vs. north-star (the `agentic partition` lane).** Today's Snowflake leg takes **SQL directly** — a native `SnowflakeExecutor` (SPARQL→SQL, compiled from the same R2RML), *not* the render-to-NL cortex path — per [ADR-0002](docs/architecture/module-05-federated-query-engine/adr/ADR-0002-snowflake-cortex-agentic-legs.md). The `render → natural language` / `Snowflake / Databricks agentic cortex` lane above is the **general design** for sources that expose *only* an agent (Cortex Analyst, Databricks Genie); it's supported in principle and built on customer demand, and never sits on the deterministic gate path.

### Build time — derive the source ontologies, align them into one

Each source's **schema** is analyzed into a **source ontology**: relational schemas via `relational-schema-analyzer` (tables, keys, FKs → concepts/properties), existing ArangoDB graphs via `arangodb-schema-analyzer`, and unstructured corpora via AOE's LLM extraction pipeline — all scoped by the **competency questions** in [use-cases.md](docs/use-cases.md) (extract what the questions need, never boil the ocean). The per-source ontologies are then **aligned** (M3, AOE §6.17): embedding retrieval proposes cross-source correspondences, multi-signal scoring auto-resolves the clear cases, an LLM adjudicates only the borderline band, and a human confirms the last ~2%. The result is the **master ontology** — `customer account` ≡ `client account` ≡ `account`, with equivalence axioms materialized — plus the **functional mappings** (CSI v1, exported as R2RML for SQL and MappingBundle for AQL) that make the query-time partitioning and translation deterministic. The ontology is temporal-versioned: source changes cascade through belief revision rather than rebuilding.

Three practicalities that matter at enterprise scale:

- **Purpose-scoped table selection.** Real warehouses have thousands of tables; introspecting everything is wrong. An integration declares its **purpose** (an ORSD-style statement + competency questions — e.g. "a Customer 360 view"), and the extractor **ranks tables by relevance before introspecting**: purpose-term similarity against table/column names and their catalog comments, foreign-key-neighborhood expansion from seed tables, the warehouse's own **query/access history** (the tables an org actually queries are the relevant ones), and governance tags as include/exclude policy. A curator confirms the ranked set — the "automation proposes, human confirms ~2%" pattern applied to table selection (M2 FR-5a; schema-analyzers RE-6).
- **Keys are read where declared, inferred where not.** Cloud warehouses (Snowflake among them) accept primary/foreign-key *declarations* without enforcing them — and many schemas declare nothing. Declared keys are read from the catalog (they're documentation-grade metadata even when unenforced); undeclared keys are **inferred** — name-convention heuristics propose candidates, bounded **value-overlap sampling** confirms them statistically — and inferred keys carry confidence into the same human-confirm step (schema-analyzers RE-7 tracks the per-warehouse sampler coverage).
- **The source's catalog is an input, never re-described.** Whatever the source already knows — `INFORMATION_SCHEMA`, object comments, governance tags, lineage/usage views, or an external enterprise catalog (OpenMetadata-class, via r2g's catalog providers) — feeds extraction directly; customers are never asked to restate it.

```mermaid
flowchart TB
    subgraph SRC["Data sources (systems of record — data stays here)"]
        PG[("PostgreSQL")]
        SNOW[("Snowflake / Databricks")]
        AG[("Existing ArangoDB graphs")]
        DOCS["Docs / Slack / email / transcripts"]
    end

    CQ["Use cases as competency questions<br/>(docs/use-cases.md — scope the extraction)"]

    PG -->|"schema, keys, samples"| RSA["relational-schema-analyzer"]
    SNOW -->|"catalog / schema"| RSA
    AG -->|"collections, edges"| ASA["arangodb-schema-analyzer"]
    DOCS -->|"LLM extraction (AOE)"| AOEX["AOE extraction pipeline"]

    CQ -.->|"priority concepts"| RSA
    CQ -.-> ASA
    CQ -.-> AOEX

    RSA -->|"conceptual schema bundle"| O1["Source ontology<br/>(relational)"]
    ASA -->|"conceptual schema bundle"| O2["Source ontology<br/>(graph)"]
    AOEX -->|"OWL/SHACL"| O3["Source ontology<br/>(unstructured)"]

    O1 --> AL
    O2 --> AL
    O3 --> AL["Alignment (M3 / AOE §6.17):<br/>embedding retrieval → multi-signal scoring →<br/>selective LLM adjudication → human confirms ~2%"]

    AL --> MO[("Master ontology<br/>(equivalence axioms materialized,<br/>temporal-versioned)")]
    MO --> MAP["Functional mappings (M4):<br/>CSI v1 → R2RML (SQL side)<br/>→ MappingBundle (AQL side)"]

    MO -.->|"consulted by"| QT["Query-time flow (above)"]
    MAP -.->|"drives partitioning + translation"| QT
```

## Modules

| # | Module | Spec |
|---|--------|------|
| M1 | Connectors | [spec](docs/architecture/module-01-connectors/specification.md) |
| M2 | Ontology Extraction | [spec](docs/architecture/module-02-ontology-extraction/specification.md) |
| M3 | Ontology Alignment | [spec](docs/architecture/module-03-ontology-alignment/specification.md) |
| M4 | Mapping Layer | [spec](docs/architecture/module-04-mapping-layer/specification.md) |
| M5 | Federated Query Engine | [spec](docs/architecture/module-05-federated-query-engine/specification.md) · [ADR-0001](docs/architecture/module-05-federated-query-engine/adr/ADR-0001-conceptual-query-language.md) · [plan](docs/architecture/module-05-federated-query-engine/implementation-plan.md) |
| M6 | Entity Resolution / Canonical Hub | [spec](docs/architecture/module-06-entity-resolution/specification.md) |
| M7 | Grounding & Provenance | [spec](docs/architecture/module-07-grounding-provenance/specification.md) |
| M8 | Governance / OBAC (future) | [spec](docs/architecture/module-08-governance-obac/specification.md) |
| M9 | Demo Harness | [spec](docs/architecture/module-09-demo-harness/specification.md) |
| M10 | Evaluation & Golden Set | [spec](docs/architecture/module-10-evaluation/specification.md) |

## Enhancement specs for existing repos

Requirement specs telling each existing Arango repo what it must add to serve the fabric:

- **r2g → federated query** — [spec](docs/architecture/_repo-enhancements/r2g-federated-query.md)
- **ontology-extractor → structured input + alignment/belief APIs** — [spec](docs/architecture/_repo-enhancements/ontology-extractor-structured.md)
- **AER → semantic + federation-aware ER** — [spec](docs/architecture/_repo-enhancements/aer-semantic-federated.md)
- **schema-analyzers → metadata sampling** — [spec](docs/architecture/_repo-enhancements/schema-analyzers-metadata-sampling.md)
- **customer-context → expose graph, grounding envelope & ontology-driven AQL** — [spec](docs/architecture/_repo-enhancements/customer-context-expose-modules.md)

## Phase 1 (≈1 week)

Demonstrate a **federated query across one relational database (live, not mirrored) + unstructured documents already ingested in Arango**, unified by an auto-derived, use-case-scoped ontology, returned grounded and cited with a retrieval path spanning both sources. Proposed demo question: **Q2** (renewal-risk-and-WHY, joining live Postgres CRM to Arango documents) ([use cases](docs/use-cases.md)). See the [PRD §7](docs/contextual-data-fabric-prd.md) for the work breakdown and the [M5 implementation plan](docs/architecture/module-05-federated-query-engine/implementation-plan.md) for the P1 walking skeleton.

## Authoring new specs

Copy [`docs/architecture/_TEMPLATE-module-spec.md`](docs/architecture/_TEMPLATE-module-spec.md), fill it in, and reconcile it against the [Architecture Index](docs/architecture/README.md). PR per sub-module.

---

*Some docs use Obsidian `[[wikilink]]` syntax (authored in a vault). They render as plain text on GitHub; the links above are the GitHub-navigable index.*

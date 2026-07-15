# Phase-1 Demo Deployment Topology

> **What this answers:** what physically runs where for the Phase-1 demo — the "demo environment" named in M9's acceptance criteria. Added v0.2.1 (PRD omission fix). One page on purpose; revisit for Phase 2 when Snowflake and the assembled pattern arrive.

---

## The key split: build-time vs demo-time

Most of the fabric's machinery runs **before** the demo, producing artifacts. The live demo runs **four processes**. Keeping AOE, RSA, and r2g off the live path is deliberate: fewer moving parts in front of the customer (M9's NFR — "a flaky demo is the failure mode"), and it makes the "no black box" story cleaner — everything live is inspectable AQL/SQL.

### Build-time (during the P1 week, not during the demo)

| Tool | Runs as | Produces |
|------|---------|----------|
| **RSA** (`relational-schema-analyzer`) | pip CLI/library, local | Postgres metadata bundle (`{conceptualSchema, physicalMapping, metadata}`) |
| **AOE** (`arango-ontoextract`) | its own 3-service stack (`make infra` + backend + frontend), local, **only while deriving/curating** | Source ontologies (OWL/SHACL) + the human-confirm curation step (B2); exports TTL/JSON-LD |
| **r2g** (Phase 12a) | pip CLI/library, local | Versioned mapping artifact (P12.1) consumed at runtime by M5 |
| **customer-context `ingestion/`** | Python pipeline, local, one-shot | The unstructured graph in ArangoDB (chunks, embeddings, entities, canonical hub via AER) |
| Hand-built master ontology (B2) | authored in AOE workspace or YAML | The master + mappings, loaded into ArangoDB as collections |
| **`arango-solutions-mcp-server`** | MCP server (stdio/HTTP), local, during development | Agent (Claude Code) access to the hub while building — inspect collections, validate AQL, run hybrid-search experiments. **Not on the demo-time path** (raw AQL bypasses the ontology — PRD §10.2); it is also the host pattern for the fabric's own semantic MCP tools in P2. |

### Demo-time (live during the demo)

```
┌─────────────────────────── demo host (one laptop or one VM) ───────────────────────────┐
│                                                                                        │
│  docker-compose:                                                                       │
│  ┌──────────────────┐         ┌──────────────────────────────┐                        │
│  │ PostgreSQL        │         │ ArangoDB (single node)        │                        │
│  │ seeded synthetic  │         │ • unstructured graph          │                        │
│  │ schema (r2g       │         │ • canonical_entities hub      │                        │
│  │ Chinook/Pagila or │         │ • master ontology + mappings  │                        │
│  │ custom, read-only │         │ (NO mirrored Postgres data)   │                        │
│  │ role)             │         └──────────▲───────────────────┘                        │
│  └────────▲─────────┘                    │ AQL                                         │
│           │ SQL pushdown                  │                                             │
│  ┌────────┴──────────────────────────────┴───────────┐    ┌─────────────────────────┐ │
│  │ M5 Federated Query Engine                          │◄───│ Demo UI (M9)            │ │
│  │ Python service (FastAPI), embeds:                   │    │ Next.js — customer-     │ │
│  │ • r2g P12 query gen (SQL)  • ontology/mapping reads │    │ context web/ app, run   │ │
│  │ • AQL generation           • M7 envelope + gate     │    │ locally (`next dev` or  │ │
│  │ LLM planner → external API (Anthropic/OpenAI)       │    │ `next start`)           │ │
│  └─────────────────────────────────────────────────────┘    └─────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────────────────┘
                       │
                       └── outbound only: LLM API (the sole external dependency)
```

**Four processes:** Postgres (container), ArangoDB (container), the M5 engine (FastAPI), the UI (Next.js). Plus one outbound dependency: the LLM API for the planner.

## Decisions & rationale

1. **One host.** Everything on a single laptop/VM (mirrors AOE's and customer-context's existing local-dev pattern). No Kubernetes, no cloud infra for P1. If the demo is remote, screen-share the laptop or run the same compose file on one VM.
2. **Postgres and ArangoDB via one `docker-compose.yml`** in this repo (new, P1 deliverable alongside B6) — reuse r2g's `docker/` sample-database wiring (Chinook/Pagila + `load-samples.sh`) for the Postgres seed.
3. **The UI talks only to the M5 engine; the engine talks to the sources.** Credentials live in the engine's env (`.env`, read-only Postgres role — CC-7); the browser never sees a connection string.
4. **AOE is not running during the demo.** Its outputs (ontology, curated master) are loaded into ArangoDB beforehand. If the demo script includes "watch the ontology get derived," that segment is pre-recorded or run as a separate rehearsed act in AOE's own UI — not wired into the live query path.
5. **Vercel is optional, not assumed.** customer-context's UI deploys to Vercel today, but a Vercel deployment can't reach a Postgres container on a laptop. P1 default: run the web app locally. (Phase 2, if a hosted demo is wanted: host Postgres + ArangoDB somewhere reachable — e.g. ArangoGraph + a managed Postgres — and keep Vercel.)
6. **Failure rehearsal:** M9 FR-2's pre-run mode plus the M10 refusal case get exercised on this exact topology before the customer sees it.

## What changes in Phase 2

Snowflake replaces/joins Postgres (engine gains a second live source — credentials and egress now matter); cost/latency instrumentation (Prometheus sidecar or AOE's observability pattern); possibly a hosted topology for repeatable customer access; and — if ADR-0001's Ontop recommendation is adopted (PRD §9.10) — an **Ontop container** joins the compose file as the relational SPARQL→SQL leg, driven by r2g's R2RML export (P1 uses r2g P12.2 pushdown directly, so no Java service on the P1 host) — its datasource config **templated from the secret store at container start**, never baked into the image or repo (CC-7). P2 also graduates credentials from `.env` to a secret store behind M1's SecretResolver seam.

# Running the demo

The Contextual Data Fabric demo answers one conceptual question by **federating
two live databases** — a relational one and a graph one — joining their results
on a shared business key and citing every fact, **without moving any data**.

> **What this proves:** the fabric's architecture, end to end — auto-derived
> mappings, cross-source query decomposition, a deterministic join, grounded
> citations, and honest refusals. It is an **internal / team** demo of the
> machinery. The customer-facing "green metrics, red sentiment" story (Q12)
> additionally needs the LLM extraction pass on the documents — see
> [Limitations](#limitations).

## Quick start

Prerequisites: **Docker Desktop running**, Python 3.10+, and the two owned
sibling libraries checked out under `~/code` (`arango-sparql-py`,
`arango-schema-analyzer`).

```bash
cd ~/code/contextual-data-fabric
make install        # once: create .venv, install engine + live-leg libraries
make demo           # bring up stacks, load data, run the gate, serve the UI
```

Then open **http://localhost:8099**, type a question (or click an example
chip), and hit **Ask**. `Ctrl+C` stops the web server; the databases keep
running (`make down` stops them, preserving data).

| Command | What it does |
|---------|--------------|
| `make install` | Create `.venv`, install the engine + live-leg libraries. Run once. |
| `make demo` | `up` → `seed` → `gate` → clear port 8099 → serve the browser UI. |
| `make gate` | Run the four golden contract checks against the live stacks (the mandatory pre-demo check). |
| `make up` / `make down` | Start / stop the two database stacks (data preserved on `down`). |
| `make seed` | (Re)load both databases + emit the mappings. |
| `make test` | Lint + types + unit suite (what CI runs). |

## Topology — what runs where

Everything runs on one machine. **Four processes**, plus one build-time step.
The databases are the systems of record; the fabric holds only the mappings and
the join — it never copies their data.

```mermaid
flowchart TB
    subgraph browser["Browser — localhost:8099"]
        UI["Demo UI<br/>question box + example chips"]
    end

    subgraph engine["Federated query engine (FastAPI :8600) — cdf.service"]
        E["POST /federate<br/>partition → execute → join → ground<br/>(credentials live here — CC-7)"]
    end

    subgraph pg["PostgreSQL :5433 — the RELATIONAL source"]
        PGT["6 tables (CRM · DocuSign · Snowflake)<br/>accounts, contacts, contracts,<br/>opportunities, nps_surveys, usage_metrics"]
    end
    subgraph ontop["Ontop :8090 — Virtual Knowledge Graph"]
        ONT["SPARQL → SQL over R2RML<br/>(no data copied)"]
    end
    subgraph adb["ArangoDB :8530 — the GRAPH source"]
        ADBC["documents (80) · chunks (80) · tickets (2)<br/>each stamped with account_id"]
    end

    UI -->|"NL question / SPARQL"| E
    E -->|"relational partition (SPARQL)"| ONT
    ONT -->|"generated SQL"| PGT
    E -->|"graph partition (SPARQL→AQL,<br/>seeded with account_id keys)"| ADBC
    E -->|"grounded, cited answer"| UI

    RSA["r2g → CSI + R2RML"] -.->|build time| ONT
    ANA["arango-schema-analyzer → reverse CSI"] -.->|build time| E
```

**The join:** the relational leg returns `account_id`; the engine pushes those
keys into the graph leg as a `VALUES` clause (a bind-join), so ArangoDB only
returns rows for the accounts in play. `account_id` is baked into both sides by
construction — a deterministic, document-level join, no fuzzy matching at query
time.

## What's in each database

### PostgreSQL (`crm`) — the structured / relational source
The synthetic CRM+contracts+telemetry corpus for three accounts (Northwind =
healthy expansion, Meridian = hidden risk, Helio = churn). Loaded by
`deploy/ontop/load_corpus.py` from `customer-context/data_gen/output/structured/`.

| Table | Rows | Source system | Holds |
|-------|-----:|---------------|-------|
| `accounts` | 3 | CRM | account name, segment, product tier, health score, ARR, `account_id` |
| `contacts` | 8 | CRM | people, titles, champion role, engagement status |
| `opportunities` | 16 | CRM | pipeline stage, amount, renewal date |
| `nps_surveys` | 35 | CRM | NPS **scores** (the numbers; the verbatims live on the graph side) |
| `contracts` | 15 | DocuSign | value, term, auto-renew, product scope, days-to-renewal |
| `usage_metrics` | 46 | Snowflake | query volume, cluster size, edition, feature adoption |

Ontop exposes these as a Virtual Knowledge Graph: it answers SPARQL by
rewriting to SQL against the live tables through the **r2g-generated R2RML**
mapping (`deploy/ontop/input/mapping.ttl`) — nothing is copied out of Postgres.

### ArangoDB (`cmf`) — the unstructured / graph source
The document corpus (Slack, email, docs, Gong transcripts) for the same three
accounts. Loaded by `deploy/arango/load_corpus.py` from
`customer-context/data_gen/output/unstructured/`.

| Collection | Count | Holds |
|------------|------:|-------|
| `documents` | 80 | one per source file: `account_id`, source (slack/email/docs/gong), `citable_url`, role, `questions_served`, `event_date` |
| `chunks` | 80 | paragraph-bounded text, each carrying `document_id` **and** the denormalized `account_id` stamp |
| `tickets` | 2 | a small typed collection linked to two corpus accounts |

The `arango-sparql-py` transpiler answers SPARQL by generating AQL against
these collections, driven by the **analyzer-generated reverse CSI**
(`deploy/csi/arango-cmf.json`).

## The example questions

Shown as clickable chips in the UI (resolved from `deploy/questions.json`):

- **"for each account, what product tier are they on?"** — a **structured-only**
  question: answered entirely from Postgres. The trust-building anchor: clean,
  fully-sourced, single-leg.
- **"for each account, what documents do we hold and where did they come from?"**
  — a **cross-graph** question: joins the 3 Postgres accounts to their 80
  ArangoDB documents on `account_id`. This is the federation story — two
  databases, one answer, every row cited with its source URL.

The answer panel shows the status badge (`grounded` / `refused` / `partial`),
the per-source partition (the actual SQL and AQL that ran, source objects,
as-of timestamps, row counts), and the joined result.

## Limitations (what this demo does *not* yet show)

- **Free-form English.** The question box resolves against a fixed registry of
  questions (the M9 "pre-run" mode). An unlisted question **refuses honestly**
  rather than guessing. Arbitrary NL → query needs the LLM front-end (WP-P1.5),
  which is deferred pending an API-key decision.
- **The Q12 centerpiece** ("every metric green, but the sentiment is red").
  The documents are loaded as citable text, but their *sentiment/entities* are
  not yet extracted — that is `customer-context`'s LLM extraction pipeline
  (WP-P1.3-full). The join contract is identical, so it drops in without engine
  changes.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No rule to make target 'demo'` | You're in the wrong directory — `cd ~/code/contextual-data-fabric` (not `customer-context`). |
| `address already in use` on :8099 | A previous UI is still running. `make demo` now clears it automatically; or run `make free-ui`. |
| A leg shows `failed` in the answer | That stack is down — `make up`, then retry. (The engine *declares* the failed leg rather than hiding it — that's the intended behavior.) |
| Ports clash with other local containers | Override, e.g. `make demo CDF_ARANGO_PORT=8531 CDF_POSTGRES_PORT=5434 CDF_ONTOP_PORT=8091`. |

See also the per-stack notes in [`ontop/README.md`](ontop/README.md) and
[`arango/README.md`](arango/README.md), and the architecture-level
[deployment topology](../docs/architecture/deployment-p1.md).

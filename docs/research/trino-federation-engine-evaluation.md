---
title: "Trino as a federation leg, a hub, or a competitor — an evaluation for the Contextual Data Fabric"
type:
  - internal
  - research
  - integration-analysis
date: 2026-09-09
status: draft — for team review
related:
  - "docs/research/federated-query-optimization.md (M12 survey; Trino CBO + dynamic filtering already cited)"
  - "docs/research/federated-aggregation-text-and-reuse.md (Trino connector SPI as the model for ADR-0005's capability registry)"
  - "docs/architecture/module-05-federated-query-engine/adr/ADR-0002-snowflake-cortex-agentic-legs.md (native SQL leg over a middle layer)"
  - "docs/architecture/module-05-federated-query-engine/adr/ADR-0005-cross-leg-aggregation-and-capability-registry.md"
  - ".claude/skills/add-source-kind/SKILL.md (Trino listed as a candidate engine kind)"
  - "docs/contextual-data-fabric-product-strategy-prd.md §2 (Starburst/Trino as a positioning comparator)"
---

# Trino: what it is, what it solves for us, and how (and whether) to interface

> **The ask (Arthur, 2026-09-09):** research Trino — what is it, what can it
> solve for us, why and how should we interface with it, what does it cost, and
> can we use it in development without paying? Trino is already cited in both
> federation research surveys, in the access-control research, in the SOTA
> scorecard, and the `add-source-kind` skill names it as a candidate engine;
> this document consolidates what was scattered and answers the interface and
> cost questions directly.
>
> **Method.** Facts come from primary sources read on 2026-09-09: trino.io
> documentation (release 483), the trinodb GitHub repositories, the Ontop
> documentation, starburst.io pricing, and aws.amazon.com Athena pricing.
> Claims seen only in third-party coverage are marked `[secondary]`. No number
> below is invented; where a source gives none, none is given.

---

## 1. Executive summary

1. **Trino is a stateless, Apache-2.0, distributed SQL query engine** (formerly
   PrestoSQL) that federates over 45+ sources through pluggable connectors and
   joins across them in its own workers. It owns no storage. It speaks ANSI SQL
   in, and pushes filters, projections, aggregates, limits and (within one
   catalog) joins down to sources.
2. **It solves the same problem we solve, one layer lower.** Trino federates
   *tables*; we federate *concepts*. It has no ontology, no entity resolution,
   no grounded citation envelope, and no agent surface. That is exactly the
   positioning line the product-strategy PRD already draws against
   Starburst/Trino, and nothing found here changes it.
3. **Three ways to interface exist. Only one is recommended now.**
   - **(A) Trino as one more SQL engine kind** via a native `TrinoExecutor`
     (the `add-source-kind` path, or Ontop's own Trino dialect). **Recommended
     when a customer already runs Trino or Starburst** — it turns 45+ engine
     families into one connector for us, for free, with the customer paying
     the compute they already pay.
   - **(B) Trino as our relational hub** (route Postgres, Snowflake and
     ClickHouse legs through Trino instead of native executors). **Not
     recommended.** It adds a JVM tier, a second cost-based optimizer we cannot
     see into, per-query coordinator latency that is visible on our
     millisecond-scale legs, a default that compacts our bind-join key lists
     into min/max ranges, and it surrenders the per-leg citation we build our
     trust argument on. This is ADR-0002's "no middle layer when the SQL door
     is open" argument, applied again.
   - **(C) Trino as a design reference**, which is already how the repo uses
     it: the connector SPI models ADR-0005's capability registry, dynamic
     filtering models our seed-overflow ladder, and the CBO statistics
     interface models M12's stats plan. Keep doing this.
4. **Cost.** Trino itself is free (Apache-2.0). Running it costs compute only.
   Hosted variants: Starburst Galaxy has a free-forever tier (3 clusters) and
   paid tiers from $0.50 per credit; Amazon Athena bills $5 per TB scanned.
   Neither removes the source's own compute bill (a Snowflake warehouse still
   runs for every Trino query against it).
5. **Development without paying: yes, trivially.** `docker run trinodb/trino`
   gives a single-node cluster on a laptop with Postgres, ClickHouse and
   Snowflake connectors included. Starburst Galaxy's free tier is a second
   zero-cost route if a hosted instance is wanted for a demo.
6. **Concrete next step, if any:** add `trino` to the `add-source-kind`
   template as a reference engine and build a `TrinoExecutor` only when the
   first customer with a Trino or Starburst estate appears (WS-C, PRD §12).
   Do not re-platform the existing three legs onto it.

---

## 2. What Trino is

| Aspect | Fact | Source |
|---|---|---|
| Kind | Distributed, MPP SQL query engine; no native storage; a compute layer over existing sources | trino.io/docs overview |
| Lineage | Fork of Facebook Presto (2019, as PrestoSQL), renamed Trino in Dec 2020 | Wikipedia; trino.io |
| License | Apache-2.0 | github.com/trinodb/trino |
| Current release | 483 (docs current); Java 25.0.1+ required to run | trino.io/docs; deployment guide |
| Scale of project | 13.2k GitHub stars, ~47k commits; last push Aug 2026 | github.com/trinodb/trino |
| Architecture | One coordinator (parse, plan, schedule) plus N workers; single-node mode allowed for testing | deployment guide |
| Connectors | 45+ catalogs: PostgreSQL, Snowflake (since release 440, Mar 2024), ClickHouse, MySQL, SQL Server, Oracle, BigQuery, Redshift, MongoDB, Elasticsearch, OpenSearch, Cassandra, Iceberg, Delta Lake, Hive, Kafka, and more. **No ArangoDB connector, no graph connector of any kind.** | connector index |
| Pushdown types | Predicate, projection, dereference, aggregation, join (same catalog only, cost-based), limit, top-N | optimizer/pushdown |
| Cross-source joins | Performed in Trino workers after each catalog's scan; join pushdown never crosses catalogs | optimizer/pushdown |
| Dynamic filtering | Runtime semi-join: build-side keys are collected and pushed as predicates to the probe-side connector, with a fallback to min/max ranges when the key set is large | admin/dynamic-filtering (already in M12 survey) |
| Client protocol | REST: `POST /v1/statement` returns paged JSON with `nextUri`; `DELETE nextUri` cancels; headers `X-Trino-User/Catalog/Schema/Session` | develop/client-protocol |
| Python client | `pip install trino` (DBAPI 2.0 + SQLAlchemy dialect); Python 3.9+; Basic, JWT, OAuth2, Kerberos, certificate auth | trinodb/trino-python-client |
| AI functions | `ai_classify`, `ai_extract`, `ai_analyze_sentiment`, etc. call an LLM (OpenAI, Anthropic, Ollama) from SQL; off by default, configured per catalog | functions/ai (added ~release 471, 2025) |
| MCP | Several community MCP servers (`txn2/mcp-trino`, `tuannvm/mcp-trino`); none from the Trino project itself | GitHub `[secondary]` |
| Commercial | Starburst (Galaxy SaaS, Enterprise self-managed); Amazon Athena is Trino under the hood; Amazon EMR ships Trino | starburst.io; aws.amazon.com |

### 2.1 Per-connector facts that matter for our three SQL sources

| Connector | Pushdown supported | Pushdown NOT supported / caveats | Relevance to us |
|---|---|---|---|
| **PostgreSQL** | Predicate (most types), aggregation (`count/sum/avg/min/max/stddev/variance/corr…`), join (cost-based, default `AUTOMATIC`), limit, top-N, dynamic filtering (default on, 20s wait) | Range predicates on strings not pushed by default; stats need `ANALYZE`; `domain-compaction-threshold` default **256** | Ontop already handles this leg with full SQL rewriting; Trino adds nothing |
| **Snowflake** | Predicate, aggregation (incl. statistical), limit, top-N, dynamic filtering | Requires JVM flags for Arrow; integers surface as `DECIMAL(38,0)`; one connector instance per Snowflake database; needs `snowflake.warehouse` so **every query spins the warehouse** (same cost as today) | Our native `SnowflakeExecutor` already compiles the BGP to Snowflake SQL directly (ADR-0002) |
| **ClickHouse** | Equality predicates on all types, aggregation (`avg/count/max/min/sum`), limit | **No `!=` or range pushdown on strings**; requires ClickHouse 25.3+; `String` maps to `VARBINARY` unless `map-string-as-varchar` is set | Our native `ClickHouseExecutor` has none of these restrictions |

The `domain-compaction-threshold` behaviour is the sharpest technical fact for
us: the JDBC connectors compact a large IN-list into a **min/max range
predicate** above the threshold (256 on Postgres). Our bind-join seeding sends
VALUES lists of up to `CDF_MAX_SEED_ROWS` (default 10,000) keys. Routed through
Trino at defaults, a 10,000-key seed would arrive at the source as
`key BETWEEN min AND max`, silently turning a selective probe into a range scan.
The threshold is configurable, but it is a per-catalog knob we would have to
own and test, and it is exactly the kind of invisible behaviour change the
fabric's cost discipline (CC-6) exists to prevent.

---

## 3. What Trino could solve for us

Ranked by how much it would actually change our roadmap.

### 3.1 One connector, forty-five engines (high value, but conditional)

The `add-source-kind` skill exists because every new engine family needs a new
native executor: DuckDB, BigQuery, MySQL, Databricks SQL, Oracle, SQL Server
are all named there or in the PRD. A single `TrinoExecutor` would let the fabric
reach any of those **through a customer's existing Trino or Starburst
deployment** without writing a dialect module for each. The R2RML from r2g
stays the same; only identifier quoting and literal rendering change, and
Trino's SQL is close to ANSI, so the module would be small.

Two caveats keep this conditional rather than urgent:

- **It requires the customer to run Trino.** Standing up Trino ourselves to
  reach, say, one BigQuery dataset is more operational surface than a native
  BigQuery executor. The value is highest when Trino is already in the estate.
- **Trino is not a graph engine.** The ArangoDB leg, which is where the
  ontology lives and where in-engine joins happen, cannot route through Trino.
  So Trino can only ever cover the relational side of a federation.

### 3.2 A federation-ready customer signal (positioning value)

An estate that already runs Trino or Starburst has already accepted "query in
place, don't copy." Those customers have felt the two gaps Trino leaves open:
no shared semantics across catalogs, and no way to hand the federation to an
agent with a grounded answer. That is the product-strategy PRD's wedge, stated
in the customer's own terms. The interface story becomes: **keep your Trino;
the fabric sits above it and gives every agent one governed conceptual model,
cited or refused.** No re-platforming required.

### 3.3 Ontop over Trino as a "buy path" (low value)

Ontop added a Trino dialect in 5.0.2. In principle one Ontop endpoint over one
Trino cluster would front every SQL source with a single R2RML mapping. Ontop's
own docs flag the problem: Trino exposes **no integrity constraints** (no
primary or foreign keys), so Ontop's SQL rewriting cannot eliminate redundant
self-joins and produces inefficient queries unless the missing constraints are
supplied by hand through lenses. We already carry that constraint knowledge in
r2g's output, but feeding it to Ontop lenses per source is real work for a path
that also inherits every issue in section 4. Not recommended.

### 3.4 Design reference (already realised)

The repo already borrows from Trino where it pays:

- ADR-0005's capability registry is modelled on the connector SPI's
  `applyFilter`/`applyAggregation` "offer and decline" pattern.
- M12's seed-overflow ladder (batched VALUES → temp-table → min/max range →
  hash) is Trino's dynamic-filtering fallback ladder, made explicit and
  budgeted.
- M12's statistics plan (per-concept counts, HLL NDV, MinHash overlap)
  mirrors Trino's per-connector statistics interface feeding a CBO.

Nothing here requires running Trino. It requires reading its docs, which the
two surveys already did.

---

## 4. Why we should NOT put Trino in the middle of the existing legs

Option (B) above deserves an explicit rejection, because "just use Trino for
all the SQL sources" is the obvious suggestion and it will be asked.

| Concern | Detail | Fabric principle it collides with |
|---|---|---|
| **A second optimizer we cannot cite** | Trino's CBO decides pushdown, join order, and predicate compaction. Our envelope cites *the SQL we compiled*; through Trino we would cite SQL Trino may have rewritten before it reached the source | Grounded citation (PRD §2.1); ADR-0002's derived-vs-attested distinction |
| **Latency floor per leg** | Every Trino query passes through coordinator parse/plan/schedule and a paged REST protocol. Practitioner reports put simple-query startup well above single-digit milliseconds `[secondary]`. Our legs are budgeted in milliseconds and a bind-join issues several per question | CC-6 cost and latency discipline; the B7 baseline |
| **Seed-list compaction** | IN-lists above `domain-compaction-threshold` (256 on Postgres) become min/max range predicates unless retuned per catalog | Plan-time admission: "no naked scans" (PRD §10.6) |
| **Weaker pushdown than native on ClickHouse** | No string inequality or range pushdown; `String` as `VARBINARY` by default | Our native executor already pushes everything E1 emits |
| **Operational surface** | A JVM (Java 25) coordinator, catalog files with source credentials, a second place to run the CC-7 read-only role and timeouts | Security floor and credential architecture (PRD §10.7, CC-7) |
| **Still no graph leg** | ArangoDB stays native regardless; the "one hub" argument never fully lands | Arango is the hub (PRD §2.1) |

Conclusion: Trino in the middle costs us determinism, latency, and citation
fidelity to save dialect modules we have already written. Reject.

---

## 5. How to interface, when we do

Recommended shape: **Trino is one more SQL engine kind**, built with the
existing `add-source-kind` protocol, gated on customer pull.

### 5.1 The `TrinoExecutor` (Phase 1 of `add-source-kind`)

- **Module:** `src/cdf/adapters/trino.py`, modelled on `clickhouse.py` and
  `snowflake.py`. Reuse `parse_r2rml`, `_collect_bgp`, `_parse_values`,
  `_literal_value`, `Mapping`, `Transport` from the ClickHouse adapter, as the
  skill prescribes.
- **Dialect specifics:** `_ident` uses double quotes (ANSI); table references
  are three-part `catalog.schema.table`, so the r2g R2RML `rr:tableName` must
  carry the catalog or the executor must be configured with a default catalog
  and schema. `_sql_literal` is ANSI single-quote escaping; booleans render as
  `true`/`false`.
- **Transport:** `trino` PyPI package (DBAPI 2.0), Apache-2.0, injected behind
  the existing `Transport` callable so parsing stays unit-testable without a
  cluster. Pass `X-Trino-Client-Tags` and a `query_max_run_time` session
  property from the `CDF_RUNTIME_WALL_TIME_MS` budget so Trino enforces the
  same deadline we do.
- **Auth:** the client supports Basic, JWT, OAuth2, Kerberos and certificate;
  wire through the existing SecretResolver seam, never in a catalog file we
  ship.
- **Capability registry entry (ADR-0005):** declare aggregation-capable for
  `count/sum/avg/min/max`, with the caveat that cross-catalog aggregation is
  Trino's own business and stays opaque to us.
- **Seed handling:** if a Trino leg is ever seeded with a bind-join, set the
  target catalog's `domain-compaction-threshold` at or above
  `CDF_MAX_SEED_ROWS`, and add a golden that proves a 1,000-key seed arrives
  as an IN-list, not a range. Trino's `system.runtime.queries` table exposes
  the executed text for that assertion.
- **from_env:** a `trino` branch keyed on `CDF_TRINO_DSN` (or the manifest's
  `kind: trino`), following the ClickHouse/Snowflake branches.

### 5.2 Ontology and mappings

Unchanged. r2g introspects the underlying source (or Trino's
`information_schema`, which is standard), emits CSI + R2RML, and single-owner
concept ownership applies as usual. A customer's Trino catalog is just another
`add-source-instance` once the executor exists.

### 5.3 Tests and gate

- Unit: compile golden SPARQL BGPs to Trino SQL, assert text.
- Integration: `docker run trinodb/trino` with a `postgresql` catalog pointing
  at the existing Ontop-backed Postgres fixture, prove the same golden answers
  land through both legs.
- Gate: keep Trino legs **off** the `make gate` default path until a customer
  needs one, same as the other optional legs.

### 5.4 What not to build

- No Ontop-over-Trino endpoint (section 3.3).
- No routing of the existing Postgres, Snowflake, or ClickHouse legs through
  Trino (section 4).
- No use of Trino AI functions: they put an LLM inside a leg, which ADR-0002
  already rejects for Cortex on the same grounds (cost, non-determinism,
  semantics in two places).

---

## 6. What it costs

### 6.1 Trino itself

| Item | Cost | Notes |
|---|---|---|
| Software license | **$0** (Apache-2.0) | Same license as Ontop |
| Python client | **$0** (Apache-2.0) | `pip install trino` |
| Compute to run it | Whatever the hosts cost | Java 25, Linux; docs recommend giving the JVM 70–85% of node RAM; production guidance assumes >32 GB nodes, but single-node laptop runs work with far less |
| Source compute | **Unchanged** | A Trino query against Snowflake still runs on a Snowflake warehouse; against ClickHouse still uses ClickHouse CPU. Trino does not make sources cheaper; it adds its own layer on top |
| Commercial support | Optional | Starburst Enterprise; pricing is quote-based, structured around nodes or capacity `[secondary]` |

### 6.2 Hosted options

| Offering | Model | Price points found | Free path |
|---|---|---|---|
| **Starburst Galaxy** (SaaS Trino) | Credits per cluster-hour, four tiers | Free: $0. Pro: from $0.50/credit. Enterprise: from $0.75/credit. Mission-Critical: from $1.00/credit. Annual commitments discounted | **Free-forever tier: up to 3 clusters**, standard execution mode, auto-suspend at 1 or 5 minutes, 4-hour max query time. Plus a 30-day trial with $500 in compute credit and Enterprise features. Snowflake and ClickHouse catalogs are GA in Galaxy |
| **Amazon Athena** (Trino-based) | Per-TB scanned or provisioned capacity | $5 per TB scanned; federated (non-S3) queries bill per TB scanned with a 10 MB minimum per query plus Lambda invocation charges; capacity reservations at $0.30 per DPU-hour | No standing free tier found on the pricing page |
| **Amazon EMR with Trino** | EMR per-instance-hour surcharge plus EC2 | Not evaluated here | No |

### 6.3 Cost of building option (A) in our repo

Rough engineering estimate, not a quote: a `TrinoExecutor` following the
ClickHouse template is on the order of the 387-line ClickHouse adapter plus
tests, because the R2RML and BGP machinery is shared. The Snowflake adapter
landed inside the July 2026 Snowflake sprint including live-proving (ADR-0002); Trino's ANSI-adjacent SQL
should be no harder. The unbounded part is per-customer catalog tuning.

---

## 7. Development without paying

Yes, three ways, all $0.

1. **Local Docker (recommended).**
   ```bash
   docker run --name trino -d -p 8080:8080 \
     --volume "$PWD/deploy/trino/catalog:/etc/trino/catalog" \
     trinodb/trino
   ```
   The image is a single-node coordinator-plus-worker with `tpch`, `tpcds`,
   `memory` and `jmx` sample catalogs. Drop a `postgresql.properties` (or
   `clickhouse.properties`, `snowflake.properties`) into the mounted catalog
   directory and the source appears as a catalog. `CATALOG_MANAGEMENT=dynamic`
   lets catalogs be created with SQL instead of files. The image's default JVM
   heap is not documented; set `-Xmx` explicitly in a mounted `jvm.config` for
   a laptop. Querying the bundled `tpch` catalog needs no external source at
   all, which makes it a good CI fixture.
2. **Starburst Galaxy free tier.** Three free-forever clusters, hosted, with
   Snowflake and ClickHouse catalogs available. Useful if a demo needs a Trino
   URL reachable from outside a laptop. Free clusters auto-suspend after 1 or 5
   minutes, so cold-start latency will show in a demo.
3. **Starburst Galaxy 30-day trial.** $500 of compute credit with Enterprise
   features, then a downgrade to free. Only worth burning when evaluating
   Enterprise-only features (fine-grained access control, autoscaling).

Nothing above requires a license key, a sales conversation, or a credit card
for the local path.

---

## 8. Decision and next steps

**Decision proposed:** Trino is a **customer-conditional engine kind and a
design reference**, not a hub. Concretely:

| # | Action | Owner | Trigger |
|---|---|---|---|
| 1 | Add a "Trino" row to the `add-source-kind` skill's engine notes: ANSI double-quote idents, three-part names, `trino` DBAPI transport, `domain-compaction-threshold` seed caveat | Arthur | Now (docs only) |
| 2 | Add Starburst/Trino talking points to `docs/customer-qa.md`: "keep your Trino, the fabric sits above it" | Arthur / PJ | WS-C, before the first federation-savvy prospect |
| 3 | Build `TrinoExecutor` via `add-source-kind`; live-prove against local Docker Trino fronting the Postgres fixture | TBD | First customer with a Trino or Starburst estate |
| 4 | Do **not** route existing legs through Trino; record this in an ADR only if the question keeps recurring | — | — |

**Open questions for the team**

- Do any current prospects run Trino, Starburst, or Athena federated queries?
  That single fact moves action 3 from "someday" to the next sprint.
- Should the positioning line in the product-strategy PRD be sharpened to
  name the two gaps (shared semantics, agent-grade grounding) rather than the
  current four-item list? This research supports that sharpening.

---

## Sources

- Trino overview and docs (release 483): https://trino.io/docs/current/overview.html
- Trino connector index: https://trino.io/docs/current/connector.html
- Trino Snowflake connector: https://trino.io/docs/current/connector/snowflake.html
- Trino ClickHouse connector: https://trino.io/docs/current/connector/clickhouse.html
- Trino PostgreSQL connector (domain-compaction-threshold default 256, join pushdown defaults): https://trino.io/docs/current/connector/postgresql.html
- Trino pushdown reference: https://trino.io/docs/current/optimizer/pushdown.html
- Trino dynamic filtering: https://trino.io/docs/current/admin/dynamic-filtering.html
- Trino deployment requirements (Java 25.0.1+, memory guidance): https://trino.io/docs/current/installation/deployment.html
- Trino Docker container guide: https://trino.io/docs/current/installation/containers.html
- Trino Docker image README: https://github.com/trinodb/trino/blob/master/core/docker/README.md
- Trino client REST protocol: https://trino.io/docs/current/develop/client-protocol.html
- Trino AI functions: https://trino.io/docs/current/functions/ai.html
- Trino release 440 (Snowflake connector added, 8 Mar 2024): https://trino.io/docs/current/release/release-440.html
- Trino GitHub (Apache-2.0, stars, commits): https://github.com/trinodb/trino
- Trino Python client: https://github.com/trinodb/trino-python-client ; https://pypi.org/project/trino/
- Trino on Wikipedia: https://en.wikipedia.org/wiki/Trino_(SQL_query_engine)
- Presto: SQL on Everything (ICDE 2019): https://trino.io/Presto_SQL_on_Everything.pdf
- Ontop Trino dialect (since 5.0.2; no integrity constraints): https://ontop-vkg.org/guide/databases/trino ; release notes: https://ontop-vkg.org/guide/releases.html
- Starburst pricing: https://www.starburst.io/pricing/ ; free-tier page: https://www.starburst.io/starburst-galaxy/plans-and-pricing-basic-tier/
- Starburst Galaxy cluster basics (auto-suspend, 4-hour limit): https://docs.starburst.io/starburst-galaxy/cluster-administration/galaxy-cluster-basics.html
- Starburst Galaxy release notes (Snowflake parallel mode, ClickHouse GA): https://docs.starburst.io/starburst-galaxy/get-started/release-notes.html
- Amazon Athena pricing: https://aws.amazon.com/athena/pricing/
- Community MCP servers for Trino `[secondary]`: https://github.com/txn2/mcp-trino ; https://github.com/tuannvm/mcp-trino
- Small-query latency practitioner reports `[secondary]`: https://www.padiso.co/blog/apache-superset-trino-performance-tuning/ ; https://github.com/trinodb/trino/issues/21671

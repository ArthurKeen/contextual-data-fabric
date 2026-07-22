# Snowflake leg (M5 — native, Option B / ADR-0002)

**Snowflake takes SQL directly**, so its relational leg is compiled **natively** by
`cdf.adapters.snowflake.SnowflakeExecutor`: it compiles an E1 single-source
sub-query + the **r2g-emitted R2RML** (concept→table/column) straight to Snowflake
SQL and runs it over `snowflake-connector-python`. Federation — **no data movement**,
no second Ontop container, no JDBC/Arrow quirk.

```
E1 sub-query (SPARQL BGP) + R2RML ─▶ SnowflakeExecutor ─▶ Snowflake SQL ─▶ Snowflake
```

Unlike the Postgres/ArangoDB legs there is **no container here** — the database is
the cloud service. All this directory holds is the one-time account setup, the
corpus loader, and the generated mapping.

## One-time setup (once per trial account)

1. Create the account (30-day trial, $400 credits, no card — PRD §7.7).
2. In a Snowsight worksheet, run [`setup.sql`](setup.sql) as `ACCOUNTADMIN`
   (replace `<YOUR_USER>`): creates warehouse `CDF_WH`, database `TELEMETRY`, a
   resource monitor (CC-11), and the read-only role `CDF_RO` (CC-7).
3. Put the connection in the gitignored root `.env` (engine env only, CC-7):

   ```
   SNOWFLAKE_ACCOUNT=oewnmae-zh45116   # org-account form; sb42555.us-east-2 also works
   SNOWFLAKE_USER=...                  # your login
   SNOWFLAKE_PASSWORD=...              # password ok for the trial; key-pair is P2
   SNOWFLAKE_WAREHOUSE=CDF_WH
   SNOWFLAKE_DATABASE=TELEMETRY
   SNOWFLAKE_SCHEMA=PUBLIC
   SNOWFLAKE_ROLE=CDF_RO
   ```

Accept: `USE ROLE CDF_RO; USE WAREHOUSE CDF_WH; SELECT 1;` works.

## Load the telemetry corpus (WP-S2)

`load_corpus.py` (sibling of the Postgres loader) reads the data-gen output
(`customer-context/data_gen/output/structured/*/snowflake/*usage_metrics*.json`) and
creates `USAGE_METRICS` with **unquoted (uppercase) physical names** — deliberate, so
CC-12's naming layer maps `USAGE_METRICS`→`UsageMetric`, `QUERY_VOLUME_M`→
`queryVolumeM`. `ACCOUNT_ID` is preserved as the cross-source join spine.

## The mapping (WP-S3)

Point r2g at the Snowflake schema, then:

```bash
r2g export-csi   --source-type snowflake --source-ref telemetry -o deploy/csi/snowflake-telemetry.json
r2g export-r2rml --source-type snowflake --source-ref telemetry -o deploy/r2rml/snowflake_telemetry.ttl
```

**Concept ownership (never cut):** also drop `usage_metrics` from the *Postgres*
mapping and regenerate its CSI/R2RML so `UsageMetric` routes **uniquely** to
Snowflake — the planner must never see one concept claimed by two sources.

## Validate the dialect

The unit tests prove the compiler with a fake transport; only a live warehouse
confirms the emitted SQL is accepted:

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest tests/test_snowflake_live.py -q   # skips without SNOWFLAKE_ACCOUNT
```

## Cost (WP-S8 — the B7 story)

The whole sprint — schema introspection, the 46-row load, and every `make gate` /
demo run — burned **~0.57 credits** on the XS warehouse (60s auto-suspend), measured
via `INFORMATION_SCHEMA.WAREHOUSE_METERING_HISTORY`. The trial grants **$400** of
credits; at this workload the leg is **effectively free**, and it's a real, defensible
number for the cost story (the compiled SQL leg bills warehouse compute only — no
per-question LLM tokens, per ADR-0002). Live check:

```sql
USE ROLE ACCOUNTADMIN; USE DATABASE TELEMETRY;
SELECT ROUND(SUM(CREDITS_USED),4) AS credits
FROM TABLE(INFORMATION_SCHEMA.WAREHOUSE_METERING_HISTORY(DATE_RANGE_START=>DATEADD('day',-7,CURRENT_DATE())));
```

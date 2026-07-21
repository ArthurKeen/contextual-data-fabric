# ClickHouse leg (M5 — native, no Ontop dialect exists)

Ontop has no ClickHouse dialect, so ClickHouse's relational leg is generated
**natively** by `cdf.adapters.clickhouse.ClickHouseExecutor`: it compiles an E1
single-source sub-query + the **r2g-emitted R2RML** (concept→table/column)
straight to ClickHouse SQL and runs it here. Federation — **no data movement**.

```
E1 sub-query (SPARQL BGP) + R2RML ─▶ ClickHouseExecutor ─▶ ClickHouse SQL ─▶ ClickHouse
```

## Run

```bash
docker compose up                                   # ClickHouse HTTP on :8123

CLICKHOUSE_DSN=clickhouse://cdf:cdf@localhost:8123/analytics \
  .venv/bin/python -m pytest tests/test_clickhouse_live.py -q
```

The live test is the dialect gate: the unit tests prove the compiler logic with a
fake transport, but only a real ClickHouse confirms the emitted SQL is accepted.

## Using your own ClickHouse schema

Point r2g at the ClickHouse source, generate the mapping, and hand the R2RML to
the executor (identical toolchain as an Ontop source — only the executor differs):

```bash
# in r2g (once the ClickHouse source connector lands, WP-CH2):
r2g export-r2rml --config mapping.yaml --schema schema.json -o clickhouse.ttl
```

Then `ClickHouseExecutor(r2rml=<clickhouse.ttl>, dsn=…)`. Concept IRIs stay under
`urn:arango-sparql:concept#`, so the ClickHouse leg shares one vocabulary with the
Ontop and Arango legs and the E1 planner routes across all three.

## Known limits (v1)

- Data-property triples + literal filters + shared-variable joins + the FR-13
  bind-join (`VALUES → IN`). R2RML *referencing object maps* (FK relationship
  traversal within ClickHouse) are not yet compiled — cross-source joins ride the
  business key, which is the common case.
- Multi-table single-source joins use comma-join + `WHERE` (ClickHouse accepts
  this); explicit `JOIN … ON` emission is a possible follow-up.

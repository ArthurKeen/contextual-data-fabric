# Ontop relational leg (M5 / WP-B1)

Stands up [Ontop](https://ontop-vkg.org/) (Apache-2.0) as a **Virtual Knowledge
Graph** over a live Postgres: it answers SPARQL by rewriting to SQL against the
database using an **R2RML** mapping — no data is copied into the fabric. This is
the concrete relational `SourceExecutor` behind the M5 federated engine.

```
E1 partition ─▶ sub-query (SPARQL) ─▶ OntopExecutor ─▶ Ontop :8080/sparql ─▶ SQL ─▶ Postgres
```

## Contents

| file | what |
|------|------|
| `docker-compose.yml` | Postgres + Ontop endpoint (`:8080/sparql`) |
| `seed.sql` | demo `accounts` table (Acme, Globex) |
| `input/mapping.ttl` | R2RML — the shape `r2g export-r2rml` emits |
| `input/ontop.properties` | JDBC connection for Ontop |
| `jdbc/` | drop the Postgres JDBC driver here |

## Run

```bash
# 1) Postgres JDBC driver (Ontop loads drivers from ./jdbc)
curl -L -o jdbc/postgresql.jar \
  https://jdbc.postgresql.org/download/postgresql-42.7.4.jar

# 2) bring it up
docker compose up          # Ontop endpoint at http://localhost:8080/sparql

# 3) sanity check (SPARQL over live SQL, no materialization)
curl -s http://localhost:8080/sparql \
  --data-urlencode 'query=PREFIX c: <urn:arango-sparql:concept#> SELECT ?name WHERE { ?a a c:Account ; c:name ?name }' \
  -H 'Accept: application/sparql-results+json'

# 4) the opt-in live integration test
ONTOP_SPARQL_ENDPOINT=http://localhost:8080/sparql \
  .venv/bin/python -m pytest tests/test_ontop_live.py -q
```

## Using your own schema

Generate the R2RML from a real relational source instead of the demo mapping:

```bash
# in the r2g repo, against your schema.json + mapping config
r2g export-r2rml --config mapping.yaml --schema schema.json -o mapping.ttl
```

Copy it to `input/mapping.ttl`, point `input/ontop.properties` at your database,
and restart. The concept IRIs stay under `urn:arango-sparql:concept#`, so the
relational leg and the Arango AQL leg speak the same vocabulary and the E1
planner routes across both.

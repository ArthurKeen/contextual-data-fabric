# Arango graph leg (M5)

Stands up ArangoDB for the M5 **graph leg**. An E1 sub-query (SPARQL) is
transpiled to AQL by the owned [`arango-sparql-py`](https://github.com/ArthurKeen/arango-sparql-py)
engine — driven by a `MappingBundle` derived from the source's `CSI` document
(the A3 adapter) — and executed here.

```
E1 sub-query (SPARQL) ─▶ arango-sparql-py translate ─▶ AQL ─▶ ArangoDB
```

## Prerequisites

`ArangoExecutor`'s live path needs the two engine libraries (kept out of the
`cdf` core deps — the adapter lazy-imports them):

```bash
.venv/bin/pip install python-arango -e ../arango-sparql-py
```

## Run

```bash
docker compose up                                   # ArangoDB at :8529
.venv/bin/python deploy/arango/seed.py              # create 'cmf' DB + 'tickets'

ARANGO_URL=http://localhost:8529 ARANGO_DB=cmf ARANGO_PASSWORD=cdf \
  .venv/bin/python -m pytest tests/test_arango_live.py -q
```

## Using your own graph

Produce the `CSI` for your ArangoDB with `arango-schema-analyzer` (reverse CSI),
or hand it the same forward CSI r2g emitted for the migration. Pass it to
`ArangoExecutor(csi=..., db=...)`; the concept IRIs stay under
`urn:arango-sparql:concept#`, so the graph leg and the Ontop relational leg share
one vocabulary and the E1 planner routes across both.

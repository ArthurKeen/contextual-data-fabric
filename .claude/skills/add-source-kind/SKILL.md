---
name: add-source-kind
description: Add support for a NEW engine KIND to the federation — build a native SourceExecutor (SPARQL BGP + r2g R2RML → the engine's SQL dialect) and wire it into from_env, following the ClickHouse/Snowflake template. Use when onboarding an engine the fabric has no executor for yet (e.g. DuckDB, BigQuery, MySQL, Databricks-SQL, Trino). NOT for adding another database of an already-supported engine — that is `add-source-instance`.
---

# Add a Source Kind (a native engine executor)

Add a **new engine** to the federation. This is **code**: a native `SourceExecutor` that
compiles an E1 single-source Basic Graph Pattern + the r2g-emitted R2RML directly to the
engine's SQL, plus a `from_env` dispatch branch. Reference implementations:
`src/cdf/adapters/clickhouse.py` (the original) and `src/cdf/adapters/snowflake.py`.

> Once the executor exists and is live-proven, adding an actual database of this kind is a
> data/config task — hand off to **`add-source-instance`**.

## Purpose

E1 hands each leg a **single-source BGP** (no FILTER/OPTIONAL/UNION — E1 refuses those), so
compiling it to SQL is a bounded problem, not general SPARQL→SQL. A native executor is the
right tool when the engine has a Python driver and either (a) Ontop has **no dialect** for it
(ClickHouse), or (b) it takes SQL directly and a native leg is simpler/cheaper than a second
Ontop container (Snowflake, per **ADR-0002**). Everything shares one r2g R2RML toolchain and
one concept vocabulary (`urn:arango-sparql:concept#`); only the executor differs.

## Decision — native vs Ontop endpoint
- **Ontop has a dialect AND you want the buy-path:** stand up an Ontop endpoint (R2RML over
  JDBC); no executor needed. But weigh ADR-0002 — for a SQL-direct source, native avoids a
  container, a JDBC jar, and dialect quirks (Snowflake's Arrow/Java-17 result-format wrinkle).
- **Ontop has NO dialect, OR native is preferred:** build the executor here. This skill covers that.

## Protocol

### Phase 1 — The dialect module  `src/cdf/adapters/<engine>.py`
Model on `clickhouse.py`/`snowflake.py`. **Reuse the dialect-neutral machinery** — import
`parse_r2rml`, `_collect_bgp`, `_parse_values`, `_literal_value`, `Mapping`, `Transport` from
`cdf.adapters.clickhouse` (R2RML parsing and BGP/VALUES extraction carry no dialect). Reimplement
only what actually differs:
- `_ident(name)` — identifier quoting (ClickHouse backticks; Snowflake double-quotes).
- `_sql_literal(value)` — string escaping + boolean/None rendering for this engine.
- `compile_sql(sparql, mapping)` — same shape as the reference, using this engine's `_ident`/`_sql_literal`.
- `<Engine>Executor` with an **injectable `transport`** (so the compiler is unit-testable with
  no DB) and a default transport over the engine's Python driver (lazy-imported).
- `<Engine>Error(ValueError)`; export via `cdf/adapters/__init__.py`.

**Dialect deltas that bite (get these right):**
- **Case folding + alias case.** If the engine folds unquoted identifiers (Snowflake→UPPER),
  physical names in R2RML are folded; quote them to match. **Quote SELECT aliases too** so the
  bare SPARQL var case survives — the reassembly join is keyed on the exact var name; an
  upper-folded alias silently misses.
- **Booleans / escaping.** TRUE/FALSE vs 1/0; `''` vs backslash escaping.
- **Subject templates.** The native executor reads only table/class/column from R2RML and
  **ignores `rr:subjectMap`/`rr:template`** — so a missing PK does NOT cause Ontop's
  NULL-template-row suppression here (that trap is Ontop-only).

### Phase 2 — Wire from_env
Add an `elif ref.kind == "<engine>" and env.get("<ENGINE>_...")` branch in
`FederationService.from_env` (`src/cdf/service/app.py`), **before** the generic Ontop fallback,
building the executor from the per-source R2RML (`CDF_R2RML_DIR/<source_id>.ttl`) + a DSN/conn args.
Keep credentials in env only (CC-7).

### Phase 3 — Tests
- **Unit** (`tests/test_<engine>.py`): fake transport, mirror `test_clickhouse.py` — assert the
  emitted dialect (quoting, alias case, literals, VALUES→IN, equi-join) + error cases + `from_env`
  wiring + a full `partition→execute→ground` pipeline case.
- **Live** (`tests/test_<engine>_live.py`): opt-in, `skipif` no DSN/creds — the dialect gate a
  fake transport can't be (a real server rejects SQL a fake accepts). Env-gated so CI skips cleanly.

### Phase 4 — Packaging + deploy
- `pyproject.toml`: add the driver to the mypy `ignore_missing_imports` module list; add the
  driver to `make install`; add a `[dense]`-style extra only if the engine needs an optional heavy dep.
- `deploy/<engine>/README.md` (+ a `docker-compose.yml`/`setup.sql` only if self-hosted; a cloud
  warehouse has no container).

## Acceptance
1. `ruff` + `mypy` + the unit suite green; `test_<engine>.py` proves the dialect.
2. `test_<engine>_live.py` green against a real instance (and skips cleanly without creds).
3. `from_env` builds the executor for the `<engine>` kind.
4. A `partition→execute→ground` test shows a `<engine>` leg joining another source.

## Gotchas
- **Don't fork the compiler.** Import the shared machinery from `clickhouse.py`; only the three
  dialect functions differ. There's a `TODO(simplify)` to lift the compiler into a dialect-
  parameterised module once ≥2 native legs are proven — do that refactor rather than a 3rd copy.
- **ADR-0002 governs the native-vs-agentic choice.** A native SQL leg is deterministic and
  token-free; never reach for a "cortex"/prompt leg for a source whose SQL door is open.
- Follow [[owl-naming-convention]] for any naming the executor surfaces.

## References
- Templates: `src/cdf/adapters/clickhouse.py`, `src/cdf/adapters/snowflake.py`.
- Dispatch: `src/cdf/service/app.py::FederationService.from_env`.
- Decision record: `ADR-0002`. Then: **`add-source-instance`** for the data.

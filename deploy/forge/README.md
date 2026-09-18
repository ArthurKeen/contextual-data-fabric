# Federation Forge — orchestration (ADR-0006, M15)

CDF's half of the Forge. r2g owns *generation* (`r2g.forge`: ontology → DDL /
loader / rows behind one dialect seam); this directory and `cdf.eval.forge`
own *orchestration*: shape sampling, the descriptor, the oracle, the suite.

```
make forge-suite          # fixture mode: sample → emit → byte-compare → run
python -m cdf.eval.forge emit --family chain --seed 7   # one shape
```

## What a shape directory holds (ADR-0006 D-1)

```
deploy/forge/shapes/<family>-<seed>/
  shape.yaml                the descriptor — the contract and the oracle
  ontology.json             CSI v1 conceptual model r2g's forge accepts
  expected-catalog.json     ownership, join keys (cross-system flagged), collisions
  goldens/<name>.json       mechanical template questions + expected outcome
  systems/<sys>.csi.json    fixture-mode CSI per system (producer: cdf-forge-fixture)
```

Descriptor + seed reproduce every file byte-identically; `--check-determinism`
regenerates into a temp dir and diffs. `suite-report.json` is the run record.

The committed suite is a **versioned artifact of generator + seed**: CI
regenerates into `deploy/forge/shapes` with the same flags as `make forge-suite`
and fails if anything under it differs (`git status --porcelain`), and
`tests/test_forge_orchestrator.py::test_committed_suite_matches_a_fresh_emit`
re-derives every shape from its own descriptor. A pin bump that changes
generated names — r2g 0.4.2 carries the singularizer fix — therefore requires
`make forge-suite` and a commit of the result in the same PR.

## Fixture mode vs. live mode

**Fixture mode** (per PR, in CI's offline job) has no databases. The dataset is
still synthesised once by r2g's generator (D-2), then projected into per-source
fixture rows; each golden runs through the real planner, executor and grounding
via `cdf.eval.golden.run_golden`. The per-system CSI is derived by CDF from the
descriptor with r2g's CC-12 naming and is labelled `cdf-forge-fixture` — it is
not analyzer output, and fixture mode therefore tests the fabric's
partition → execute → ground pipeline, not the estate's introspection.

**Live mode** (`python -m cdf.eval.forge live`; S2/S3) drives the same
descriptor through the estate. Nothing in the descriptor changes between modes.

```
make up                                  # the local compose stacks
make forge-live                          # deploy → … → goldens, all shapes (→ deploy/forge/live/)
make forge-live FORGE_LIVE_FLAGS="--only two_leg-421 --keep-ontop"   # one shape, Ontop left up
CDF_FORGE_LIVE=1 pytest tests/test_forge_live_local.py    # one shape per family + execute, asserted
```

CI runs both in the `live-local` job on every PR and on the weekday schedule,
and publishes `live-summary.json` as a build artifact.

Per shape, per system (`cdf.eval.forge.live`):

1. **deploy** — r2g's dialect emits DDL and a loader for the **projection** the
   system owns (ADR-0006 D-2): only its entities; a relationship stays a
   constraint only when both endpoints are on the system; a parent elsewhere
   leaves a plain `<parent>_id` column. Rows come verbatim from the one
   synthesised dataset, so join spines agree across systems. A fresh database
   `forge_<shape>_<system>` per system in Postgres, ClickHouse or ArangoDB
   (targets: `CDF_FORGE_PG_DSN`, `CDF_FORGE_CLICKHOUSE_DSN`, `ARANGO_*`;
   defaults follow the compose stacks).
2. **introspect + export** — the estate's forward pipeline: r2g connector →
   Auto-Map → `mapping_to_csi` / `mapping_to_r2rml` (relational), ASA
   `analyze` → `to_csi` (ArangoDB). `source_ref` is the system name, so the
   catalog's `<kind>:<name>` ids match the goldens.
3. **declare** — a relationship whose parent lives elsewhere is invisible to
   introspection (no constraint exists there), so the descriptor's
   cross-system relationships are applied as **declared references** after
   verifying each holds over the dataset's spine — the mechanism the demo
   estate already uses (CRM key overlay, `cmf-refs`) and RD-3 formalises. The
   report lists them by name, apart from what introspection found.
4. **drift** — fixture CSI vs estate CSI, conceptual model, CC-12-normalized.
   Zero drift on every locally deployable shape of the committed suite.
5. **onboard** — the catalog builder over the estate artifacts with the
   shape's **declared** capabilities on the overlay (the CC-14 probe, slice 4,
   is what strips them), `manifest.json` that loads exactly as
   `FederationService.from_env` loads it (`CDF_CATALOG_ROOT` names the
   artifact root), and the secret registry JSON `from_env` consumes.

6. **execute** (`--execute`; `cdf.eval.forge.live_execute`) — one **Ontop per
   Postgres leg** (a container on the compose network, fed the R2RML r2g
   exported for exactly that database; ~3 s to ready), its SPARQL endpoint
   completes the secret registry; `FederationService.from_env` with strict
   startup; the **CC-14 probe** of every declared capability against the live
   executor — a declaration the engine cannot honour is stripped from the
   manifest **and the goldens are re-derived from the probed capabilities**
   (fixture mode expects what was declared; live mode expects what the probe
   established; the report names the expectations that flipped); then every
   golden through `run_golden_live` — real planner, legs and grounding.
   `--keep-ontop` leaves the containers up for inspection.

Snowflake systems are **skipped by name** (local engines only); a shape with
any skipped system is skipped whole, because its goldens assume every leg.
Live artifacts live under `deploy/forge/live/` (gitignored): they describe
*this* environment, not the suite.

**What live mode found on its first run (2026-09-18):** the join, chain and
cross-leg-aggregation templates navigated a relationship predicate across
systems (`?payment c:paymentsToAssets ?asset . ?asset a c:Asset`). Fixture
executors answered that happily; the real legs cannot — no edge or constraint
exists across systems, the AQL leg compiles the predicate to an attribute that
is not there, and Ontop binds the entity as an IRI while the AQL leg binds a
scalar, so the variables can never join. The templates now use the fabric's
cross-source join contract — a shared key variable bound by a literal property
on each side (`<parent>Id` on the child, `id` on the parent) — and the same
goldens pass on the real fabric.

## Question families the oracle emits

| family | shape | expectation |
|---|---|---|
| `lookup` | one entity, ≤2 properties | grounded, bindings from the dataset |
| `join` | child ⋈ parent across two systems, on a **shared key** (`<parent>Id` on the child, `id` on the parent — the fabric's cross-source join contract; a relationship predicate cannot be navigated across systems) | grounded, bindings joined on the FK spine |
| `chain` | A → B → C across three systems | grounded |
| `single_leg_aggregation` | `COUNT` grouped by a boolean | grounded where the owning system **declares** GROUP BY in the descriptor; **named refusal** where it declares none (ADR-0005 D4) — see *Capabilities* below |
| `cross_leg_aggregation` | `COUNT` over a cross-system join | **named refusal** until S2's fold-combine lands; then rewritten as grounded with the counts already computed here |

## Capabilities: declared in fixture mode, probed in live mode

Each `systems[<name>]` block in `shape.yaml` carries a `capabilities`
declaration in the manifest's format (ADR-0005 D4), and every golden's
`sources[]` entry repeats it so `run_golden` applies it through the registry
(`SourceCatalog.declare_capabilities`). The declaration is **sampled per system,
independently of the engine kind** — a ClickHouse system may declare GROUP BY
and a Postgres system may declare none — so over a suite both admission
branches appear on every kind. Expected (the oracle reads the declaration) and
actual (the planner reads the same declaration through the registry) share only
that declaration; if the override path breaks, the goldens fail. Before this
change both sides consulted `default_capabilities_for_kind`, so the eight
refusal goldens could not fail and encoded "Snowflake and ClickHouse cannot
GROUP BY" — true of our executors that day, not of those engines (PR #34
review, item 3). In live mode the onboarding probe (CC-14) is what ties a
declaration to reality: a declaration the real executor cannot honor is
stripped, and that shape's aggregation goldens flip to refusals there.

## Sign-off (roadmap §4)

Which shape families are generated, in what order, and whether a descriptor may
enter CI as a golden is the Solutions Engineer's lane. Sign-off is recorded in
**`deploy/forge/signoff.yaml`**, a hand-maintained ledger keyed by golden name
that the generator never writes — so `make forge-suite` cannot wipe it, and the
generated files stay a pure function of code + seed (which is what keeps the
drift check above unambiguous). A golden absent from the ledger is unsigned; an
entry naming a golden the suite no longer emits fails the run (a signed-off
golden that vanished is regeneration drift the SE must see). `suite-report.json`
lists the signed goldens per shape and the totals. The CI step runs all goldens
as a **fabric regression check**, not as policy. Recording a sign-off is the
SE's commit:

```yaml
goldens:
  chain-422--join--Asset-Vendor:
    signedOff: true
    signedBy: pj
    signedOn: 2026-09-20
```

(The keys are `signedBy` / `signedOn`, not `by` / `on`: YAML 1.1 reads a bare
`on` key as the boolean `true`, and the loader refuses such an entry by name.)

## Pins (CC-9)

The generator core is installed from `deploy/pins/r2g-arango.txt` (a git pin
until r2g 0.4.2 ships `forge.py`; then a version band). The sampler asks r2g's
own naming normaliser whether a class name round-trips rather than assuming —
`Warehouse` does not (r2g hardening list #6), so it is skipped.

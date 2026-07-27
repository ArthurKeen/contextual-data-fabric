# Federated Query Optimization — Research Survey for M12

> **Status:** Research input to the M12 (Federated Optimizer) design, P4.2 in the product PRD.
> **Date:** 2026-07-26
> **Scope:** Optimization techniques applicable to the fabric's engine: conceptual SPARQL
> BGPs decomposed into per-source legs (Ontop/PostgreSQL, native BGP→SQL for Snowflake and
> ClickHouse, SPARQL→AQL for ArangoDB), inner-joined in-engine on shared keys. Legs are
> network calls (I/O-bound); in-engine joins handle dozens-to-thousands of bindings;
> federations are 2–6 legs; per-leg cost budgets (load and dollars) are first-class; every
> answer carries a grounded citation envelope with as-of semantics.
>
> **Evidence-quality flags used throughout:**
> - `[peer-reviewed, measured]` — published evaluation with numbers.
> - `[vendor, measured]` — vendor-run benchmark; directionally useful, not independent.
> - `[docs, mechanism]` — official documentation describing behavior, no benchmark.
> - `[reported, unverified]` — claim seen only in secondary summaries; primary numbers not
>   independently checked for this survey.
> No number in this document is invented; where a source gives no number, none is given.

---

## Executive summary

1. **The federated-SPARQL literature (FedX → CostFed → Odyssey) solved a harder problem
   than ours** — source *discovery* across dozens of overlapping endpoints. Single-owner
   concept ownership pre-captures the biggest published wins (source-selection pruning).
   What *does* transfer: bind-join batching, exclusive-group-style maximal per-source
   pushdown (our per-leg compilation is exactly this), selectivity-ordered joins, and the
   finding that **request-count reduction, not clever cost modeling, delivered the
   order-of-magnitude gains** (FedX: one FedBench query from ~170K requests to 23)
   `[peer-reviewed, measured]`.
2. **Production SQL federations converge on three mechanisms** that fit a hub owning no
   storage: (a) a per-connector *statistics interface* feeding a CBO (Trino); (b) *dynamic
   filtering* — a runtime semi-join structurally identical to our VALUES bind-join, with a
   fallback ladder (distinct-values → min/max range); (c) *largest-federable-subplan*
   pushdown (DataFusion). BigQuery's federated-source pushdown stops at filters and column
   pruning — evidence that **pushdown expressiveness is the prerequisite for
   optimization** (E1.5 before P4.2 is correctly sequenced) `[docs, mechanism]`.
3. **The join-strategy menu has four entries** — bind-join, fetch-and-hash, Bloom/sketch
   semi-join, broadcast — and the decision reduces to three numbers per edge: estimated
   left cardinality, right-side selectivity of the key, and per-leg budget. Bloom-filter
   pre-filtering measurably cuts transfer (predicate transfer: 3.1× average over Bloom
   join on TPC-H, single-engine setting) `[peer-reviewed, measured]`, but applying a
   Bloom filter *inside* a remote SQL source we don't control is operationally awkward;
   for our sources, **batched VALUES / temp-table bulk seeding is the practical
   equivalent** above the seed cap.
4. **Cardinality estimation is where optimizers fail** (Leis et al.: all tested estimators
   "routinely produce large errors"; the cost model matters far less)
   `[peer-reviewed, measured]`. For remote sources the cheapest robust statistics are:
   per-concept row counts, join-key NDV via HLL, MinHash overlap between join-key columns
   (already planned for Q5 — reuse), and **envelope telemetry** (every production query is
   a free training sample). Staged execution gives *exact* left-side cardinality at each
   stage boundary — a free, precise version of Spark AQE's stage-boundary re-optimization.
5. **Result caching with honest freshness is a solved pattern**: cache per-leg results
   keyed on (canonical sub-query, as-of, entitlement scope). Where the source supports
   time travel (Snowflake `AT`, Delta `VERSION AS OF`) an as-of-pinned key is immutable —
   a hit is correct by construction; elsewhere use TTL-plus-refresh-key probes (the Cube
   semantic-layer production pattern) `[docs, mechanism]`.
6. **LLM-era techniques: mostly not yet.** Learned optimizers (Neo, Bao) are
   research-grade; with ≤6 legs, exhaustive enumeration beats learning. Semantic caching
   of NL queries has a documented false-hit problem that is fatal for a grounded-citation
   engine — the safe variant caches the **NL→IR compilation, never the answer**. One
   low-risk pilot: regression-calibrating the leg cost model from envelope telemetry.

---

## Q1 — State of the art in federated SPARQL optimization

### FedX (Schwarte et al., ISWC 2011)

Techniques: source selection via `ASK` probes with a cache; **exclusive groups** (triple
patterns answerable by exactly one source are grouped and shipped as a single sub-query);
**bound joins** (a batch of intermediate bindings shipped in one request, encoded as a
SPARQL `UNION` block — modern engines use `VALUES`); rule-based join ordering by a
variable-counting heuristic (fewest free variables first). No persisted statistics at all.

Measured evidence `[peer-reviewed, measured]`: on FedBench, the combination of bound joins
and exclusive groups reduces remote request counts by orders of magnitude — e.g. query CD3
required 170,579 requests under DARQ and 93,248 under AliBaba versus **23 under FedX**.
The paper's headline result is that *minimizing the number of remote calls*, not cost
modeling, produced the step-change in latency.

Relevance: our per-source leg compilation *is* the exclusive-group idea generalized; our
trailing-`VALUES` seeding *is* the bound join. The FedX contribution we have not yet
absorbed is **join ordering by selectivity** — done there with a free heuristic, no
statistics required.

### SPLENDID (Görlitz & Staab, COLD 2011)

Uses **VoID descriptions** (per-dataset triple counts, per-predicate counts, distinct
subjects/objects) as its statistics substrate, plus `ASK` fallback for patterns VoID can't
disambiguate; dynamic-programming plan enumeration; and a cost model that **chooses per
join between bind join and hash join** — the earliest clear statement of the strategy
choice our engine needs `[peer-reviewed, measured — evaluation in paper]`.

### HiBISCuS (Saleem & Ngonga Ngomo, ESWC 2014)

Source-selection-only contribution: models the query as a directed labeled hypergraph and
prunes sources using **URI-authority summaries** (which authorities appear as
subjects/objects per predicate per source). Bolted onto FedX, SPLENDID and DARQ, it reduced
the number of sources queried without recall loss and "significantly reduces the execution
time of the selected engines on most of the benchmark queries" `[peer-reviewed, measured]`.
Relevance to us: **low** — single-owner concept ownership makes source selection trivial in
the fabric. It becomes relevant only if concept ownership ever becomes overlapping.

### CostFed (Saleem et al., SEMANTiCS 2018)

Index-assisted source selection (predicate + URI-authority summaries) plus a cost model
that explicitly handles **skew** in subject/object frequency distributions. Measured on
LargeRDFBench `[peer-reviewed, measured]`:

- Ranked 1st on 12/14 (large-data) queries; **2.71× faster than FedX** overall, 1.7× faster
  than SemaGrow, 7.34× faster than ANAPSID.
- Source selection: 104 sources selected vs 199 (FedX/SPLENDID/SemaGrow); **0 ASK requests**
  vs FedX's 1,196; ~1 ms selection time.
- Index: 99.99% compression of the raw data into summaries; ~60 min to build.

Note the decomposition: much of CostFed's win over FedX is *source-selection* efficiency,
which we get for free. Its residual lesson is that a **small, skew-aware summary** (not
full histograms) was enough to pick good join orders.

### Odyssey (Montoya, Skaf-Molli & Hose, ISWC 2017)

The statistics-maximalist position: **characteristic sets** (per-entity property
combinations) and **characteristic pairs** (links between star shapes), extended with
*federated* characteristic pairs for cross-dataset entity links — better join-cardinality
estimates than SPLENDID/SemaGrow's per-predicate stats, hence better DP plans
`[peer-reviewed, measured — evaluation in paper]`. Cost: construction requires a full
scan/sort of every dataset — feasible for owned copies, expensive for rented remote
sources. Relevance: adopt the *idea* (join-edge-specific statistics) via sketches, not
the mechanism.

### ANAPSID (Acosta et al., ISWC 2011)

The adaptivity position: non-blocking operators (`agjoin`, `adjoin`) producing results
incrementally and adapting to endpoint delays/burstiness; reported to beat
symmetric-hash-join operators depending on selectivity and transfer delays, and to produce
answers when fixed-plan engines time out `[peer-reviewed, measured — evaluation in paper]`.
Relevance: the *failure-handling* motivation transfers (we already declare failed legs);
full streaming operators do not — at dozens-to-thousands of bindings there is nothing to
stream.

### Cross-engine empirical evaluation (Qudus et al., Semantic Web Journal 2021)

Benchmarked five cost-based engines (CostFed, SPLENDID, LHD, Odyssey, SemaGrow) on
LargeRDFBench with novel metrics for **cardinality-estimation accuracy** (relative error,
q-error, cosine similarity error at triple-pattern, join and plan level)
`[peer-reviewed, measured]`. *Directionally*: estimation accuracy correlates with runtime,
and CostFed produced the fewest estimation errors and shortest runtimes on the majority of
queries `[reported, unverified — full tables paywalled; metric framework public in
dice-group/CostBased-FedEval]`. The transferable output is the **metric suite itself**:
q-error per join is the right acceptance metric for M12's estimator.

### Benchmarks

FedBench (2011; small, 10 datasets) and **LargeRDFBench** (13 real interlinked datasets,
>1B triples, 32 real queries; measures runtime, source-selection quality, request counts,
result completeness/correctness) `[peer-reviewed]`. Both stress multi-endpoint source
ambiguity — structurally unlike our 2–6-leg, known-ownership setting — so published
rankings should not be assumed to transfer wholesale.

---

## Q2 — Cost-based federation in production SQL engines

### Trino / Presto

- **CBO**: join-order enumeration and join-distribution choice (broadcast vs partitioned)
  driven by **connector-provided table statistics** (row counts, NDV, min/max, null
  fractions). The stats interface is the design to copy: each connector answers "what do
  you know about this table", the hub owns the model `[docs, mechanism]`
  (Presto: SQL on Everything, ICDE 2019 `[peer-reviewed]`).
- **Dynamic filtering**: build-side join-key values are collected at runtime and pushed
  into probe-side scans; past size thresholds
  (`dynamic-filtering.max-distinct-values-per-driver` etc.) Trino degrades to a **min/max
  range filter**; whether the filter reaches the remote system is per-connector (Hive:
  partition/stripe pruning; Starburst JDBC: pushdown into the remote `WHERE`)
  `[docs, mechanism]`. Vendor-measured ~**9× on TPC-DS q71** `[vendor, measured]`.
  Structural read: dynamic filtering *is* a runtime-applied bind-join with a
  size-triggered fallback ladder. Our `VALUES` seeding implements rung one; the min/max
  rung and the costed "give up and hash" rung are missing.
- **What transfers to a storage-less hub**: the stats interface, the fallback ladder, and
  broadcast-vs-partitioned as a *seeding-direction* decision (which leg's keys seed which).
  What does not: worker-local scan pruning (we have no scans).

### BigQuery (federated sources & Omni)

For external/federated sources, SQL pushdown is limited to **column pruning and filter
pushdown** — no join, aggregate, limit or compute pushdown `[docs, mechanism]`. Google's
answer to the resulting pain is Omni: *move the engine to the data* rather than optimize
the federation. Lesson for M12: a federation whose legs can't push filters and aggregates
has nothing for an optimizer to arrange — **E1.5 expressiveness before P4.2 optimization**.

### Apache DataFusion (datafusion-federation)

Each remote engine registers a **FederationProvider with its own optimizer rule**; a
federation optimizer finds the **largest sub-plan answerable by one provider**, which
self-determines how much it absorbs (same-source join pushdown, TopK pushdown)
`[docs, mechanism]`. This is FedX's exclusive groups rediscovered in Rust; it validates our
per-source leg compiler. The increment worth stealing: **intra-source join pushdown** — two
concepts owned by one source must ship as one leg, never two.

---

## Q3 — The join-strategy menu

The bind join originates in Garlic's mediator optimizer (Haas, Kossmann, Wimmers & Yang,
VLDB 1997) — ship outer bindings to the inner source, retrieve only matching rows
`[peer-reviewed]`. Sixty years of literature later the menu is still four items:

| Strategy | Mechanism | Transfer cost | Wins when | Loses when |
| --- | --- | --- | --- | --- |
| **Bind join** (our `VALUES` seed) | Push distinct left keys into right leg's query | O(‖left keys‖ up + matching rows down) | Left small (≤ seed cap), right large & selective on key, key indexed at source | Left keys exceed cap / statement-size limits; unindexed key at source (Q7 gating) |
| **Fetch-and-hash** (our unseeded fallback) | Pull both legs fully, hash-join in engine | Full both legs down | Both legs small; key not pushable; right not selective anyway | Either leg is large → budget blowout |
| **Bloom/sketch semi-join** | Ship compact filter of left keys; right pre-filters before returning | O(filter size up + superset of matches down) | Left keys ≫ cap but right very selective; source can apply the filter | Source can't evaluate the filter; false-positive superset still large |
| **Broadcast small leg** | Fetch smallest leg entirely first; seed *all* others from it | Small leg down + seeded legs | One leg is known-tiny (dimension-like concept) | No leg is small; wrong "smallest" guess |

Decision inputs per join edge: estimated left cardinality, key NDV on both sides
(bind-join payoff ≈ right-leg reduction factor = matched rows / total rows), source
statement limits, index presence (PRD Q7), and the per-leg budget (CC-11).

Evidence on the semi-join/Bloom family:

- In distributed in-memory settings network dominates join cost — up to ~90% of runtime
  `[peer-reviewed — "The End of Slow Networks", 2015]`; key-set reduction before transfer
  is leverage.
- **Predicate transfer** (Yang, Zhao, Yu & Koutris, CIDR 2024) generalizes Bloom join to
  multi-table pre-filtering: **3.1× average speedup over Bloom join on TPC-H**
  `[peer-reviewed, measured — single-engine, not a federation; an upper bound on the
  idea, not a federation forecast]`.
- **brTPF** (Hartig & Buil-Aranda, ODBASE 2016) — batched bind joins at the RDF-interface
  level reduced HTTP request counts and network load versus per-pattern interfaces
  `[peer-reviewed, measured — concrete percentages not re-verified for this survey]`.

**Practicality note for our sources.** A literal Bloom filter needs the *source* to
evaluate it (UDF or engine support). Against Snowflake/ClickHouse/Postgres the pragmatic
ladder above the 1,000-key seed cap is: (1) **batched bind join** — cap-sized `VALUES`
chunks across parallel requests, union results; (2) **temp-table bulk bind** — bulk-load
keys into a session temp table, join server-side (the Bloom semi-join's exact-filter
cousin); (3) **min/max range seed** when even key upload violates budget;
(4) fetch-and-hash as the floor. A true Bloom filter pays only where key upload is
expensive and a filter UDF is feasible — defer.

---

## Q4 — Statistics and adaptivity for remote heterogeneous sources

### Why bother (and why not too much)

Leis et al., *How Good Are Query Optimizers, Really?* (VLDB 2015) `[peer-reviewed,
measured]`: across industrial optimizers, cardinality estimators "routinely produce large
errors" (one 2-join query drew estimates of 3–310 rows against a true 2,600 depending on
syntactic order); **the cost model contributes far less than the cardinalities**;
exhaustive enumeration still helps despite bad estimates. Consequences for M12: spend on
cardinality inputs, not cost-model sophistication; with ≤6 legs, enumerate exhaustively;
prefer *robust* plans (bounded downside under estimate error) over optimal-if-lucky ones.

### The statistics menu, cheapest-first

| Source of truth | What it gives | Cost to acquire | Assessment |
| --- | --- | --- | --- |
| **Envelope telemetry** (already recorded) | Observed per-leg latency, cardinality, bytes, dollars per (source, concept, shape) | Zero — close the loop | **First.** Every production query is a labeled sample; also the only estimator that captures source load variance |
| **Per-concept counts** (VoID-style: rows per concept, per property) | Base cardinalities | One cheap `COUNT(*)`/`approx_count_distinct` sweep at onboarding + scheduled refresh | **First.** This is literally VoID re-hosted in the catalog `[docs — W3C VoID; SPLENDID shows it suffices for join ordering]` |
| **HLL NDV + min/max per join-key column** | Key selectivity; bind-join payoff estimates; range-seed bounds | Native at every source (Snowflake `APPROX_COUNT_DISTINCT`, ClickHouse `uniq`, Postgres `n_distinct`, Arango) | **First.** Mergeable, tiny, standard `[peer-reviewed — Ertl, HLL estimation; SetSketch VLDB 2021]` |
| **MinHash overlap sketches** between join-key columns | Cross-source join selectivity (|A∩B| via Jaccard) — the number bind-join costing actually needs | Already planned for Q5 join discovery — **reuse, don't rebuild** | Second. One sketch pipeline, two consumers (join discovery + optimizer) |
| Histograms on hot columns | Filter selectivity | Per-source sampling jobs | Third — only after E1.5 filter pushdown makes filter selectivity matter |
| Characteristic sets / pairs (Odyssey) | Star-shape and cross-source link cardinalities | Full scan+sort of each source | **Skip** — cost model appropriate to owned copies, not rented sources |
| Learned estimators | Point estimates from models | Training + drift management | **Skip for now** — see Q6 |

### Adaptive mid-query re-optimization

- **Spark AQE** `[docs, mechanism; Databricks vendor material]`: re-plans at *stage
  boundaries* using materialized-stage statistics — switches join strategies, coalesces
  partitions, splits skewed partitions. The insight: the only trustworthy statistics are
  the ones you observe mid-flight.
- **Trino dynamic filtering** `[docs, mechanism]`: runtime semi-join creation, with
  size-triggered degradation.
- **ANAPSID** `[peer-reviewed]`: operator-level adaptivity to endpoint delays.

**Our engine already has the AQE hook for free.** Execution is staged: at each stage
boundary the engine holds the *exact* cardinality of the accumulated bindings — not an
estimate. Adaptivity for an I/O-bound Python engine is therefore a policy function
evaluated between stages, not a new operator runtime: given exact |bindings| and remaining
budgets, re-choose each pending leg's strategy (seed / batched seed / temp-table / range /
hash) and stage assignment. A leg returning 100× its estimate changes the downstream plan
*deterministically*, with no mid-leg cancellation machinery. (Later refinement:
`concurrent.futures.as_completed` to start seeding a dependent leg as soon as its specific
upstream finishes.)

---

## Q5 — Result caching with explicit freshness / as-of semantics

Production reference points, each embodying a distinct freshness contract
`[docs, mechanism]`:

- **Snowflake result cache** — exact-statement reuse only while underlying data is
  unchanged; 24 h retention, renewed on reuse up to 31 days. Contract: *bit-identical or
  miss* — correct by construction.
- **ClickHouse query cache** — explicitly pragmatic: TTL (default 60 s,
  `query_cache_ttl`), lazy eviction, documented tolerance of transient staleness.
  Contract: *bounded staleness, declared*.
- **Cube pre-aggregations** — the closest analog to a hub owning no storage: cached
  rollups invalidated by a **refresh key** (cheap probe like `MAX(updated_at)`, or a
  fixed schedule). Contract: *probe-verified*.
- Research framing: *Stale View Cleaning* (VLDB 2015) formalizes answering from stale
  views with bounded error `[peer-reviewed]`; SPARQL-side federated-fragment maintenance
  exists but is not production-hardened.

**Design consequence for the fabric** — the envelope's as-of field makes cache honesty a
key-design problem rather than an invalidation problem:

1. **Cache per-leg results, not fused answers.** Key = `(canonical post-seeding leg
   sub-query — the VALUES clause is part of the key, as-of, entitlement scope,
   source-schema version)`. Citations stay grounded because the seeded SPARQL in the key
   *is* what the envelope cites.
2. **Two freshness tiers.** Tier A (time-travel sources: Snowflake `AT(TIMESTAMP => …)`,
   Delta `VERSION AS OF`): pin the leg to the as-of — the cached entry is **immutable, a
   hit can never be stale** `[docs, mechanism]`. Tier B (no pinning): TTL per source class
   plus optional refresh-key probe (Cube pattern); the envelope declares `as_of` and
   `freshness = ttl/probe`, so staleness is *stated*, never silent.
3. **Semantic (NL-level) caching is a different, riskier thing** — see Q6.

---

## Q6 — LLM-era techniques: what's real, what's early

### Learned query optimizers — skip, with one narrow exception

- **Neo** (VLDB 2019): fully learned planner bootstrapped from a classical optimizer
  `[peer-reviewed, measured]`. Research system.
- **Bao** (SIGMOD 2021): learns only to *steer* an optimizer via coarse hint sets
  (tree-CNN + Thompson sampling) `[peer-reviewed, measured]`. Its reference implementation
  states it should **not be used in production**; Microsoft's QO-Advisor productionized a
  descendant, at hyperscale unlike ours.
- Assessment for M12: with 2–6 legs (≤ `6!` orders × 4 strategies) the plan space is
  exhaustively enumerable in microseconds — learning join order solves a non-problem.
  **The narrow exception worth piloting:** *cost-model calibration* — per-source
  regressions (latency vs. seeded-key count and result size; dollars vs. bytes) on
  envelope telemetry. "Learned" only in the sense ANALYZE is; no tail-risk plan-choice
  failure mode; directly improves budget-refusal quality (Q7's "refuse with remedy").

### Semantic caching of NL queries — dangerous at answer level, useful at IR level

- **GPTCache** (embedding-similarity cache for LLM responses) `[docs, mechanism]`.
  Documented failure mode: contextually different queries that embed similarly produce
  **false cache hits** — MeanCache (arXiv 2024) reports GPTCache returning 54 false hits
  vs 3 for its alternative on contextual queries `[peer-reviewed preprint, measured]`.
  Practitioner guidance: 0.90–0.95 similarity thresholds plus cross-encoder reranking for
  ~96–98% precision `[reported, unverified — practitioner blogs, no independent benchmark]`.
- For a fabric whose product *is* grounded citations, a false semantic hit does not
  degrade gracefully — it returns a well-cited answer to the wrong question. **Never
  cache at the answer level.** The safe pilot: cache the **NL→conceptual-IR compilation**
  (embedding match on the utterance → candidate IR → *deterministic* validation that the
  IR's concepts/filters match the new utterance's extracted slots before reuse), then
  execute the IR through the normal planner. A wrong reuse is caught at slot-validation
  or yields a visibly wrong IR — inspectable, unlike a wrong cached answer.
- LLM-for-query-optimization surveys (e.g. arXiv 2412.17558) mostly concern RAG query
  *rewriting*, not DB plan optimization — adjacent field, don't over-read the titles
  `[docs/survey]`.

---

## Ranked technique table for M12

Impact assessed for 2–6-leg, I/O-bound federations under per-leg budgets; effort assessed
against the existing Python engine (staged executor, ThreadPoolExecutor, SEED_CAP=1000,
envelope telemetry already recorded).

| # | Technique | Expected impact | Effort | Evidence base | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | Parallel legs within stages | Wall clock ≈ slowest leg, not sum (up to ~N× for N independent legs) | Done/in flight | Mechanism-obvious; FedX/Trino concur | Already in executor |
| 2 | Per-leg result cache keyed (canonical seeded sub-query, as-of, entitlement) | Eliminates repeat legs entirely in agent/demo workloads; honest by key design | **Low** | Snowflake/ClickHouse/Cube `[docs]` | Tier A immutable / Tier B TTL+probe |
| 3 | Stage-boundary adaptive strategy choice (exact accumulated cardinality) | Robustness: no estimate-error blowups; better budget refusals | **Low–Med** | Spark AQE `[docs]`; Leis on estimate fragility `[peer-reviewed]` | The engine's staging makes this cheap |
| 4 | Join order + seeding direction from cheap stats (counts, HLL NDV, MinHash overlap) | Large transfer cuts when the engine currently seeds the wrong way or stages relational-first blindly | **Med** | SPLENDID/CostFed `[peer-reviewed, measured]`; Trino CBO `[docs]` | Broadcast-smallest generalizes the fixed two-stage order |
| 5 | Seed-overflow ladder: batched VALUES → temp-table bulk bind → min/max range → hash | Converts today's over-cap "run unseeded" cliff into graceful degradation | **Med** | Trino DF fallback ladder `[docs]`; brTPF batching `[peer-reviewed]` | Biggest win on wide joins that currently blow the cap |
| 6 | Statistics collection pipeline (onboarding sweep + telemetry feedback) | Foundational for #4; improves refusal messages | **Low–Med** | VoID/SPLENDID; Qudus q-error metrics `[peer-reviewed]` | Reuse Q5 sketch pipeline |
| 7 | Intra-source join pushdown (two same-source concepts → one leg) | Removes an entire leg + in-engine join when it fires | **Med** | DataFusion federation `[docs]`; FedX exclusive groups `[peer-reviewed, measured]` | Check: may already hold by construction |
| 8 | Bloom-filter semi-join at capable sources | Transfer cuts beyond what key-upload allows | **High** | Bloom-join literature; predicate transfer 3.1× `[peer-reviewed, single-engine]` | Defer; temp-table bind covers most of the value |
| 9 | Learned cost-model calibration from envelope telemetry | Modest latency; better $-refusals | **Low** (pilot) | Bao-adjacent, but risk-free framing | Regression, not RL |
| 10 | Semantic NL→IR cache (never answer-level) | Latency/cost on repeated NL questions | **Med** (pilot) | GPTCache false-hit evidence `[measured]` argues for IR-level only | Slot-validation gate mandatory |

---

## Recommended design sketch — M12 optimizer module

### Statistics to collect first (in this order)

1. **Close the telemetry loop** (effectively free): persist per-(source, concept, shape)
   observed latency, cardinality, bytes and dollars from the envelope into the catalog;
   expose `p50/p95` per leg shape — the cost model's training data *and* ground truth.
2. **Onboarding stat sweep**: per concept — row count; per join-key property — NDV
   (source-native approx functions), min/max, null ratio. Scheduled refresh; staleness
   stamped in the catalog.
3. **MinHash overlap sketches per join edge** — shared artifact with Q5 join discovery;
   Jaccard-derived intersection estimates are bind-join payoff predictions.
4. Defer: histograms (until E1.5 filter pushdown); characteristic sets (never, for
   rented sources).

### Join strategies, implementation order

1. **(exists)** Staged bind-join via trailing `VALUES`, SEED_CAP=1000; over-cap → unseeded
   fetch-and-hash.
2. **Stage ordering by estimated cardinality** — smallest-estimated-leg-first
   (broadcast-style) when stats disagree with the fixed relational-then-graph default;
   keep the current order as the zero-stats fallback.
3. **Batched bind-join**: over-cap key sets split into cap-sized `VALUES` chunks run
   concurrently on the existing pool, results unioned; chunk-count bounded by leg budget.
4. **Temp-table bulk bind** for Snowflake/ClickHouse/Postgres past a chunk-count
   threshold; capability-gated in the catalog (Q6 registry).
5. **Min/max range seeding** (Trino's fallback) when keys are numerically clustered.
6. **Hash-join floor** (exists) with explicit costing, so the planner *chooses* it rather
   than falling into it.
7. **Bloom semi-join**: last, per capable source, only if telemetry shows temp-table
   binds under budget pressure.

Gating rules throughout (PRD Q7/CC-11): bind-join only into indexed key columns; every
strategy admissible only within the per-leg budget; inadmissible plans route to the
admission-control refusal carrying the estimate that condemned them — the optimizer's
estimates are also the refusal engine's evidence.

### Adaptive execution in the I/O-bound Python engine

- **Plan = ordered stages + per-leg strategy + budget envelope**, produced by exhaustive
  enumeration over ≤6 legs (microseconds; Leis licenses exhaustiveness).
- **Re-cost at every stage boundary** with the exact accumulated binding count: re-choose
  pending legs' strategies and, if a leg returned ≫ estimate, re-order remaining stages.
  Log (estimate, actual) → per-join q-error into telemetry (the Qudus metric,
  self-applied).
- **Within a stage**: keep ThreadPoolExecutor; move from wait-all to `as_completed` so a
  dependent leg's seeding starts when its specific upstream finishes.
- **No symmetric-hash / streaming operators** — unjustified at ≤ thousands of bindings.
- **Caching layer under the executor**: check the (seeded-sub-query, as-of, entitlement)
  cache before dispatch; Tier A gets as-of pinning in the leg SQL, Tier B gets TTL +
  refresh-key probes; every hit declared in the envelope (`served_from_cache`, `freshness`).
- **Acceptance metrics** (test-what-you-touch): per-join q-error distribution; wall-clock
  vs. sequential baseline on the golden federation; bytes per query; refusal precision
  (refused plans that would indeed have breached budget).

### Explicit non-goals (with reasons)

- Source-selection machinery (HiBISCuS/CostFed summaries) — single-owner ownership makes
  it moot.
- Learned join ordering (Neo/Bao) — plan space too small; production-readiness unproven
  by the authors' own admission.
- Answer-level semantic caching — false-hit evidence disqualifies it for a
  grounded-citation product; IR-level pilot only.
- Symmetric-hash / streaming join operators — nothing to stream at our binding volumes.

---

## Citations

### Federated SPARQL (Q1)
- FedX: Optimization Techniques for Federated Query Processing on Linked Data (ISWC 2011) — http://dbis.informatik.uni-freiburg.de/content/team/schmidt/docs/iswc11_fedx.pdf
- FedX summary incl. request-count table — https://www.openresearch.org/wiki/FedX:_Optimization_Techniques_for_Federated_Query_Processing_on_Linked_Data
- SPLENDID: SPARQL Endpoint Federation Exploiting VOID Descriptions (COLD 2011) — https://ceur-ws.org/Vol-782/GoerlitzAndStaab_COLD2011.pdf
- HiBISCuS: Hypergraph-Based Source Selection (ESWC 2014) — https://svn.aksw.org/papers/2014/HiBISCuS_ESWC/public.pdf
- CostFed: Cost-Based Query Optimization for SPARQL Endpoint Federation (SEMANTiCS 2018, preprint) — http://olafhartig.de/files/SaleemEtAl_CostFed_Semantics2018_Preprint.pdf
- The Odyssey Approach for Optimizing Federated SPARQL Queries (ISWC 2017) — https://arxiv.org/pdf/1705.06135
- ANAPSID: An Adaptive Query Processing Engine for SPARQL Endpoints (ISWC 2011) — http://iswc2011.semanticweb.org/fileadmin/iswc/Papers/Research_Paper/03/70310017.pdf
- Qudus et al., An Empirical Evaluation of Cost-based Federated SPARQL Query Processing Engines (SWJ 2021) — https://arxiv.org/abs/2104.00984 ; metric code: https://github.com/dice-group/CostBased-FedEval
- LargeRDFBench (JWS 2017) — https://svn.aksw.org/papers/2017/LargeRDFBench_JWS/public.pdf ; https://github.com/dice-group/LargeRDFBench
- brTPF: Bindings-Restricted Triple Pattern Fragments (ODBASE 2016) — https://arxiv.org/abs/1608.08148

### Production SQL federation (Q2)
- Presto: SQL on Everything (ICDE 2019) — https://trino.io/Presto_SQL_on_Everything.pdf
- Trino cost-based optimizations — https://trino.io/docs/current/optimizer/cost-based-optimizations.html ; CBO intro: https://trino.io/blog/2019/07/04/cbo-introduction.html
- Trino dynamic filtering — https://trino.io/docs/current/admin/dynamic-filtering.html ; blog (TPC-DS q71 ~9×): https://trino.io/blog/2019/06/30/dynamic-filtering.html
- Starburst dynamic filtering (JDBC pushdown) — https://docs.starburst.io/latest/admin/dynamic-filtering.html
- BigQuery federated queries introduction (pushdown limits) — https://docs.cloud.google.com/bigquery/docs/federated-queries-intro
- BigQuery Omni introduction — https://docs.cloud.google.com/bigquery/docs/omni-introduction
- datafusion-federation — https://github.com/datafusion-contrib/datafusion-federation
- datafusion-table-providers — https://github.com/datafusion-contrib/datafusion-table-providers

### Join strategies (Q3)
- Haas, Kossmann, Wimmers, Yang: Optimizing Queries Across Diverse Data Sources (VLDB 1997) — https://db.in.tum.de/research/publications/conferences/garlic.pdf
- Predicate Transfer: Efficient Pre-Filtering on Multi-Join Queries (CIDR 2024) — https://www.cidrdb.org/cidr2024/papers/p22-yang.pdf ; https://arxiv.org/abs/2307.15255
- Including Bloom Filters in Bottom-up Optimization (2025) — https://arxiv.org/abs/2505.02994
- Optimizing Distributed Joins with Bloom Filters — https://link.springer.com/chapter/10.1007/978-3-540-89737-8_15
- The End of Slow Networks: It's Time for a Redesign (network share of join cost) — https://arxiv.org/pdf/1504.01048

### Statistics & adaptivity (Q4)
- Leis et al., How Good Are Query Optimizers, Really? (VLDB 2015) — https://www.vldb.org/pvldb/vol9/p204-leis.pdf
- Still Asking: How Good Are Query Optimizers, Really? (VLDB 2025) — http://www.vldb.org/pvldb/vol18/p5531-viktor.pdf
- SetSketch: Filling the Gap between MinHash and HyperLogLog (VLDB 2021) — https://vldb.org/pvldb/vol14/p2244-ertl.pdf
- Ertl, New cardinality estimation algorithms for HyperLogLog sketches — https://github.com/oertl/hyperloglog-sketch-estimation-paper
- Spark SQL performance tuning / AQE — https://spark.apache.org/docs/latest/sql-performance-tuning.html
- Databricks: Adaptive Query Execution — https://www.databricks.com/blog/2020/05/29/adaptive-query-execution-speeding-up-spark-sql-at-runtime.html

### Caching & freshness (Q5)
- Snowflake: Using Persisted Query Results — https://docs.snowflake.com/en/user-guide/querying-persisted-results
- Snowflake: Time Travel — https://docs.snowflake.com/en/user-guide/data-time-travel
- ClickHouse: Query cache — https://clickhouse.com/docs/operations/query-cache
- Delta Lake time travel — https://delta.io/blog/2023-02-01-delta-lake-time-travel/
- Cube: Caching overview / refreshing pre-aggregations — https://cube.dev/docs/product/caching ; https://cube.dev/docs/product/caching/refreshing-pre-aggregations
- Stale View Cleaning (VLDB 2015) — https://arxiv.org/pdf/1509.07454

### LLM-era (Q6)
- Neo: A Learned Query Optimizer (VLDB 2019) — https://www.researchgate.net/publication/335904669_Neo_a_learned_query_optimizer
- Bao: Making Learned Query Optimization Practical (SIGMOD 2021) — https://dl.acm.org/doi/10.1145/3448016.3452838 ; repo caveat: https://github.com/learnedsystems/BaoForPostgreSQL
- Kepler: Robust Learning for Faster Parametric Query Optimization — https://arxiv.org/pdf/2306.06798
- GPTCache — https://github.com/zilliztech/gptcache
- MeanCache: User-Centric Semantic Caching for LLM Web Services (false-hit comparison) — https://arxiv.org/pdf/2403.02694
- A Survey of Query Optimization in Large Language Models (RAG-focused; note scope) — https://arxiv.org/html/2412.17558v3

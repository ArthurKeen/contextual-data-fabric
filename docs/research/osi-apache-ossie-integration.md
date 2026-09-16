---
title: "Open Semantic Interchange (now Apache Ossie) in the Contextual Data Fabric — the eight questions"
type:
  - internal
  - research
  - integration-analysis
date: 2026-09-16
status: draft — for review (PJ, Arthur; reviewer: Kevin)
related:
  - "docs/contextual-data-fabric-prd.md (§6 cross-cutting 'OSI compliance surfaced', §10.12 CC-12, §12 RD-1/RD-3)"
  - "docs/architecture/module-04-mapping-layer/specification.md (FR-6 OSI export/import)"
  - "docs/architecture/module-05-federated-query-engine/adr/ADR-0005-cross-leg-aggregation-and-capability-registry.md"
  - "docs/research/okf-open-knowledge-format-integration.md (the sibling standards analysis)"
  - "docs/research/aws-context-ontology-accelerator-comparison.md (COA speaks OSI)"
  - "docs/roadmap-2026H2.md (where this lands on the calendar)"
---

# Open Semantic Interchange (Apache Ossie) in the fabric

> **The ask (Arthur, 2026-09-15):** research OSI integration into CDF against
> eight questions — 3.1 effect on NL→SPARQL, 3.2 how OSI definitions enter CDF
> (does it extend the ontology?), 3.3 federator push-down of definitions,
> 3.4 harvesting definitions from source systems, 3.5 definitions for graph
> algorithms (Arango, virtual graph), 3.6 auto-generated query templates,
> 3.7 human-in-the-loop implications, 3.8 scheduling. Seeded with a Gemini
> note, treated as a map, not as evidence.
>
> **Method.** Primary sources read 2026-09-16: the `apache/ossie` repository
> (core `spec.md` 0.2.0.dev0, `ontology/ontology.md` + `ontology.json`,
> `core-spec/expression_language.md`, `converters/*/README.md`, `ROADMAP.md`,
> `DISCLAIMER`), the ASF incubator status page, Snowflake's function docs,
> GitHub discussions #22, #53, #68, #82, #101 and issue #107; plus a code read
> of what the estate already does with OSI (RSA 0.8.0 `connectors/osi.py`,
> CDF `src/cdf/query/nl.py`, `planner.py`, `catalog/capabilities.py`, the NL
> corpus and goldens). Claims resting on press or blog coverage are marked
> `[secondary]`. Where a spec field is proposed but not adopted it is marked
> `[proposed]`.

## 0. Answers at a glance

| # | Question | Short answer | Lands in |
|---|---|---|---|
| 3.1 | NL→SPARQL effect | Metrics become **named targets**: the LLM selects a certified metric and slices it by dimensions instead of composing aggregates. Helps only if (a) `ai_context`/descriptions actually reach the prompt (today the prompt carries names only) and (b) the engine admits the aggregate shape (today the prompt forbids aggregation the engine can already run single-leg). | `nl.py` prompt + corpus; M10 eval |
| 3.2 | Extend the ontology? | **No, three artifacts, not one.** Ossie's *ontology layer* ↔ our OWL/CSI conceptual model; its *logical layer* (datasets/fields/relationships) ↔ CSI physical mapping + R2RML; its *metrics* ↔ a **new catalog object** (`Metric`), not an OWL axiom. | M11 catalog; CSI extension; FR-6 |
| 3.3 | Push-down | Definitions are not pushed at query time; three real modes: **expression** push-down per dialect, **two-phase** cross-leg per ADR-0005, and **provisioning** (materialize the definition as the source's native semantic object and query it as a property). | M5 planner, capability registry, M13 dial |
| 3.4 | Harvest | **Yes — the near-term win.** Snowflake exports any semantic view as Ossie YAML (preview); Databricks metric views and dbt manifests convert losslessly-ish; Postgres/ClickHouse/Arango have nothing to harvest but schema. | RSA OSI connector (stop dropping metrics) |
| 3.5 | Graph algorithms | **No objects for them, and none planned.** Two footholds: recursive `derived_by` rules (reachability, not PageRank) and an `ARANGO` `custom_extensions` slot carrying a graph-metric definition whose *result* is exposed as a field. | Ontology WG proposal; M13 |
| 3.6 | Templates | Yes — but they are our existing prepared questions + few-shot corpus + goldens, generated per metric × dimension, **admission-filtered and execution-graded** before entering the corpus. | `deploy/questions.json`, `nl-corpus`, Forge |
| 3.7 | HITL | Three gates, two already named (RD-1 ontology curation, RD-3 mapping review) plus a new one: **metric certification**, which Ossie does not model — we carry it in the catalog and emit it as an extension. Consoles are ArGOS tabs. | PRD §12; Q-11 vocabulary; ArGOS |
| 3.8 | When | **Not before S2's rung-3 fold and the catalog-as-hub.** Cheap slices now (misnomer fix, RSA connector, pin, harvest spike); metric object + Ossie export at S6; push-down modes and the graph proposal in H1-2027. Ossie itself is a 0.2.0.dev0 draft in incubation — pin, don't chase. | roadmap S2/S3/S6, H1-2027 |

## 1. What OSI is today (state of the standard, 2026-09-16)

**Identity.** Open Semantic Interchange was announced by Snowflake on
2025-09-23 with Salesforce, BlackRock, dbt Labs, RelationalAI and others
(17 participants at launch) `[secondary — Snowflake press release, SiliconANGLE]`.
The repository was created 2025-11-18; the site announced "the OSI
specification is live" on 2026-04-28 (the only tag is `osi-0.1.1-rc1`). On
**2026-06-22 the project entered the Apache Incubator as "Apache Ossie"**
(champion JB Onofré; mentors Onofré, Karau, Chen, Spitzer). The README says
"Apache Ossie was formerly known as Open Semantic Interchange (OSI)"; the
rename avoided the acronym collision with the Open Source Initiative
`[secondary]`. The site lists 50+ members (Snowflake, Databricks, dbt,
Salesforce, Oracle, ServiceNow, Denodo, Starburst, Dremio, Cube, AtScale,
RelationalAI, Collibra, Alation, Atlan, DataHub, Informatica, JetBrains,
Mistral AI …). Repo: 2,140 stars, pushed the day of this reading.

**Two layers, two specs.** The expression-language proposal states it
directly: Ossie has an **ontology layer** ("maps more closely to modelling
languages like OWL, (Py)Rel from RelationalAI, and Legend from Goldman
Sachs") above a **logical layer** ("maps closely to traditional BI semantic
models").

*Logical layer — `core-spec/spec.md`, version `0.2.0.dev0`, marked DRAFT
("schema may change before 0.2.0 is released").*

| Object | Key fields | Notes |
|---|---|---|
| `semantic_model` | `name`, `description`, `ai_context`, `datasets[]`, `relationships[]`, `metrics[]`, `custom_extensions[]` | top-level container |
| `datasets[]` | `name`, `source` (opaque `db.schema.table` or query), `primary_key[]`, `unique_keys[][]`, `fields[]`, `ai_context` | one logical table |
| `fields[]` | `name`, `expression.dialects[{dialect, expression}]`, `datatype` (String…DateTimeTz, Opaque), `dimension.is_time`, `label`, `ai_context` | scalar SQL per dialect; **no column types beyond the logical enum** |
| `metrics[]` | `name`, `expression.dialects[]`, `datatype`, `description`, `ai_context` | aggregate SQL per dialect; **no grain, no filters, no dataset reference** (all open in the metrics working group) |
| `relationships[]` | `name`, `from`, `to`, `from_columns[]`, `to_columns[]` | **implicitly many-to-one**, `to` side is the keyed side |
| `ai_context` | string, or `{instructions, synonyms[], examples[]}` | the LLM-facing slot |
| `custom_extensions[]` | `{vendor_name, data: <JSON string>}` | well-known vendors: `COMMON`, `SNOWFLAKE`, `SALESFORCE`, `DBT`, `DATABRICKS`, `GOODDATA`, `HONEYDEW`, `WISDOM`, `SIGMA` |

Expressions are **SQL pass-through with dialect tags** (`ANSI_SQL`,
`SNOWFLAKE`, `DATABRICKS`, `BIGQUERY`, `MDX`, `DAX`, `TABLEAU`, `MAQL`,
`SIGMA`, `THOUGHTSPOT`). The working group's *Expression Language* proposal
("Proposed Final"; leads from Snowflake with dbt, Databricks, Salesforce,
AtScale, Cube, RelationalAI, Starburst, Denodo, Malloy, ThoughtSpot,
Lightdash at the table) defines a portable subset — `Ossie_SQL_2026`, based
on ANSI SQL:2003 Core — that compliant implementations MUST support:
arithmetic/comparison/logical operators, `CASE`, `IN` lists (no subqueries),
`LIKE`, core aggregates (SUM/COUNT/MIN/MAX/AVG), statistical and percentile
aggregates, approximate aggregates (recommended), date/time and string
functions; **not** `SELECT/FROM/JOIN/GROUP BY/WHERE`, subqueries, CTEs or set
operators ("handled by the semantic layer"). It also publishes a
**decomposability table** — Distributive (SUM, COUNT, MIN, MAX), Algebraic
(AVG, STDDEV, VARIANCE), Holistic (MEDIAN, PERCENTILE, COUNT DISTINCT),
Sketch-based (APPROX_*) — which is the same partition ADR-0005 D1/D3 uses.

*Ontology layer — `ontology/ontology.md` + `ontology.json`, version
`0.2.0.dev0` dated 2026-05-29 ("Basic support for ontologies and logical
schema mappings").* An ORM-flavoured, fact-oriented model (the RelationalAI
lineage is visible; the proposer of discussion #22 is an RAI committer):

| Construct | Fields | Reads as |
|---|---|---|
| concept | `concept`, `type: EntityType \| ValueType`, `extends[]`, `identify_by[]`, `derived_by[]`, `requires[]`, `relationships[]`, **`uri`** | class / datatype; `uri` "full URI or QName resolved against the ontology-level `prefixes` map" |
| relationship | `name`, `roles[{concept, name}]`, `multiplicity: ManyToOne \| OneToOne`, `derived_by[]`, `requires[]`, `verbalizes[]` (required) | n-ary fact type; identified as `Concept.name`; dot-join navigation |
| `derived_by` | ANSI SQL expressions, **recursion allowed** (`Person.ancestor_of.parent_of(descendant)`) | derived relationship or derived concept (view/rule) |
| `requires` | expressions over roles | constraints (SHACL-shaped) |
| `ontology_mappings` → `concept_mappings[]` | `object_mappings[{expression \| referent_mappings}]`, `link_mappings[{object_mapping, relationship, children}]` | **a mapping layer from logical datasets/fields to concepts** — R2RML-shaped (object mapping ≈ subject template; link-mapping tree ≈ predicate-object maps) |

**What is *not* in Ossie (verified against the spec, not inferred).**
No RDF/OWL serialization (only the `uri`/`prefixes` hook in the ontology
schema); no per-object provenance, owner, version or certification
(discussions #13, #31, **#53 "Certified and Certifying Authority" — open,
zero replies**); no graph, document or stream sources (**#68**: maintainer
points to an ontology working group "adding graph and ontology
representation"; streams "not currently prioritizing"); no query language or
reference engine (a *future* working group); `verified_queries` not in the
core (**#82**, open — objections: they go stale, they belong to an evals
pipeline, required groupings are a property of the metric not a hint);
metric grain, filters, metric-to-dataset references and explicit cardinality
all still under discussion (#12, #18, #19, #29, #50).

**Tooling that exists.** `python/` (Pydantic v2 models — the shared
foundation of the converters), `validation/validate.py` (JSON-schema,
unique names, references, SQL syntax), `core-spec/ossie-schema.json`, a Go
CLI, and converters: **dbt** (bidirectional, `semantic_manifest.json`),
**Databricks Unity Catalog metric views** (bidirectional; import stashes
MV-only features in `custom_extensions[DATABRICKS]` so `MV → Ossie → MV` is
lossless; export drops relationships/extensions with a warning),
**Snowflake** (Ossie → Cortex Analyst YAML only — but Snowflake itself ships
the reverse, §4.4), GoodData (bi), Microsoft Power BI/Fabric TMSL (bi),
Salesforce, Polaris (export), OrionBelt, Honeydew, Omni, Sigma, NVIDIA GSF,
Wisdom, and **`converters/ontology`**: `palantir_to_ossie` plus
`ossie_to_spec`/`spec_to_ossie`.

**Adoption caveats that bind us.** The incubator `DISCLAIMER` asks adopters
to "conduct a thorough licensing review"; the spec says production
deployment of the pre-release is discouraged; issue #102 asks for semantic
versioning of the JSON schema because none exists yet. Every artifact we
read or write must carry the version string and be parsed behind a seam
under **CC-9 pin discipline** — the same posture the OKF report took.

## 2. Where the estate already touches OSI (code read, with corrections)

1. **RSA 0.8.0 has an OSI connector — as a *physical* catalog source.**
   `relational_schema_analyzer/connectors/osi.py` reads `*.osi.yaml` into a
   `PhysicalSchema`: `datasets[]` → Table (`source` → schema hint +
   `extra['osiSource']`, `description` → comment), `fields[]` → Column
   (declaration order → ordinal; **types degrade to `string`/`temporal`**
   because Ossie carries none), `primary_key`/`unique_keys` → keys,
   `relationships[]` → ForeignKey, document `generated_at`/mtime → bitemporal
   valid time. Two lines matter for this report: *"Model-level `metrics` are
   aggregate expressions, not physical columns, so they are intentionally
   ignored"*, and `ai_context` is never read. So today the estate ingests
   Ossie **minus exactly the semantic content the eight questions are
   about**. That is a correct scoping for a read-only introspector, and it
   is the seam to extend (§4.4).
2. **r2g has no OSI code.** Zero references in `r2g/src/r2g` or `r2g/docs`.
   The PRD's "`r2g` … **implements OSI**" (§9) and the module specs'
   "OSI/YAML" artifact language (M2 §3, M3 §3, M4 §2/§9, M5 §3) describe an
   intent; the shipped interchange is **CSI v1** (ADR-0001 #3), and the
   Ossie import path is RSA's connector → `catalog/adapters/rsa.py` → CSI.
   M4 **FR-6 "OSI-compliant export/import" (P2) is unstarted.**
3. **The name.** PRD §5 and the north-star principle 7 expand OSI as "open
   semantic inter*face*". It is the Open Semantic Inter*change*, now Apache
   Ossie. Worth a one-line fix in both documents when they next open.
4. **Competitors already speak it.** AWS's context-ontology-accelerator
   ships OSI import/export in its metric editor (COA report §4/§6), and the
   COA report's "interop counter-demo" (their ontology, our federation) is
   the Ossie *ontology layer* import, not the metrics layer.

## 3. Concept mapping — Ossie ↔ CDF

| Ossie | CDF artifact today | Fit |
|---|---|---|
| `semantic_model` | catalog manifest source (`deploy/catalog/manifest.json`) | direct |
| `dataset` (`source`, `primary_key`, `unique_keys`) | CSI entity + `arangoPhysicalMapping` / R2RML logical table; manifest `uniqueConstraints` | direct (RSA does this) |
| `field` (`expression`, `datatype`, `is_time`) | CSI property + physical field; `sampleValues` | direct; **Ossie has no sample values, CSI has no synonyms** |
| `relationship` (many-to-one, `to` keyed) | CSI relationship / R2RML `refObjectMap`; manifest `joinKeys` | direct — and the `to`-side key is exactly the **declared-unique one-side ADR-0005 D1 requires** |
| `metric` | **nothing** — the fabric has no metric layer (scorecard dim. 2 gap) | new object (§4.2) |
| `ai_context.{synonyms,instructions,examples}` | nothing — `SourceCatalog.vocabulary()` emits class and property *names* only | new prompt inputs (§4.1) |
| `custom_extensions[VENDOR]` | — | our slot: `ARANGO`/`CDF` extension for concept IRIs, provenance, certification, graph metrics |
| ontology `concept` (`EntityType`, `extends`, `uri`) | OWL class, `rdfs:subClassOf`, IRI under `urn:arango-sparql:concept#`; CC-12 singular PascalCase already matches (`Person`, `OrderLineItem`) | mechanical for classes |
| ontology `concept` (`ValueType`, `requires`) | datatype + SHACL constraint | mechanical |
| relationship, binary | object/datatype property (`c:accountId`) | mechanical; `verbalizes` ≈ `rdfs:label`/comment |
| relationship, n-ary; `derived_by` | reified class / SPARQL CONSTRUCT or view | partial — not lossless either way |
| `identify_by` | AER canonical key / declared join key | strong match |
| `ontology_mappings` (object/link mappings) | R2RML / CSI physical mapping | same shape, different spelling |

## 4. The eight questions

### 4.1 (3.1) How do OSI metric definitions affect NL→SPARQL?

**Where the NL front-end stands.** `src/cdf/query/nl.py` grounds the prompt
in the caller's authorized projection of the catalog — per source, per
class, the property names, plus relationship names — and validates the
model's output by running `partition_query`, repairing up to twice, then
refusing. Two facts bound what any metric layer can do:

- The prompt carries **names only** — no descriptions, synonyms or example
  questions (the vocabulary projection has none to give). Ossie's
  `ai_context` and `description` are precisely the missing inputs.
- The prompt says *"Do NOT use … aggregation (COUNT/SUM/GROUP BY) — those
  are refused by the engine"*, and the docstring repeats "E1 still refuses
  … aggregation". **The engine no longer does:** `planner.py`
  `_admit_single_leg_aggregation` (issue #14, rung 2) admits a top-level
  `GROUP BY` routed to one aggregation-capable source, and goldens g19
  (accounts per tier, Ontop) and g20 (docs per role, Arango) pin it. The NL
  corpus (`nl-corpus-v1.json`, 12 examples) has **zero** aggregation
  examples. So a question the engine can answer ("how many accounts per
  tier?") is unreachable through NL today. This gap predates OSI and is the
  first thing metrics would have to pass through.

**What metrics change — three effects.**

1. **The target set shrinks and gets named.** With certified metrics in the
   catalog, "total query volume by edition for Meridian" is *metric selection
   + dimension slicing + entity filter*, not free composition of `SUM` over
   the right property with the right grain. The LLM is asked to pick
   `c:totalQueryVolume` (defined once as `SUM(usage_metrics.query_volume_m)`
   at `UsageMetric` grain) rather than to invent it. Synonyms
   (`ai_context.synonyms: ["query volume", "queries"]`) and `examples` feed
   the same retrieval the few-shot seam already has. This is the mechanism
   behind the seed's "reduces hallucinations" claim — real, but it works
   through vocabulary and target reduction, not through OSI per se, and it
   needs the prompt to carry the fields.
2. **The IR needs a way to say "this metric".** Two options, both
   compatible with SPARQL as canonical IR (ADR-0001):
   - *Expand at generation time* — the metric definition is macro-expanded
     into a SPARQL aggregate (`SELECT ?edition (SUM(?v) AS ?total) … GROUP BY
     ?edition`). Works when the expression fits the admitted fragment and the
     shape is single-leg (rung 2) or distributive cross-leg over declared-unique
     keys (rung 3, S2). Nothing new in the engine; the template is the
     artifact (§4.6).
   - *Treat the metric as a derived property* (`c:totalQueryVolume` on a
     `c:UsageSummary` class) realized by a source-side object — a Postgres
     view or Ontop lens, a Snowflake semantic view, a Databricks metric view.
     No aggregation in the IR at all; the source computes its own governed
     definition (§4.3 mode C). This is the option that keeps NL inside the
     BGP+FILTER fragment the prompt already teaches.
3. **Refusals become more precise, not fewer.** A metric carries its
   aggregation class. Distributive/algebraic metrics are admissible cross-leg
   under ADR-0005 D1; holistic ones (percentiles, MEDIAN, COUNT DISTINCT
   across legs) are **permanently refused cross-leg** (D3). The refusal can
   now name the metric and the class ("`p95Latency` is holistic; it runs
   single-leg on Snowflake only") — better than today's "aggregation".

**Verified queries ≈ our NL corpus.** Ossie's proposed `verified_queries`
(#82) and Snowflake's shipped `verified_queries` (`name`, `question`, `sql`,
`verified_at`, `verified_by`, `use_as_onboarding_question` — "example
questions with their corresponding SQL queries … help Cortex Analyst
understand how to answer similar questions") are the same idea as
`CorpusExample` (`question`, `aliases`, `sparql`, `expected_sources`,
`refusal`). The #82 objections — staleness, "belongs in an evals pipeline",
"required groupings are a property of the metric" — are all things
`cdf.eval` already handles (execution grading, versioned corpus, refusal
cases). Interop is an exporter, not a redesign.

**Seed check.** "Drastically improves … reducing hallucinations and
ambiguous mapping" — directionally right, unevidenced, and conditional on
(a) and (b) above. The honest measure is the NL eval (M10) before and after
enrichment, on a corpus that finally includes aggregation questions.

### 4.2 (3.2) How do OSI definitions enter CDF — does it extend the ontology?

**Answer: three Ossie artifacts land in three CDF places; only one of them
is "the ontology".**

| Ossie artifact | Enters CDF as | Why here |
|---|---|---|
| **Ontology layer** (`concept`, `relationships`, `extends`, `requires`, `uri`) | the **conceptual model** — OWL/CSI `conceptualModel`, classes and properties under the concept base, CC-12 names | it *is* an ontology; `uri`/`prefixes` is the interop hook; import ≈ another extraction source for M2/M3 (review-gated, §4.7) |
| **Logical layer** (`datasets`, `fields`, `relationships`, `primary_key`) | **CSI physical mapping + R2RML + manifest `joinKeys`/`uniqueConstraints`** | RSA's connector already does this; nothing conceptual about a `db.schema.table` binding |
| **Metrics** (`metrics[]`, per-dialect expressions, `ai_context`) | a **new catalog object class `Metric`** in the M11 catalog graph — not an OWL axiom | OWL cannot express aggregation semantics; SHACL-AF/SPARQL rules could, but would not round-trip to Ossie; the catalog is where the planner already reads capabilities, join keys and statistics |

Proposed shape of the new object (manifest `metrics[]` + CSI extension,
serialized 1:1 to Ossie `metrics[]` for FR-6):

```yaml
metrics:
  - name: totalQueryVolume            # CC-12 lowerCamel; Ossie name on export: total_query_volume
    concept: c:UsageMetric            # grain — the class whose instances are summed
    dimensions: [c:edition, c:period] # allowed GROUP BY properties
    aggregationClass: distributive    # distributive | algebraic | holistic | sketch (ADR-0005 D1/D3)
    expression:
      dialects:
        - dialect: Ossie_SQL_2026
          expression: SUM(usage_metrics.query_volume_m)
        - dialect: SNOWFLAKE
          expression: SUM(USAGE_METRICS.QUERY_VOLUME_M)
    aiContext:
      synonyms: [query volume, total queries]
      examples: ["total query volume by edition last quarter"]
    certification:                    # ours — Ossie has no slot (§4.7); emitted as custom_extensions[CDF]
      certified: true
      certifiedBy: steward@…
      certifiedAt: 2026-09-16
    provenance: {harvestedFrom: snowflake:telemetry, ossieVersion: 0.1.1, importedAt: …}
```

Two notes. First, until the metrics working group lands grain and dataset
references (#12, #18), the `concept`/`dimensions` binding travels in
`custom_extensions[CDF]` on export — lossless for us, ignorable for others.
Second, the ontology-layer import is the **COA counter-demo** path: a
Palantir or COA ontology exported as Ossie enters M2/M3 as a source ontology
with provenance, is curated (RD-1), and is then federated by us.

**Seed check.** "OSI acts as an overlay or an extension to your base
ontology" — half right for the logical and metric layers, and misses that
Ossie now carries its own ontology layer that maps onto ours rather than
extending it.

### 4.3 (3.3) How does the federator push definitions down into sources?

The seed is right that nothing is "pushed into" a source at query time. What
actually happens splits into three modes, and CDF already has the machinery
for two of them.

**Mode A — expression push-down (per leg, per dialect).** The metric's
`expression.dialects` gives the leg its text: `SnowflakeExecutor` takes
`SNOWFLAKE`, `ClickHouseExecutor` and Ontop take `ANSI_SQL` (or a
`Ossie_SQL_2026` string compiled to the dialect), and the Arango leg has
**no Ossie dialect** — `arango-sparql-py` already translates SPARQL
aggregates to AQL for the single-leg case, so the portable subset →
SPARQL → AQL path is the one to use; there is no `AQL` dialect tag to
propose upstream until the ontology working group's non-tabular work
exists. The capability registry gains two declarations, probe-verified per
CC-14: `expressionDialects: [SNOWFLAKE, ANSI_SQL]` and `semanticObjects:
{kind: snowflake-semantic-view | databricks-metric-view | view | none}`.

**Mode B — two-phase cross-leg (ADR-0005 D1, roadmap S2).** A metric's
`aggregationClass` decides admissibility: distributive/algebraic partials
per leg, keyed by (grouping keys ∪ cross-source join keys), folded in the
federator; holistic refused by name. Ossie's relationships are many-to-one
with the `to` side keyed, so **importing Ossie relationships populates the
declared-unique one-side that D1 requires** — the Ossie import is
retroactively a prerequisite of rung 3, the same way the CRM overlay was.

**Mode C — provisioning the definition into the source.** "Write once,
query anywhere" in the strong sense: emit the metric as the source's own
governed object and query it as a plain property. Snowflake:
`SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML` (preview) creates a semantic
view from our export; Databricks: `ossie-databricks export` → metric view;
Postgres: a view or Ontop lens; Arango: a curated AQL view or materialized
attribute. The leg then runs the source's certified definition, the source
is the certifying executor, and the envelope cites the object. This is a
**delivery-mode decision about a definition** (federate the expression /
virtualize a view / materialize a table) — exactly the M13 dial, applied to
metrics instead of concepts — and it is the only mode where the phrase
"push the definitions down" is literally true.

**Seed check.** "Translates the OSI-enriched SPARQL query into the native
dialect using a mapping layer (e.g. R2RML), pushing down the execution" —
correct for mode A on a single leg; silent on cross-leg correctness (mode
B is where federations go wrong) and on provisioning (mode C is where the
standard pays off).

### 4.4 (3.4) Can we harvest definitions from the source systems?

**Yes, and from the three sources that matter most it is one call or one
converter away.**

| Source | Harvest path | Fidelity (primary docs) |
|---|---|---|
| **Snowflake** | `SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW('<db.schema.view>')` — Preview, all accounts, needs SELECT or USAGE on the view; returns Ossie YAML `version: 0.1.1` | non-EQUI relationships and field labels "silently dropped"; table-level metrics and filters → dataset `custom_extensions`; only the `SNOWFLAKE` dialect is returned ("stores a single resolved expression internally") |
| **Databricks** | `ossie-databricks import -i view.yaml` (Unity Catalog metric view v1.1 → Ossie) | MV-only features (`filter`, `window`, `format`, `rely`) preserved in `custom_extensions[DATABRICKS]`; `MV → Ossie → MV` lossless |
| **dbt** | `msi-to-ossie` on `semantic_manifest.json` | lossy in named ways, each recorded as a `ConverterIssue` |
| Power BI / Fabric, GoodData, Palantir | bidirectional TMSL / LDM converters; `palantir_to_ossie` | n/a to our current sources; relevant to the COA/Palantir counter-demo |
| **Postgres, ClickHouse, ArangoDB** | nothing to harvest — definitions live in views, queries and AQL | RSA/ASA schema introspection is the harvest; metrics are curator-authored |

Pipeline, all on existing seams: harvest → Ossie YAML (version pinned,
validated with `validate.py`) → **RSA OSI connector extended to keep
`metrics[]` and `ai_context`** (today dropped; upstream issue in the WS-A
hardening lane) → CSI + `metrics[]` → catalog admission with review (§4.7)
→ manifest. The demo estate already has the right test bed: the Snowflake
`TELEMETRY` schema with `USAGE_METRICS` is where a semantic view would be
authored, exported and diffed against the r2g-produced CSI.

**Seed check.** "Baseline metadata (schemas, keys, existing view logic)"
undersells it: Snowflake, Databricks and dbt emit the **business
definitions themselves** in Ossie form. The seed is right that they still
need human refinement (§4.7).

### 4.5 (3.5) Are there definitions for graph algorithms?

**No — and no working group is heading there.** The core is tabular by
construction (datasets, columns, equi-joins); the spec has no algorithm,
procedure or graph objects; discussion #68's answer was an ontology working
group "adding graph and ontology representation", and the ontology spec
that followed models *concepts and fact types*, not traversals. Issue #107's
`ontology-query` (Datalog reachability) reasons over the **model** — which
datasets can join — not over data.

Two footholds exist, both worth using:

1. **Recursive `derived_by`.** The ontology spec's `Person.ancestor_of`
   example is transitive closure over a relationship. That covers the
   *traversal* class — reachability, k-hop neighbourhoods, path existence
   — as a derived relationship with portable semantics. It does **not**
   cover iterative numeric algorithms: PageRank, community detection,
   centralities, similarity — anything with convergence, damping or a
   partition result has no expression in either layer.
2. **`custom_extensions[ARANGO]` + a result field.** A graph metric can be
   *declared* as a vendor extension — algorithm, parameters, named graph /
   edge collections, output property — and its **result exposed as an
   ordinary Ossie field** (`pagerank`, datatype `Float`) on the vertex
   dataset, with ordinary metrics over it (`AVG(pagerank)`, `MAX(…)`).
   Other tools ignore the extension and still see the field. This is §4.3
   mode C applied to graph analytics: the definition travels, the execution
   (Pregel / GAE / AQL traversal) happens in Arango, the materialized
   attribute is what federates. It is also exactly the object the M13 dial
   would allocate between virtual (compute on demand) and materialized.

**Positioning.** The ontology working group asked, in #68, for the
non-tabular use cases. A concrete `ARANGO` extension shape plus the
"derived relationships as traversals" reading is a standards contribution
Arango is uniquely placed to make — and cheaper than lobbying for graph
objects in the core.

**Seed check.** Correct that graph algorithms fall outside OSI; the
"custom extensions in your ontology to trigger virtual graph procedures" is
the right instinct, made concrete above.

### 4.6 (3.6) Should we automatically create query templates for the definitions?

**Yes — with three constraints the seed omits.** "Template" already means
three artifacts here: prepared questions (`deploy/questions.json`, exact
NL→SPARQL routes), few-shot examples (`nl-corpus-v1.json`,
`CorpusExample`), and goldens (`deploy/golden/g*.json`, execution-graded).
Ossie `ai_context.examples` and (proposed) `verified_queries` are the same
artifact class. Generation per metric × dimension set ("total query volume
by edition", "… by period", "… for account X") is mechanical.

1. **Admission-filter at generation time.** Run every generated SPARQL
   through `partition_query` against the manifest; emit only what the
   planner admits today (single-leg, rung 2) and label the rest as
   *refusal* examples with the expected reason — the corpus already models
   refusals as first-class examples, and the refusal templates are what
   keep the LLM from guessing.
2. **Execution-grade before admission to the corpus.** A template enters
   `nl-corpus` only with a computed expected answer; the Forge (M15) can
   supply these for generated shapes, the goldens for the demo estate. This
   is the SE lane's sign-off, per the roadmap's division of labour.
3. **Guidance, never routing.** Keep the corpus rule: lexical similarity
   selects prompt examples and "can never select executable SPARQL". A
   harvested `verified_queries` bank is retrieved as guidance, not run.

The staleness objection in #82 is solved by what FR-7 already stamps:
templates are bound to the mapping/CSI version they were graded against
and re-graded on regeneration; a template whose grading fails is retired,
not silently kept.

### 4.7 (3.7) Are there HITL implications?

**Yes — three gates, two already on the readiness ladder, one new.**

| Gate | What is reviewed | Where it already lives |
|---|---|---|
| **RD-1 ontology curation** | an imported Ossie *ontology* (concepts, relationships, `requires`) is one more source ontology entering M2/M3 — review-modify-approve before catalog admission | PRD §12 RD-1; AOE curation via ArGOS tabs |
| **RD-3 mapping review** | imported Ossie *datasets/fields/relationships* are mappings like r2g's — reviewable, curator edits surviving regeneration | PRD §12 RD-3; the r2g exclusion/comment-preservation debt is the blocker |
| **Metric certification (new)** | who certified `totalQueryVolume`, when, at which grain, against which schema version; which dimensions are allowed; whether it may run cross-leg | nowhere yet — **Ossie has no certification fields** (#53, open, zero replies), so CDF carries `certified/certifiedBy/certifiedAt/steward` in the catalog (Q-11 policy vocabulary) and emits them in `custom_extensions[CDF]` |

Three consequences. First, the SOTA scorecard's leadership rung for
semantic depth literally reads "certified metrics reproduce identically
through SQL, SPARQL, NL, API, and agent interfaces" — certification plus an
interface-parity golden per certified metric is the evidence, and
`interface-parity-v1.json` is the corpus to grow. Second, the steward is a
persona the product PRD already has (Q1 "the steward and the asker"); the
approval surface is an **ArGOS tab against a CDF contract** (metric object +
certification state + review events), per the M14 re-scope — CDF does not
grow a console. Third, harvested definitions arrive *already certified by
someone else's governance* (a Snowflake semantic view is a governed
object); the catalog should record that as provenance, not re-certify
blindly — "certified by source X's owner" is a legitimate, distinct state.

**Seed check.** Right and generic; the gates above are the specific ones.

### 4.8 (3.8) When should we schedule it?

**Not as a block; as slices behind their prerequisites.** Three things must
exist first: the capability registry (S1, ADR-0005 D4 — refusals name
capabilities, which is how metric dialects and semantic objects get
declared), the rung-3 fold (S2 — without it every cross-leg metric is a
refusal), and the catalog as hub (M11 — the `Metric` object needs a home
the planner reads). Ossie itself argues for patience: 0.2.0.dev0 draft,
incubating since June, grain/filters/cardinality/certification all open.
Pin what we consume; do not build on fields still being renamed.

| When | Slice | Size | Owner (roadmap lanes) |
|---|---|---|---|
| **now / S2** | Fix "open semantic interface" → "Open Semantic Interchange (Apache Ossie)" in PRD §5 and north-star principle 7 | minutes | SA |
| now / S2 | **RSA upstream issue + fix:** OSI connector keeps `metrics[]` and `ai_context` (pass-through fields, no interpretation); pin the `ossie` Python package / schema version under CC-9 | ½–1 day | SA (WS-A lane) |
| now / S2 | **Harvest spike (evidence, not product):** author one semantic view over `TELEMETRY.USAGE_METRICS`, export with `SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW`, run through RSA, diff against `deploy/csi/snowflake-telemetry.json`; record the loss table | ½ day + Snowflake account | SA / intern |
| **S3–S4** | **NL enrichment, OSI-independent:** carry descriptions / sample values / synonyms into `build_system_prompt`; lift the aggregation prohibition to match rung 2 and add aggregation examples to the corpus; measure with `nl_eval` before/after | 2–3 days | SE (NL lane) |
| **S6** (consolidation, "contracts versioned for ArGOS") | **Catalog `Metric` object + certification fields** as a versioned contract; **Ossie export** of manifest + CSI + metrics (M4 FR-6, the OKF report's "Lane C" move played toward the Ossie ecosystem) | 3–4 days | SA; SE reviews |
| **H1-2027** (P4/P5) | Push-down modes A/B/C; provisioning via Snowflake / Databricks semantic objects; the `ARANGO` graph-metric extension proposal to the ontology working group; template generation over Forge shapes | sprint-sized items, sequenced with M12/M13 | SA + SE |
| **quarterly** | Standards watch with OKF and COA: 0.2.0 release, metrics-at-ontology-layer, #82 `verified_queries`, #53 certification, any `AQL`/graph dialect motion | ½ day | rotating |

**Seed check.** "After your core connector framework and base ontology
extraction modules are stable" — agreed, and now anchored to named gates.

## 5. Corrections to the starter note

| Gemini note said | What the primary sources show |
|---|---|
| OSI (unqualified) | The project entered the Apache Incubator on 2026-06-22 and is now **Apache Ossie**; the spec, converters and Snowflake functions all use the new name |
| "Explicit metric definitions drastically improve NL→SPARQL … reducing hallucinations" | Plausible, unmeasured; conditional on the prompt carrying `ai_context`/descriptions (it carries names only) and on the engine admitting the shape (the NL prompt still forbids aggregation the engine runs single-leg) |
| "OSI acts as an overlay or an extension to your base ontology" | Ossie has **its own ontology layer** (0.2.0.dev0, ORM-style, `uri`/`prefixes` hook) that maps *onto* our conceptual model; its metrics are a catalog object, not an ontology extension |
| "The federator … translates the OSI-enriched SPARQL query into the native dialect using R2RML" | True for single-leg expression push-down; omits cross-leg correctness (ADR-0005) and provisioning of definitions as source-native semantic objects — the mode where "write once, query anywhere" is literal |
| "Harvest baseline metadata (schemas, keys, existing view logic)" | Snowflake (`SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW`, preview), Databricks (`ossie-databricks import`) and dbt (`msi-to-ossie`) emit the **business definitions themselves** as Ossie YAML |
| "OSI is primarily designed around BI analytics, metrics, and dimensions … graph algorithms fall outside" | Correct; add that the ontology layer's recursive `derived_by` covers traversal/reachability, and `custom_extensions` is the sanctioned slot for a vendor graph-metric definition |
| "Automatically generating SPARQL templates … is highly recommended" | Agreed, with admission-filtering, execution grading and guidance-not-routing as the conditions |
| "Data stewards validate harvested mappings, approve metric logic" | Agreed; the missing piece is that **Ossie has no certification model** (#53 open, no replies) — CDF carries it |
| "Schedule after connector framework and base ontology extraction are stable" | Agreed; concretely after capability registry (S1), rung-3 fold (S2) and catalog-as-hub (M11) |

## 6. Recommendations

1. **Do not build an "OSI layer".** Build three small things in the places
   they belong: keep metrics/`ai_context` in RSA's connector (harvest),
   a `Metric` object in the catalog with certification (governance), and
   an Ossie exporter over manifest + CSI + metrics (FR-6 interop). Each is
   independently useful; none depends on Ossie stabilizing.
2. **Fix the NL front-end's aggregation gap first, independent of OSI.**
   The prompt forbids what the engine admits; the corpus has no aggregation
   examples. This is a one-sprint SE item with a measurable eval delta and
   it is the precondition for any metric reaching an answer.
3. **Run the Snowflake harvest spike as the evidence base** before any
   design commitment — one semantic view, one export, one diff, one loss
   table — and attach it to this report.
4. **Adopt ADR-0005's aggregation classes as the metric's admissibility
   field** (`aggregationClass`), mirroring Ossie's decomposability table.
   One vocabulary for the planner, the exporter and the refusal text.
5. **Take the graph question to the ontology working group** as a written
   `ARANGO` extension shape plus the traversal-as-derived-relationship
   reading. It costs a document and buys standards presence for the graph
   case nobody else at that table represents.
6. **Record the naming and the code reality in the PRD:** "Open Semantic
   Interchange (Apache Ossie)" in §5 and the north star; "r2g implements
   OSI" → "RSA reads OSI logical models; FR-6 export unstarted".
7. **Pin and watch.** Version-pin the `ossie` package and schema (CC-9);
   put Ossie on the quarterly standards watch with OKF and COA.

## 7. Open questions for review

- **Metric grain vs. our class model.** Ossie metrics have no grain today;
  the proposal above binds a metric to a CSI class. Is a metric always at
  the grain of one class, or do we need metric-over-join (which rung 3
  admits only over declared-unique keys)?
- **Where certification lives.** Catalog manifest (file, CI-diffable) or
  AOE's belief store (temporal, already has review states)? The Q-11
  policy-vocabulary decision with ArGOS is the natural place to settle it.
- **Do we want an `AQL` dialect tag upstream?** Cheap to propose, but it
  would invite AQL pass-through in expressions — the opposite of the
  portable subset we would rather compile to.
- **Harvest trust.** Is "certified by the source's governance" a
  certification state we accept as-is at admission, or always a proposal
  pending our steward's approval?

## Sources

- https://open-semantic-interchange.org/ (primary — object model summary, members, working groups, "specification is live" 2026-04-28)
- https://github.com/apache/ossie — README (formerly OSI), `ROADMAP.md`, `DISCLAIMER`, `.asf.yaml` (primary)
- https://github.com/apache/ossie/blob/main/core-spec/spec.md — core spec 0.2.0.dev0 (primary)
- https://github.com/apache/ossie/blob/main/core-spec/expression_language.md — `Ossie_SQL_2026` proposal, decomposability table (primary)
- https://github.com/apache/ossie/blob/main/ontology/ontology.md and `ontology/ontology.json` — ontology spec 0.2.0.dev0, 2026-05-29 (primary)
- https://github.com/apache/ossie/blob/main/converters/README.md and `converters/{snowflake,databricks,dbt,ontology}/README.md` (primary — directions and fidelity)
- https://incubator.apache.org/projects/ossie.html — incubation 2026-06-22, champion, mentors (primary)
- https://github.com/apache/ossie/discussions/22 (relational ontologies proposal), /101 (support for ontologies), /68 (non-tabular data models), /82 (`verified_queries`), /53 (certified metrics); https://github.com/apache/ossie/issues/107 (`ontology-query`) (primary — discussion state as read 2026-09-16)
- https://docs.snowflake.com/en/sql-reference/functions/system_read_ossie_yaml_from_semantic_view — preview, version 0.1.1, drop list (primary)
- https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec — `verified_queries` fields (primary)
- https://www.snowflake.com/en/news/press-releases/snowflake-salesforce-dbt-labs-and-more-revolutionize-data-readiness-for-ai-with-open-semantic-interchange-initiative/ (primary — 2025-09-23 launch); SiliconANGLE 2025-09-23 coverage `[secondary]`; Datus blog on the rename `[secondary]`
- Estate code read 2026-09-16: `relational_schema_analyzer/connectors/osi.py` (RSA 0.8.0, from the CDF venv); `src/cdf/query/nl.py`, `src/cdf/query/planner.py`, `src/cdf/catalog/capabilities.py`, `src/cdf/eval/nl_corpus.py`, `src/cdf/eval/corpora/nl-corpus-v1.json`, `deploy/golden/g16,g19,g20`, `deploy/csi/snowflake-telemetry.json`; `r2g/src`, `r2g/docs` (no OSI references)

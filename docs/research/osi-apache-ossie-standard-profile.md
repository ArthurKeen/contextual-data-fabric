---
title: "Apache Ossie (formerly Open Semantic Interchange) — standard profile as of 2026-09-16"
type:
  - internal
  - research
  - standards-profile
date: 2026-09-16
version: "0.1 — split out of the integration paper's §2 (same day)"
status: "draft — for review (PJ, Arthur; reviewer: Kevin)"
related:
  - "docs/research/osi-apache-ossie-integration.md (the synthesis this profile supports; its §2 summarizes this document)"
  - "docs/research/okf-open-knowledge-format-integration.md (sibling standards analysis)"
---

# Apache Ossie — standard profile

> **Scope.** The state of the Open Semantic Interchange standard, now Apache
> Ossie, as read from primary sources on 2026-09-16: identity and
> governance, the two specification layers, what the spec deliberately does
> not contain, the tooling that exists, and the adoption caveats that bind
> us. The companion paper
> [osi-apache-ossie-integration.md](osi-apache-ossie-integration.md) draws
> the conclusions; this document holds the evidence so the synthesis stays
> under the documentation size cap. Re-verify quarterly (see the watch list
> at the end).

**Identity.** Open Semantic Interchange was announced by Snowflake on
2025-09-23 with Salesforce, BlackRock, dbt Labs, RelationalAI and others
(17 participants at launch) `[secondary — Snowflake press release, SiliconANGLE]`.
The repository was created 2025-11-18; the site announced "the OSI
specification is live" on 2026-04-28 (the only tag is `osi-0.1.1-rc1`). On
**2026-06-22 the project entered the Apache Incubator as "Apache Ossie"**
(champion JB Onofré; mentors Onofré, Karau, Chen, Spitzer). The README says
"Apache Ossie was formerly known as Open Semantic Interchange (OSI)"; the
rename avoided the acronym collision with the Open Source Initiative
`[secondary]`. The site lists 50+ members — warehouses (Snowflake, Databricks,
Oracle, Firebolt), semantic layers and BI (dbt, Cube, AtScale, Salesforce,
ThoughtSpot, Sigma, GoodData, Omni, Hex, Metabase, Qlik), catalogs (Collibra,
Alation, Atlan, DataHub, Informatica, Select Star), **federation vendors
(Denodo, Starburst, Dremio)**, and RelationalAI. Repo: 2,140 stars, pushed
the day of this reading. Arango is not a member.

**Two layers, two specs.** The expression-language proposal states it
directly: Ossie has an **ontology layer** ("maps more closely to modelling
languages like OWL, (Py)Rel from RelationalAI, and Legend from Goldman
Sachs") above a **logical layer** ("maps closely to traditional BI semantic
models").

*Logical layer — `core-spec/spec.md`, version `0.2.0.dev0`, marked DRAFT
("schema may change before 0.2.0 is released").*

| Object | Key fields | Notes |
|---|---|---|
| `semantic_model` | `name`, `description`, `ai_context`, `datasets[]`, `relationships[]`, `metrics[]`, `custom_extensions[]` | top-level container; **no first-class filters** (discussion #5) |
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

**What is *not* in Ossie (verified against the spec and schema, not
inferred).** No RDF/OWL serialization (only the `uri`/`prefixes` hook in the
ontology schema); no per-object provenance, owner, version or certification
(discussions #13, #31, **#53 "Certified and Certifying Authority" — open,
zero replies**); no entitlement or PII markers (#55, #58, #59, #87 open); no
graph, document or stream sources (**#68**: maintainer points to an ontology
working group "adding graph and ontology representation"; streams "not
currently prioritizing"); no query language or reference engine (a *future*
working group); `verified_queries` not in the core (**#82**, open —
objections: they go stale, they belong to an evals pipeline, required
groupings are a property of the metric not a hint); metric grain, filters,
metric-to-dataset references and explicit cardinality all still under
discussion (#5, #12, #18, #19, #29, #50).

**Tooling that exists.** `python/` (Pydantic v2 models — the shared
foundation of the converters), `validation/validate.py` (JSON-schema,
unique names, references, SQL syntax), `core-spec/ossie-schema.json`, a Go
CLI, and converters: **dbt** (bidirectional, `semantic_manifest.json`),
**Databricks Unity Catalog metric views** (bidirectional; import stashes
MV-only features in `custom_extensions[DATABRICKS]` so `MV → Ossie → MV` is
lossless; export drops relationships/extensions with a warning),
**Snowflake** (Ossie → Cortex Analyst YAML only — but Snowflake itself ships
the reverse, §5.4), GoodData (bi), Microsoft Power BI/Fabric TMSL (bi),
Salesforce, Polaris (export), OrionBelt, Honeydew, Omni, Sigma, NVIDIA GSF,
Wisdom, and **`converters/ontology`**: `palantir_to_ossie` plus converters
between the core model and the ontology-spec YAML.

**Adoption caveats that bind us.** The incubator `DISCLAIMER` asks adopters
to "conduct a thorough licensing review"; the spec says production
deployment of the pre-release is discouraged; issue #102 asks for semantic
versioning of the JSON schema because none exists yet. Every artifact we
read or write must carry the version string and be parsed behind a seam
under **CC-9 pin discipline** — the same posture the OKF report took.

## Quarterly watch list

Re-check each cycle, alongside OKF and COA: the 0.2.0 release and any
schema versioning (issue #102); metrics at the ontology layer and grain /
dataset references (#12, #18, #29); `verified_queries` (#82); certification
and governance hooks (#13, #53, #87); semantic filters (#5); explicit
cardinality (#50); any `AQL` or graph-dialect motion in the ontology working
group (#68); Snowflake's supported Ossie version in
`SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW` (0.1.1 at this reading).

## Sources

- https://open-semantic-interchange.org/ (primary — object model summary, members, working groups, "specification is live" 2026-04-28)
- https://github.com/apache/ossie — README (formerly OSI), `ROADMAP.md`, `DISCLAIMER`, `.asf.yaml` (primary)
- https://github.com/apache/ossie/blob/main/core-spec/spec.md and `core-spec/ossie-schema.json` — core spec 0.2.0.dev0, `$defs` (primary)
- https://github.com/apache/ossie/blob/main/core-spec/expression_language.md — `Ossie_SQL_2026` proposal, decomposability table (primary)
- https://github.com/apache/ossie/blob/main/ontology/ontology.md and `ontology/ontology.json` — ontology spec 0.2.0.dev0, 2026-05-29 (primary)
- https://github.com/apache/ossie/blob/main/converters/README.md and `converters/{snowflake,databricks,dbt,ontology}/README.md` (primary — directions and fidelity)
- https://incubator.apache.org/projects/ossie.html — incubation 2026-06-22, champion, mentors (primary)
- https://github.com/apache/ossie/discussions/22, /101, /68, /82, /53, /5; https://github.com/apache/ossie/issues/107, /102 (primary — discussion state as read 2026-09-16)
- https://docs.snowflake.com/en/sql-reference/functions/system_read_ossie_yaml_from_semantic_view (primary — preview, version 0.1.1, drop list)
- https://www.snowflake.com/en/news/press-releases/snowflake-salesforce-dbt-labs-and-more-revolutionize-data-readiness-for-ai-with-open-semantic-interchange-initiative/ (primary — 2025-09-23 launch); SiliconANGLE 2025-09-23 coverage `[secondary]`; Datus blog on the rename `[secondary]`

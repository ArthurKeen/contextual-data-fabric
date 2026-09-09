---
title: "PuppyGraph — authentication, access control, schema extraction and source permissions"
type:
  - internal
  - research
  - competitive-analysis
date: 2026-09-09
status: draft — for team review
related:
  - "docs/research/vendor-auth-access-control-survey.md (the synthesis this feeds)"
  - "docs/research/graph-virtualization-vendor-security-profiles.md (Stardog, Timbr, RelationalAI, Ontop/GraphDB, Neo4j, Neptune, TigerGraph, Hasura)"
  - "docs/research/sql-federation-vendor-security-profiles.md"
  - "docs/research/data-source-identity-mechanisms.md"
  - "docs/architecture/access-control-research.md"
  - "docs/architecture/module-05-federated-query-engine/adr/ADR-0004-identity-planes-and-policy-enforcement.md"
---

# PuppyGraph: how it authenticates, what it is granted, how it reads schema

> **The ask (Arthur, 2026-09-09).** How does PuppyGraph handle authentication
> and access control when it virtualizes Snowflake, Databricks and similar
> sources? How does it extract schema, what permissions is it granted, can we
> get its user manuals, and who are its competitors?
>
> **Method.** Every fact below was read on 2026-09-09 from docs.puppygraph.com
> (current 1.x docs, releases 1.7.0 through 1.9.0 dated Aug 21 to Sep 3 2026),
> puppygraph.com (pricing, MSA, security page, blog), github.com/puppygraph,
> and the AWS Marketplace listing. Quotes are verbatim. "Not documented" means
> the public docs were checked and say nothing. Several docs URLs that search
> engines index returned 404 at fetch time and are listed at the end; claims
> that depend on them are marked unverified. The Gemini starter note was used
> only as a map of what to check; its claims are corrected in section 9.

---

## 1. Summary

- **PuppyGraph is a zero-ETL graph query engine.** Gremlin and openCypher
  queries are planned against a graph schema that maps tables to nodes and
  edges, then executed by translating "logical operations into SQL for
  relational databases, direct reads from lakehouse files like Iceberg, or API
  requests for external systems". A connection to a source is a **catalog**.
  One deployment serves one graph.
- **Outbound identity is one service credential per catalog**, stored inside
  the catalog definition (Web UI form or the `catalog[]` block of the schema
  JSON). No secret-manager integration or `${ENV}` substitution for source
  credentials is documented; key files and keytabs are volume-mounted into the
  container and referenced by path.
- **End-user identity reaches a source in exactly one case: Snowflake "query
  impersonation".** PuppyGraph does not pass the user's token. It keeps the
  service-account connection, calls a customer-installed stored procedure that
  sets a Snowflake session variable to the SSO username, and Snowflake **row
  access policies** written by the customer read that variable. Everything
  else runs as the service account, with PuppyGraph's own row-level security
  filtering results inside the engine.
- **Permissions required are read-only and stated loosely.** Snowflake: query
  the referenced tables, use the warehouse, call the proxy-user procedure. No
  `GRANT` statements are given. Databricks: enable "External data access" on
  the metastore and grant `EXTERNAL USE SCHEMA` on each schema so Unity
  Catalog **credential vending** can hand PuppyGraph short-lived storage
  credentials to read Delta and Iceberg files directly. BigQuery: the three
  roles BigQuery Data Viewer, Job User and Read Session User. AWS: exact IAM
  action lists for S3 and Glue. For most JDBC sources (Postgres, MySQL,
  Oracle, ClickHouse, Redshift, Vertica) required privileges are not documented.
- **Schema is hand-built, not inferred.** The Schema Builder introspects the
  catalog into a database, table and column tree and the user picks ID columns
  and from/to keys; foreign keys are not inferred. An optional AI Assistant
  (Anthropic or OpenAI) samples real rows and probes candidate joins to
  propose a model, sending catalog metadata and sampled rows to the LLM.
- **Inbound:** default admin `puppygraph` / `puppygraph123`; OIDC SSO with
  IdP group mapping (Enterprise only); five fixed RBAC roles enforced on REST,
  Bolt and Gremlin; service accounts with header-based impersonation; an
  entitlement-table row-level security feature; no column or property masking;
  TLS only via an nginx front.
- **Manuals:** entirely public at docs.puppygraph.com (354 sitemap URLs). No
  PDF or offline build exists.
- **Editions:** Developer is "Forever free" (single node, two simultaneous
  data sources, no SSO, no monitoring). Enterprise is priced on server CPU and
  memory, with a 30-day trial. AWS Marketplace hourly prices run from $0.90 to
  $7.20 per hour depending on instance.

---

## 2. What PuppyGraph is and how it executes

| Aspect | Fact | Source |
|---|---|---|
| Product | "zero-ETL graph query engine", "distributed", "vectorized", "no data duplication" | puppygraph.com |
| Query languages | Gremlin (port 8182) and openCypher over Bolt (port 7687); Web UI and REST on 8081 | docs: launching in Docker |
| Execution | Source connectors "translate logical operations into SQL for relational databases, direct reads from lakehouse files like Iceberg, or API requests for external systems"; the engine "incrementally combines these results"; planner does "join ordering, traversal pruning, and predicate pushdown" | puppygraph.com/blog/virtual-graph |
| Topology | Control plane (registry, Web UI, gRPC), leaders (coordinate), compute nodes (execute) | docs: cluster deployment |
| Graphs per instance | One active schema, replaced via `POST /schema` | docs: managing the graph |
| Caching | Per-catalog JDBC metadata cache with TTL; a JDBC data cache with optional local-disk tier (1.8.0); `localTable[]` that "caches rows from one or more catalog tables onto PuppyGraph's compute nodes" | docs: data sources; releases |
| Requirements | 8 GB RAM for testing; production "Minimum 16 vCPUs", "Minimum 64 GB of RAM", AVX2 | docs: installation |

The execution model matters for access control: because reads are either
pushed-down SQL or direct file reads under one credential, any per-user
scoping has to happen either at the source keyed off something PuppyGraph
injects (the Snowflake session variable) or inside PuppyGraph after the read
(its own row-level security).

---

## 3. Outbound authentication, source by source

### 3.1 Snowflake

Documented on the Snowflake connector page and two tutorials.

| Method | How configured | Notes |
|---|---|---|
| Key-pair | `Snowflake Auth Type: key-pair`; RSA key mounted into the container; JDBC `private_key_file=/keys/rsa_key.p8&private_key_file_pwd=…` | The tutorial path. `password` left empty in the catalog block |
| OAuth 2.0 client credentials | `authenticator=oauth_client_credentials&oauthClientId=…&oauthClientSecret=…&oauthTokenEndUrl=…`, optional scope | Release notes call it "Snowflake External OAuth authentication", added 1.0.0. This is a machine-to-machine grant, not user delegation |
| Raw JDBC string | `Auth Type: JDBC authentication` | Any Snowflake JDBC parameter |
| Username and password | Not a first-class mode | Works through the JDBC string only |

**Privileges.** The docs state three requirements without SQL: "The service
account must be able to query the Snowflake tables referenced by the graph
schema", "must be able to use the configured warehouse", and, when
impersonation is used, "must be able to call the proxy-user stored procedure".
The tutorial's setup SQL contains only `CREATE` and `INSERT` statements for
sample data, no `CREATE ROLE` or `GRANT`. Read this as: `USAGE` on warehouse,
database and schema plus `SELECT` on the mapped tables, but PuppyGraph does not
spell it out.

**Query impersonation (the one delegation path).** "Use Snowflake query
impersonation when the same PuppyGraph graph should return different Snowflake
rows for different SSO users." Requires SSO (`SSO_ENABLED`,
`SSO_CLAIM_AS_USER_ID`, for example `preferred_username`) and a
`queryImpersonation.proxyUserProcedure` entry in the catalog pointing at a
customer-installed JavaScript procedure such as
`PUPPYGRAPH_SECURITY.UTIL.SET_PROXY_USER`, declared `EXECUTE AS CALLER`, which
runs `SET PG_PROXY_USER = ?`. The customer's Snowflake row access policies
then read `GETVARIABLE('PG_PROXY_USER')`. "Local PuppyGraph users do not have
an SSO identity to pass to Snowflake."

What this is and is not:

- It is **not** token passthrough. Snowflake authenticates the service
  account; the username is a string PuppyGraph asserts. Snowflake's audit
  trail records the service account, and the policy trusts PuppyGraph to set
  the variable honestly. In the vocabulary of the access-control research
  this is a trusted subsystem that *tells* the source who the user is, so the
  confused-deputy exposure remains.
- It **does** let source-native row policies fire per user with no IdP
  federation to Snowflake, which is why it is operationally attractive. Column
  masking policies can read the same variable, though the docs show only row
  access policies.

### 3.2 Databricks and Unity Catalog

The dedicated `connecting-to-databricks` page returned 404. Databricks is
covered through Delta Lake and Iceberg tutorials that use Unity Catalog as
the metastore, plus a "Databricks JDBC connectivity" line in the 1.0.0 release
notes whose guide also returned 404. The JDBC path is therefore unverified.

| Method | Configuration | Notes |
|---|---|---|
| Personal access token | `"metastore": {"type": "unity", "host": "https://<workspace>.cloud.databricks.com", "token": "<pat>", "databricksCatalogName": "<catalog>"}` | |
| OAuth M2M for a service principal | Same block with `oauthClientId` and `oauthClientSecret` in place of `token` | |

**Read path.** "PuppyGraph reads Delta Lake data through Databricks
credential vending." Unity Catalog issues short-lived storage credentials and
PuppyGraph reads Parquet or Iceberg files from the customer's own S3 bucket or
ADLS Gen2 container. Two hard constraints: tables must live in an external
location, because "Databricks blocks credential vending for tables stored in
the default managed storage"; and vending is supported only "with AWS S3 and
Azure Data Lake Gen2 storage backends".

**Privileges.** On the metastore Details tab enable the "External data
access" toggle. On each schema, grant the token's principal `EXTERNAL USE
SCHEMA`. PuppyGraph's page defers to Databricks' own guide for `USE CATALOG`,
`USE SCHEMA` and `SELECT`; it does not list them. Credential vending has been
in the product since 0.40, with a fix in 0.70.

An important consequence: with credential vending the SQL warehouse is
bypassed. Databricks row filters and column masks, which are enforced by the
SQL engine, do not apply to direct file reads. Unity Catalog governs which
tables the principal may read, not which rows.

### 3.3 BigQuery and Google Cloud

Application Default Credentials via Google's native JDBC driver (`OAuthType=3`).
The key file is mounted and named by `GOOGLE_APPLICATION_CREDENTIALS`; on GCE
the attached service account is used. Required roles, verbatim: "BigQuery Data
Viewer", "BigQuery Job User", "BigQuery Read Session User". Workload Identity
is not documented. Spanner needs `roles/spanner.databaseReader`. BigLake needs
`roles/biglake.viewer`, `roles/storage.objectViewer` and
`roles/serviceusage.serviceUsageConsumer`.

### 3.4 AWS, Iceberg catalogs and object storage

The AWS authentication reference lists the default credential provider chain,
EC2 instance profile ("The most secure and recommended approach for EC2-based
deployments"), static keys in environment variables, `~/.aws/credentials`
profiles, EKS IRSA, and cross-account role assumption via an "IAM Role ARN"
field. IAM policies are given as exact action lists: S3 `s3:GetObject` and
`s3:ListBucket`; Glue `GetDatabase(s)`, `GetTable(s)`, `GetTableVersions`,
`GetPartition(s)`, `BatchGetPartition`, `GetConnection(s)`, `GetDevEndpoint(s)`.

Iceberg metastore options and their auth:

| Catalog | Auth fields | Privileges documented |
|---|---|---|
| Iceberg REST (generic) | `security`, `credential` (OAuth2 client credential), `scope`, `oauthServerUri`, or client ID and secret; SigV4 added in 1.0.0 but fields not documented | Depends on catalog |
| Polaris | `security: oauth2`, `credential: <id>:<secret>`, `scope: PRINCIPAL_ROLE:ALL` | Tutorial grants `TABLE_WRITE_DATA` on the catalog role, which is broader than read |
| Snowflake Open Catalog | Same as Polaris against `…snowflakecomputing.com/polaris/api/catalog` | "Grant all privileges on the namespace to the catalog role" |
| Glue | region, `useInstanceProfile` or keys | IAM list above |
| S3 Tables | `useDefaultCredentialsProviderChain` | "at least `AmazonS3TablesReadOnlyAccess`" |
| Nessie | URI and warehouse; auth disabled in the demo | Not documented |
| Hive Metastore | Thrift URL; Kerberos via mounted `krb5.conf`, keytab and Hadoop XML paths | Not documented |
| OneLake | Service principal client ID and secret against the tenant token endpoint | "Contributor" on the Fabric workspace, which is far broader than read |

With "credential vending" catalogs the storage block is set to "Get from
metastore" and no storage keys are stored in PuppyGraph. Otherwise storage
credentials sit in the catalog: S3 keys or instance profile, GCS service
account key or Compute Engine account with optional impersonation, Azure SAS
token or service principal.

### 3.5 Relational and other JDBC sources

| Source | Auth documented | Privileges documented |
|---|---|---|
| PostgreSQL, AlloyDB | Username and password. Release 1.0.0 claims "PostgreSQL Azure AD service principal authentication" but the connector page does not show it | No |
| MySQL, MariaDB, StarRocks, SingleStore | Username and password | No |
| SQL Server | Username and password; Kerberos / Windows Authentication via mounted `krb5.conf`, `jaas.conf` and keytab, with "No password is stored in the catalog configuration or transmitted over the wire"; Azure AD service principal is listed in the overview but has no section | No |
| Oracle, Vertica, ClickHouse, Redshift | Username and password; Redshift IAM auth not documented | No |
| Trino | Username and optional password; bundled `trino-jdbc-434.jar`; demo runs unauthenticated | n/a |
| Elasticsearch (1.3.0+) | Basic auth only; "API keys, service account tokens, and client certificates (PKI), are not currently supported" | No |
| MongoDB | Atlas SQL JDBC or BI Connector (MySQL protocol) | No |
| DuckDB | None: "DuckDB has no username or password"; file mounted; exclusive lock while connected | n/a |

### 3.6 Where credentials live and how they move

- Inside the catalog: `jdbc.username`, `jdbc.password`, tokens, client secrets,
  storage keys.
- Schema export offers "Download Graph Schema" without credentials and
  "Download Graph Schema with Catalog" with "full connection details".
  Uploading a schema that embeds catalogs requires catalog write permission.
- The Helm chart's `secrets` block covers only PuppyGraph's own admin
  password and JWT signing key, not source credentials.
- Secret-manager integration and environment substitution inside the schema
  JSON: not documented.

---

## 4. Schema extraction and mapping

**Schema JSON (v1).** Top level: `catalog[]`, `node[]`, `edge[]`, optional
`localTable[]`, optional `rowLevelSecurity`. A node has `label`, `id[]`,
`attribute[]` and a `dataSourceGroup` that is one of `externalDataSource`
(`catalog`, `schema`, `table`, `whereClause`, `mappedField[]`, `unnest[]`),
`localDataSource`, or `unionDataSource` with `dedupKey[]`. An edge adds
`fromNodeLabel`, `toNodeLabel`, `fromKey[]`, `toKey[]`. The v0 to v1 migration
made data sources first class and promoted `metaFields` to top-level `id`,
`fromKey` and `toKey` arrays.

**Schema Builder.** After "Create Catalog" the catalog appears as an
expandable tree of databases, tables and columns, so PuppyGraph does introspect
metadata through the JDBC or metastore connection. Whether it uses
`information_schema` or driver `DatabaseMetaData`, and which metadata
privileges that needs, is not documented; the only related control is the
per-catalog "Caching JDBC Metadata" toggle and TTL. Nodes and edges are built
by hand: pick ID columns, pick from and to nodes and their matching columns.
Foreign keys are not inferred.

**AI Assistant.** Optional, Anthropic by default or OpenAI, configured with
`AI_API_KEY`, `AI_BASE_URL`, `AI_MODELS`. It "Surveys the catalog", "Samples
real rows and profiles columns to spot ID-shaped values", and "Probes
foreign-key relationships: for every candidate pair of tables, it tries the
join against real data to measure the hit rate". Requests carry "catalog
metadata, sampled rows, and generated queries" to the LLM provider. Any
deployment with data-residency constraints should treat this as an egress
path.

**Schema upload API.** `POST /schema` with HTTP Basic auth and
`?postUploadBehavior=none|load|switch`.

Contrast with CDF: our mappings are emitted by r2g from introspected schema
and constraints (CSI plus R2RML) and the ontology is derived, reviewed and
published under a separate steward identity (ADR-0004). PuppyGraph has no
ontology layer; the schema JSON is the whole model, and the build-time and
query-time identities are the same admin or GraphAdmin user.

---

## 5. Inbound authentication and access control

| Control | What the docs say |
|---|---|
| Default admin | `puppygraph` / `puppygraph123` via `PUPPYGRAPH_USERNAME` and `PUPPYGRAPH_PASSWORD`; "Change this in production"; marketplace images use the instance ID as the initial password; the admin account cannot be deleted or demoted |
| SSO | OIDC Authorization Code with PKCE (1.0.0); ID token signature, issuer, audience and nonce verified; claims "read from the ID token, not the userinfo endpoint"; providers documented: Okta, Auth0, Google Workspace, Entra ID, Keycloak; `SSO_CLAIM_AS_USER_ID` (default `email`), `SSO_GROUPS_CLAIM` (default `groups`). The Web UI page mentions SAML, the SSO page documents only OIDC. Enterprise edition only per the pricing page |
| RBAC | On by default since 1.0.0. Five fixed roles, no custom roles: Admin, UserAdmin, GraphAdmin ("Cannot create or delete catalogs"), Analyst ("Run queries; read graph schemas and catalog metadata"), Viewer ("Cannot run queries"). Enforced "at every entry point: REST, Bolt, and Gremlin"; 1.6.0 added "operation-level authorization for submitted queries". Role precedence: explicit assignment, then IdP group mapping, then `RBAC_DEFAULT_ROLE` (default Analyst) |
| Service accounts | Created in Settings, secret shown once. Impersonation via Bolt `impersonated_user` or header `X-PuppyGraph-Assume-User`; the assumed role must not exceed the service account's role |
| Row-level security | An entitlement table in a catalog with Username, Resource Type, Resource Value and Is Authorized columns, referenced by `rowLevelSecurity.entitlementSource` and applied through `tableSecurityFilter[]`; `entitlementCacheTtlSeconds` default 3600. "RLS filtering is applied at every step of a graph traversal, not just the starting vertices." Users with no entitlement rows bypass RLS |
| Column or property masking | Not documented |
| Multi-tenancy | One graph per instance; "logical partitions" are a query-time `USING` directive for pruning, not a security boundary |
| Audit log | Records timestamp, actor, action, target for user and role changes, service-account management and access denials; storage location not documented |
| Internal tokens | Control plane mints JWTs signed with `AUTHENTICATION_JWT_SECRETKEY`; `GREMLINSERVER_AUTHENTICATION_ENABLED`, `BOLTSERVER_AUTHENTICATION_ENABLED` |
| TLS | No native TLS settings documented; the only guide terminates TLS at nginx in front of ports 8081, 8182 and 7687 |
| Compliance | SOC 2 badge on the security page; "your data stays exclusively within your controlled environment" |

Two design points stand out. First, the RLS entitlement table is itself read
through a catalog under the service credential, so the policy data and the
protected data share a trust boundary. Second, the fail-open default (no
entitlement rows means no filtering) is the opposite of the fail-closed
posture ADR-0004 requires for CDF's delegated mode.

---

## 6. Editions, pricing and licensing

| | Developer | Enterprise |
|---|---|---|
| Price | "Forever free" | Annual, based on "the Memory and CPU of the server that runs PuppyGraph"; "No storage fees. Pricing doesn't depend on data volume"; 30-day full-feature trial |
| Topology | "Single node" | "Multi-instance cluster" |
| Simultaneous data sources | 2 | Unlimited |
| SSO | No | Yes |
| Monitoring, Datadog | No | Yes |
| AMI on EC2 | No | Yes |
| Support | Slack community | Dedicated Slack channel, email SLA |

RBAC and RLS carry no edition marker in the docs or pricing table. The MSA
§16 defines the Developer edition as for "evaluation, internal demonstration,
testing or other non-production or non-commercial purposes", with no
warranties or support, terminable "at any time". No license-key mechanism is
documented for Docker, cluster or Helm installs.

AWS Marketplace lists "PuppyGraph Professional" as an hourly AMI with a
30-day trial: r6i.2xlarge $0.90/h, r6i.4xlarge $1.80/h, r6i.8xlarge $3.60/h
(recommended), c6i.32xlarge $7.20/h. The docs call the same listing
"PuppyGraph Enterprise". Azure and GCP listings exist under the Professional
name; their prices could not be verified.

**For CDF's purposes:** the Developer edition is enough to reproduce any
claim in this document locally at no cost, within the two-source limit.

---

## 7. The user manuals

All documentation is public at https://docs.puppygraph.com/ with these
top-level sections: `getting-started`, `installation`, `security`,
`user-interface`, `ai`, `connecting/connecting-to-<source>`, `modeling`,
`querying`, `reference`, `releases`, `help`, and `archive/v0` for the 0.x
docs. The sitemap holds 354 URLs. There is no PDF, print or offline build,
and no docs-source repository on GitHub; the `puppygraph-getting-started`
repo (Apache-2.0) holds demo compose files and sample schemas. Support
channels are Slack, contact@puppygraph.com, and GitHub.

Supported sources as the Connecting section enumerates them (21): AlloyDB,
BigQuery, ClickHouse, Delta Lake, DuckDB, Elasticsearch, Hive, Hudi, Iceberg,
MongoDB, MySQL, Oracle, PostgreSQL, Redshift, SingleStore, Snowflake, Spanner,
SQL Server, StarRocks, Trino, Vertica. Iceberg catalog flavours: REST, Nessie,
Polaris, Tabular, Google Cloud Lakehouse, AWS Glue, Amazon S3 Tables, Hive
Metastore, OneLake. Not listed anywhere: Presto, Azure Synapse (logo only),
Databricks JDBC (release note only).

---

## 8. Competitors and how they handle the same problem

Full profiles are in the companion documents. The short comparison on the
axis that matters here:

| Product | Outbound identity | End-user identity reaches source? | Per-user scoping lives in |
|---|---|---|---|
| **PuppyGraph** | Service credential per catalog | Snowflake only, by asserted session variable | Source row policies (Snowflake) or PuppyGraph entitlement-table RLS |
| **Stardog** | Service JDBC credential per data source | Yes, opt-in OAuth pass-through to Databricks, Snowflake, Aurora, REST; introspection still uses the service account | Source (when passing through); Stardog named-graph security otherwise |
| **Timbr.ai** | Service JDBC credential | No | Timbr policy objects |
| **RelationalAI** | Runs inside Snowflake as the `RELATIONALAI` role | No; per-user row and masking policies explicitly unsupported | Snowflake, keyed to the app role only |
| **Ontop / GraphDB** | Service JDBC credential | No | GraphDB repository roles and ACLs |
| **Neo4j Virtual Graph** (preview) | Read-only Snowflake service account, key-pair | No | Snowflake grants of the service user |
| **Denodo, Starburst, Dremio** (SQL federation) | Service credential or per-source pass-through | Yes, with configured identity federation | Source when passing through; engine policies otherwise |

PuppyGraph sits in the middle of the field: more than Timbr, RelationalAI and
Neo4j Virtual Graph (which have no per-user path to the source at all), less
than Stardog and the SQL federators (which carry a real user token). Its
session-variable trick is unique in this set and is the cheapest way to make
source-native row policies fire per user, at the cost of the source having to
trust the engine's assertion.

---

## 9. Corrections to the Gemini starter note

| Starter claim | What the docs say |
|---|---|
| Inbound "OIDC, OAuth2, JWT" | OIDC Authorization Code with PKCE only; SAML mentioned once but not documented; Enterprise edition only |
| "internal RBAC and RLS rules, filtering specific node properties" | RBAC has five fixed roles. RLS filters rows via an entitlement table. Property or column masking is not documented |
| Snowflake "Key-Pair Authentication" only | Also OAuth 2.0 client credentials and raw JDBC; plus query impersonation via session variable, which the note omitted |
| Databricks "access tokens alongside Databricks Credential Vending" | Correct, plus OAuth M2M; credential vending needs external storage locations and `EXTERNAL USE SCHEMA`, and it bypasses SQL-warehouse row filters and masks |
| "Schema Builder … auto-detect schemas, tables, and data types" | Correct for introspection; node and edge mapping is manual, foreign keys are not inferred; the AI Assistant is a separate, LLM-backed feature |
| Snowflake privileges "USAGE … plus SELECT" | Stated by the note, not by PuppyGraph; the docs give no GRANT statements |
| Sources include "Presto" and "Vertica" | Vertica yes; Presto is not documented anywhere |
| Manuals cover "Vertica" and "deployment blueprints for Docker Compose and Kubernetes" | Correct; Kubernetes is a Helm chart with a 1 control-plane, 3 leader, 3 compute default |

---

## 10. Implications for CDF (pointers only; the synthesis carries the argument)

- PuppyGraph confirms that the market default for graph virtualization is
  the **trusted-subsystem** model with engine-side row filtering. CDF's
  service mode today is the same posture. ADR-0004's delegated mode, once
  brokers exist, is a differentiator against every graph competitor except
  Stardog.
- The **Snowflake session-variable pattern** is worth evaluating as a
  low-cost intermediate step for CDF's Snowflake leg: a `SET` of the
  authenticated principal before each leg lets customer row access policies
  fire without an IdP federation project. It must be labelled honestly in
  the envelope as an asserted identity, not an authenticated one.
- **Credential vending bypasses engine-enforced policies.** If CDF ever adds
  a direct Iceberg or Delta file-reading leg, Unity Catalog row filters and
  masks will not apply, exactly as for PuppyGraph. Keep SQL-warehouse legs as
  the policy-bearing path.
- PuppyGraph's **AI Assistant ships sampled rows to an LLM provider**. CDF's
  schema-first derivation via r2g and the ontology extractor never needs
  row samples to leave the customer's environment; that is a sales point.
- PuppyGraph's **RLS fails open** for users with no entitlement rows. CDF's
  delegated mode fails closed by design. State that difference in the
  customer Q&A.

---

## Sources

docs.puppygraph.com pages read (all under https://docs.puppygraph.com/):
`/` · `/getting-started/` · `/connecting/` · `/security/` · `/security/sso/` · `/security/rbac/` · `/security/row-level-security/` · `/installation/` · `/installation/cluster-deployment/` · `/installation/helm-chart/` · `/modeling/` · `/modeling/building-a-graph/` · `/modeling/data-sources/` · `/modeling/managing-the-graph/` · `/modeling/migrating-from-v0/` · `/querying/querying-using-gremlin/` · `/querying/querying-using-opencypher/` · `/releases/` · `/help/` · `/sitemap.xml` · `/ai/built-in-chatbot/` · `/user-interface/puppygraph-web-ui/` · `/reference/cloud_integrations/aws/authentication/` · `/reference/cloud_integrations/aws/aws-iam-policies/` · `/reference/cloud_integrations/gcp/authentication/` · `/reference/databricks-credential-vending/` · `/reference/logical-partition/` · `/archive/v0/reference/schema/` · connectors `/connecting/connecting-to-{snowflake,bigquery,postgresql,mysql,sql-server,trino,iceberg,delta-lake,apache-hudi,elasticsearch,duckdb,vertica,redshift,mongodb,oracle,clickhouse,spanner,alloydb,singlestore,starrocks}/` · tutorials `/getting-started/{launching-puppygraph-in-docker,launching-puppygraph-from-aws-marketplace,modeling-a-graph-through-the-schema-builder,building-a-graph-with-the-ai-assistant,querying-snowflake-data-as-a-graph,querying-snowflake-with-sso-impersonation,querying-unity-catalog-data-as-a-graph,querying-databricks-delta-lake-data-as-a-graph,querying-databricks-iceberg-data-as-a-graph,querying-polaris-data-as-a-graph,querying-snowflake-open-catalog-data-as-a-graph,querying-nessie-data-as-a-graph,querying-s3-tables-data-as-a-graph,querying-onelake-data-as-a-graph,querying-biglake-data-as-a-graph,querying-kerberized-hive-data-as-a-graph,querying-duckdb-data-as-a-graph,querying-trino-data-as-a-graph,sni-tls-nginx}/`

puppygraph.com: https://www.puppygraph.com/ · https://www.puppygraph.com/pricing · https://www.puppygraph.com/msa · https://www.puppygraph.com/security-information · https://www.puppygraph.com/blog/virtual-graph

Other: https://github.com/puppygraph · https://github.com/puppygraph/puppygraph-getting-started · https://aws.amazon.com/marketplace/pp/prodview-dgmn5jnwnfacu

Indexed but 404 at fetch time, so unverified: `/connecting/connecting-to-databricks/`, `/connecting/connecting-to-hive/`, `/getting-started/querying-databricks-data-as-a-graph/`, `/schema/`, `/reference/schema/jdbc-catalog/`, `/introduction/`. The Azure Marketplace listing returned 403.

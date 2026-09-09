---
title: "SQL federation and data virtualization vendors — source authentication and access-control profiles"
type:
  - internal
  - research
  - competitive-analysis
date: 2026-09-09
status: draft — for team review
related:
  - "docs/research/vendor-auth-access-control-survey.md (the synthesis this feeds)"
  - "docs/research/graph-virtualization-vendor-security-profiles.md"
  - "docs/research/puppygraph-security-and-schema-profile.md"
  - "docs/research/data-source-identity-mechanisms.md"
  - "docs/research/trino-federation-engine-evaluation.md"
  - "docs/architecture/access-control-research.md"
  - "docs/architecture/module-05-federated-query-engine/adr/ADR-0004-identity-planes-and-policy-enforcement.md"
---

# SQL federation vendors: how they authenticate to sources and control access

> **Scope.** The SQL-first federation and virtualization tier: Trino and its
> commercial distribution Starburst, Denodo, Dremio, Amazon Athena federated
> query, BigQuery external connections, TIBCO Data Virtualization, IBM Data
> Virtualization with the Db2 SQL/MED lineage, Microsoft Fabric shortcuts and
> Power BI single sign-on, PostgreSQL's `postgres_fdw`, Databricks Lakehouse
> Federation, and Snowflake's own federation features. These are the products
> that have solved per-user delegation most completely, so they are the
> reference designs for CDF's delegated mode in ADR-0004.
>
> **Method.** Facts come from vendor documentation read on 2026-09-09.
> Claims found only in third-party coverage or search snippets are marked
> `[secondary]`. "Not documented" means the vendor's public docs were checked
> and say nothing. Denodo's documentation pages render only their table of
> contents to fetch tools, so those pages were pulled raw and text-extracted.
>
> **The five questions per vendor**
> 1. Outbound: how does it authenticate to the source, and where do credentials live?
> 2. Identity: does the end user's identity reach the source, and by which mechanism?
> 3. Inbound: SSO protocols and the authorization model the product enforces itself.
> 4. Schema: how is metadata discovered and what privileges does that need?
> 5. Operations: rotation, audit, least privilege, network guidance.

---

## 0. Summary table

| Vendor | Outbound credential storage | Delegation mechanisms to the source | Engine-side policy | Notable caveat |
|---|---|---|---|---|
| **Trino** (OSS) | Catalog properties; `${ENV:VAR}`; `credential-provider.type` INLINE, FILE, KEYSTORE | Extra-credentials passthrough from the client; Kerberos impersonation for HDFS and Hive Metastore (proxyuser) | File-based rules, OPA, Apache Ranger, custom SPI; row filters and column masks in all three | Snowflake connector documents password only |
| **Starburst** (SEP, Galaxy) | Same as Trino plus key-pair for Snowflake, PAT or OAuth passthrough for Unity, vended credentials; Galaxy stores catalog credentials with no staff plaintext access | Password passthrough, OAuth 2.0 token passthrough, JWT passthrough, Kerberos passthrough, user impersonation with auth-to-local rules; Snowflake supports six impersonation types | Built-in access control, Ranger, Immuta; Galaxy RBAC plus tag ABAC | OAuth passthrough has no token refresh: token must outlive the query |
| **Denodo** | Data-source credentials; Credentials Vault (AWS Secrets Manager, Azure Key Vault, CyberArk, HashiCorp) with `@{USER_NAME}` per-user secrets | "Pass-through session credentials": Kerberos constrained delegation (S4U2proxy, protocol transition), OAuth via RFC 8693 token exchange, Azure on-behalf-of, or raw token passthrough; password passthrough | Roles, view privileges, column and row restrictions with masking, tag-driven Global Security Policies (Enterprise Plus) | Introspection always uses the data-source credentials; cache ignores per-user restrictions |
| **Dremio** | Stored in the source definition or referenced from Azure Key Vault, AWS Secrets Manager, HashiCorp Vault; `dremio-admin encrypt` for config secrets | Snowflake "OAuth with impersonation" via a Snowflake security integration trusting Dremio as issuer; Hive impersonation via proxyuser; vended credentials from Unity and Iceberg REST | RBAC with ownership and inheritance; SQL row-access and column-masking policies as UDFs; Ranger for Hive | Reflections disabled on impersonated sources |
| **Athena federated** | Lambda connector plus AWS Secrets Manager (mandatory for new connectors) | None: Lambda execution role and stored secret. Fine-grained access via Lake Formation when registered in Glue | IAM plus Lake Formation | Users must not hold direct `lambda:InvokeFunction` when Lake Formation governs |
| **BigQuery** connections | Encrypted connection resource; service agent identity; Omni uses web-identity federation with no stored cloud keys | Service identity via the connection's service account; users need `bigquery.connectionUser` | IAM on connections | Whether user identity reaches Spanner is not stated |
| **TIBCO TDV** | Per-source saved credentials, or pass-through | Pass-through login by default when no credentials saved; Kerberos SSO with SPNs; per-session `setDataSourceCredentials()` | Row-based security via SQL filter policies | Requires per-user exclusive connections under Kerberos |
| **IBM Data Virtualization / Db2** | Shared or personal connection credentials; platform-login JWT option | Db2 `CREATE USER MAPPING` (SQL/MED); Kerberos SSO with SPN delegation | Data protection rules mask results only | Masking does not apply to predicates or views |
| **Microsoft Fabric / Power BI** | Shortcut connection identity (user, service principal, or account key) | Passthrough for same-tenant OneLake; delegated fixed identity for everything external; Power BI DirectQuery SSO forwards the Entra token to Snowflake | OneLake security roles on delegated shortcuts | Snowflake mirroring requires re-creating source RLS in Fabric |
| **postgres_fdw** | `CREATE USER MAPPING` per local role | The SQL/MED standard itself; GSSAPI credential delegation; SCRAM passthrough (PG17+) | Local Postgres privileges | `password_required=false` is superuser-only and dangerous on `public` |
| **Databricks Lakehouse Federation** | Connection object; `secret()` recommended | None documented; connection identity only | Unity Catalog privileges on the foreign catalog | Row filters and masks not applicable to foreign tables |
| **Snowflake as federator** | Catalog integrations with OAuth client credentials, bearer, SigV4; storage integrations with no explicit cloud keys | n/a | Snowflake RBAC | Rotation via `ALTER CATALOG INTEGRATION` |

**Reading the table.** Every mature federation product supports both models
per source: a stored service credential by default and an opt-in delegated
path where the source can accept the user's identity. The delegated path has
three implementation families: credential or token passthrough, impersonation
by a trusted service (proxyuser, auth-to-local, constrained delegation), and
per-user credential mapping (SQL/MED). Denodo and Starburst implement all
three.

---

## 1. Trino (open source)

**Outbound.** JDBC-family connectors take `connection-user` and
`connection-password` in the catalog properties file. `credential-provider.type`
selects INLINE (default), FILE (a separate properties file), or KEYSTORE (JKS or
PEM with named user and password entries). Any properties file may reference
`${ENV:VAR}` so that "No secret is stored in the Trino configuration files on
the filesystem." The Snowflake connector documents only user and password plus
account, role and warehouse; key-pair and OAuth are not documented in OSS Trino
483. Hive and HDFS support Kerberos with a principal and keytab; the keytab
"must be readable by the operating system user running Trino" with restricted
permissions.

**Identity.** Two delegation routes. First, extra-credentials passthrough: a
catalog declares `user-credential-name` and `password-credential-name`, and the
client supplies the actual values through the JDBC `extraCredentials`
parameter, so the connector logs in as the end user without the coordinator
storing anything. Second, impersonation for Hadoop-family sources:
`hive.hdfs.impersonation.enabled` and `hive.metastore.thrift.impersonation.enabled`.
Without it "All queries are executed as the OS user who runs the Trino process,
regardless of which user submits the query"; with it, the Hadoop cluster's
proxyuser configuration must authorize Trino's principal to impersonate.

**Inbound.** Authenticators: password file, LDAP, Salesforce, OAuth 2.0,
certificate, JWT, Kerberos; TLS is the recommended first step and required with
a shared secret for password auth. Access control is pluggable: file-based
rules on catalog, schema, table and column with row filters and column masks
(first match wins, default deny); Open Policy Agent with separate endpoints for
row filters and column masks and requests carrying identity, groups, query id,
action and resources; Apache Ranger with audit to Solr, Elasticsearch, HDFS or
S3; or a custom SPI implementation.

**Schema.** Connectors implement `ConnectorMetadata` (`listSchemaNames`,
`listTables`, `streamTableColumns`, …). The base JDBC connector reads
schemas, tables and columns through `java.sql.DatabaseMetaData`, opening the
connection with the session's `ConnectorIdentity`, so under passthrough
introspection also runs as the user. Required remote privileges are not
documented.

**Operations.** Secrets through environment variables managed by provisioning
systems; keytab permissions "similar to SSH private keys"; Ranger audit stores.
Rotation guidance is not documented.

## 2. Starburst Enterprise and Galaxy

**Outbound.** Adds to Trino: Snowflake key-pair via
`snowflake.connection-private-key-file` or an inline Base64 key with passphrase;
Unity Catalog via PAT, `OAUTH2_PASSTHROUGH`, and credential vending
(`hive.metastore.unity.vended-credentials-enabled`, credentials "scoped to the
table ID or external location in Unity Catalog", for S3, ADLS Gen2 and GCS).
Galaxy S3 catalogs use a cross-account IAM role or access keys. On Galaxy
credential storage: "Starburst does not have access to credentials for those
catalogs within a customer account" and staff lack plaintext access;
encryption specifics are not documented.

**Identity.** The richest menu in the survey.

| Mechanism | Server setting | Catalog setting | Precondition |
|---|---|---|---|
| Password passthrough | `http-server.authentication.type=DELEGATED-PASSWORD` | `<connector>.authentication.type=PASSWORD_PASS_THROUGH` | "the data source and SEP must use the same authentication backend and use the same credentials", for example one Active Directory |
| OAuth 2.0 token passthrough | `DELEGATED-OAUTH2` | connector-specific | Source "must be configured to support an external OAuth 2.0 server"; no refresh: "Each access token's remaining lifetime must be longer than the query's execution time" |
| JWT passthrough | JWT authenticator | `snowflake.impersonation-type=JWT_PASSTHROUGH` | Snowflake external OAuth trusting the same issuer |
| Kerberos passthrough | Kerberos authenticator | connector-specific | Constrained delegation configured in AD |
| User impersonation | any | `<connector>.impersonation.enabled` with auth-to-local rules or LDAP mapping | Service user must be "trusted in this system and to be allowed to impersonate other users" |

Snowflake specifically offers `snowflake.impersonation-type` values `NONE`,
`ROLE` (map user to a Snowflake role), `OKTA_LDAP_PASSTHROUGH`,
`ROLE_OKTA_LDAP_PASSTHROUGH`, `OAUTH2_PASSTHROUGH`, `JWT_PASSTHROUGH`; Okta SAML
SSO to Snowflake and SEP registered as a pre-authorized OAuth client are the
preconditions; `snowflake.role` is unavailable under impersonation. The
connector feature matrix lists impersonation for Db2, Greenplum, Netezza,
Oracle, PostgreSQL, Redshift, SAP HANA, SQL Server, Snowflake, Synapse,
Teradata, Vertica and generic JDBC; password passthrough additionally for
ClickHouse, MongoDB and Kafka; OAuth2 passthrough for Delta Lake, Hive,
Iceberg, BigQuery, Snowflake, Elasticsearch, Kafka and Stargate. Unity
credential passthrough is "only supported with Azure Databricks and when
Microsoft Entra is the IdP".

**Inbound.** Built-in access control (roles to users and groups; entities
including tables, columns, functions, session properties and data products;
requires the backend service and a license; only password-based authentication
types supply identity). Ranger with row filtering and column masking. Immuta
with ABAC policies "applied in SEP as access control rules". Galaxy: RBAC with a
single active role, ownership, privileges from account down to function, plus
tag-based ABAC; row filters "evaluated using the privileges of the role that
owns the row filter"; DENY overrides ALLOW.

**Operations.** Galaxy audit logs of all account actions, AWS PrivateLink, NAT
egress IPs. The OAuth token-lifetime caveat is the main operational trap for
long federated queries.

## 3. Denodo Platform

**Outbound.** JDBC data-source authentication options: "Use login and
password", "Use Kerberos", "Use OAuth" (client credentials or resource-owner
password), "Use Pass-through session credentials", "Use AWS IAM Credentials"
(Athena and Redshift), and "Credentials vault". The Credentials Vault supports
AWS Secrets Manager, Azure Key Vault, CyberArk and HashiCorp Vault; secrets are
cached in memory and re-fetched on failure and restart; a `@{USER_NAME}` pattern
in the secret name yields per-user secrets, so the vault becomes a SQL/MED
mapping table; the stated benefit is that the security team "can now
configure the vault to rotate the password".

**Identity.** Pass-through session credentials means Virtual DataPort "will
use the credentials of the user who is executing the query", and the
data-source credentials "are only used at design-time to inspect the sources".
The mechanism depends on how the user reached Denodo:

- Kerberos client: Denodo requests a Kerberos ticket "on behalf of this user"
  using constrained delegation. Two AD modes are documented: "Use kerberos
  only" (S4U2proxy, needs forwardable tickets) and "Use any authentication
  protocol" (S4U2self protocol transition), the latter enabling pass-through
  "even if the end user has connected to VDP without using Kerberos like, for
  example, using Oauth". Resource-based constrained delegation covers cross-domain.
- OAuth client: Denodo obtains a source token by "Token exchange flow (RFC
  8693)", "On-behalf-of flow (Azure AD)", or "OAuth Token pass-through"; token
  exchange is recommended as "more secure".
- Login and password client: same credentials forwarded, or converted to a
  ticket or token via "Requires Kerberos" or "Requires OAuth" options.

Consequences documented: one connection pool per user (initially one
connection); Oracle proxy authentication offered as a lighter alternative; and
the cache "does not check which user populated it", so caching under
pass-through must be populated by an unrestricted scheduled user or disabled.

**Inbound.** Server authentication via LDAP, Kerberos, SAML, OAuth, or the
Denodo Security Token. Authorization: roles, coarse view privileges, and
fine-grained column restrictions and row restrictions (reject rows or mask
fields), with administrators exempt from row restrictions. Global Security
Policies apply tag-driven rules once across views (mask columns tagged
"confidential") and are "only available with the subscription bundle
Enterprise Plus".

**Schema.** The "Create base view" wizard always introspects with the
data-source credentials, even under pass-through or vault: "the data source
will use the credentials of the data source to connect to the database and
list the views, not the user name of the current user". Base views are
metadata only.

## 4. Dremio

**Outbound.** Snowflake: login and password, key-pair, or "OAuth with
impersonation"; secrets stored in the source definition ("Dremio stores the
password") or referenced by Azure Key Vault URI, AWS Secrets Manager ARN or
HashiCorp Vault path. PostgreSQL: master credentials or vault references, no
auth, or Kerberos. S3: access key with optional assumed role, EC2 metadata, EKS
Pod Identity, AWS profile; minimum read IAM `s3:GetBucketLocation`,
`s3:ListAllMyBuckets`, `s3:ListBucket`, `s3:GetObject`. Unity Catalog:
Databricks PAT or Entra ID application, with "Use vended credentials" on by
default. Iceberg REST catalogs default to vended credentials. `dremio-admin
encrypt` protects configuration-file secrets; how stored source passwords are
encrypted is not documented.

**Identity.** Snowflake OAuth impersonation is a clean example of the
"engine as issuer" design: Snowflake is configured with a `SECURITY
INTEGRATION` whose `EXTERNAL_OAUTH_ISSUER` is Dremio, whose
`EXTERNAL_OAUTH_RSA_PUBLIC_KEY` is Dremio's, and whose
`EXTERNAL_OAUTH_TOKEN_USER_MAPPING_CLAIM` is `sub`; Dremio then mints per-user
tokens Snowflake trusts, with role mapping "Any Role" or a defined role list.
The cost: "Reflections are not supported on data sources with user
impersonation enabled", because a cached acceleration cannot be attributed to
one user. Hive uses proxyuser impersonation with an "Allow VDS-based Access
Delegation" switch that decides whether the view owner or the query user is
impersonated.

**Inbound.** Console SSO via OIDC, LDAP or local passwords, and personal
access tokens; applications via external JWT, OAuth client credentials, or
Entra service principals. RBAC with single ownership and inheritance from
source to folder to dataset. Row-access and column-masking policies are UDFs
attached with `ALTER TABLE … ADD ROW ACCESS POLICY` and `SET MASKING POLICY`;
Ranger integration for Hive rewrites queries as an "implicit view".

**Schema.** Metadata refresh defaults for PostgreSQL are one hour for
discovery and three hours for expiry. Required remote privileges are not
documented.

## 5. Amazon Athena federated query

Connectors are Lambda functions. Newer connectors use Glue Connection objects
and "must use AWS Secrets Manager to store credentials"; legacy ones take a
JDBC string with a `${secret_name}` placeholder resolved to a JSON secret with
`username` and `password`. Snowflake supports key-pair (secret holding
`sfUser`, `pem_private_key`, passphrase) or OAuth (a Snowflake `SECURITY
INTEGRATION TYPE=OAUTH`, with refresh tokens). Sample least-privilege grants
are given: `USAGE` on warehouse and database, `SELECT ON ALL TABLES`. Spill
buckets are encrypted with AES-GCM by default.

The identity model is service-only: the Lambda execution role plus the stored
secret. Per-user passthrough is not documented. Fine-grained access instead
comes from registering the connector as a Glue Data Catalog governed by Lake
Formation (catalog, database, table, column, row, tag levels), at which point
users must *not* hold `lambda:InvokeFunction`, spill-bucket or Glue-connection
permissions directly, or they bypass the policy. Rotation of secrets is
"highly recommend[ed]"; spill prefixes should carry S3 lifecycle rules.

## 6. Google BigQuery external connections

A Cloud SQL connection stores a username and password in a connection
resource: "Connections are encrypted and stored securely in the BigQuery
connection service" (Google-managed or customer keys); the API exposes
`has_credential` and never returns the secret. The connection runs under a
service agent that needs the Cloud SQL Client role. Cloud-resource (BigLake)
connections create a delegated service account that accesses storage "on
behalf of users". BigQuery Omni on AWS uses an IAM role trusting
`accounts.google.com` with `sts:AssumeRoleWithWebIdentity`, so no AWS keys are
stored; Omni on Azure uses workload identity federation with "no application
client secrets to be managed by you or Google". Users need
`roles/bigquery.connectionUser`; admins `roles/bigquery.connectionAdmin`.
Spanner federated queries require the *user* to hold `roles/spanner.databaseReader`,
but the docs do not state whether the user's identity or the connection's
reaches Spanner.

## 7. TIBCO (Spotfire) Data Virtualization

"TDV Server session credentials are used by default to log in to the data
source when no other credentials have been set", so pass-through is the
default posture. JDBC clients may set per-source credentials for their own
session with `setDataSourceCredentials()`. With Kerberos SSO enabled "the
client's Kerberos token is used to negotiate a connection with the data
source", requiring SPNs (except Oracle) and per-user exclusive connections.
Row-based security is SQL-script filter policies appended "at run time using
the AND operator", applied before column policies. Explicit credential
mapping tables are not documented in the fetched pages.

## 8. IBM Data Virtualization and Db2 SQL/MED

Cloud Pak for Data connections use shared or personal credentials; the Data
Virtualization connection type also offers "Use my platform login
credentials", forwarding the session JWT. Kerberos SSO for Oracle and Hive uses
an SPN and keytab, with the AD administrator configuring "the SPN to delegate
user credentials". Masking via data protection rules applies to query results
only, "does not stop Data Virtualization users from connecting", and is not
applied to predicates or views. The source-side credential entered for a
virtualized system is "an ID with read-only access to the data source" that
"does not necessarily correspond to a Cloud Pak for Data username" `[secondary]`.

Db2 carries the ISO SQL/MED standard: `CREATE USER MAPPING FOR
authorization-name | USER | PUBLIC SERVER s OPTIONS (REMOTE_AUTHID …,
REMOTE_PASSWORD …)`; "REMOTE_PASSWORD option is always required"; DBADM is
needed when IDs differ; public and non-public mappings cannot coexist.

## 9. Microsoft Fabric, OneLake and Power BI

OneLake shortcuts have two identity models. **Passthrough** is the default for
same-tenant OneLake-to-OneLake shortcuts: the user's identity reaches the
target. **Delegated** uses a fixed connection identity ("another user's
identity, a service principal, or an account key") and is the only option for
cross-tenant and all external shortcuts (S3, GCS, ADLS). Delegated shortcuts
can carry OneLake security roles, with column-level security on both sides and
row-level security on the producer side only. S3 shortcuts need
`S3:GetObject`, `S3:GetBucketLocation`, `S3:ListBucket`. Snowflake mirroring
authenticates by password, Entra SSO, or key pair, needs `CREATE STREAM`,
`SELECT`, `SHOW`, `DESCRIBE`, and comes with the warning that "Any granular
security established in the source Snowflake warehouse must be re-configured
in the mirrored database".

Power BI DirectQuery SSO to Snowflake is the canonical token-passthrough
example: the tenant setting "Snowflake SSO" consents "to sending your
Microsoft Entra token to the Snowflake servers", the dataset option is "End
users use their own OAuth2 credentials when accessing this data source via
DirectQuery", and Snowflake is configured with a `SECURITY INTEGRATION
type=external_oauth external_oauth_type=azure` mapping the `upn` claim to
`login_name`, with privileged roles blocked by default.

## 10. PostgreSQL `postgres_fdw`

The SQL/MED reference implementation. `CREATE SERVER` takes libpq options
except `user`, `password` and `sslpassword`, which must go in a user mapping.
`CREATE USER MAPPING FOR {user | USER | CURRENT_ROLE | PUBLIC} SERVER s OPTIONS
(user, password)`; the server owner may create mappings for anyone, and a user
with `USAGE` on the server may map themselves. "Non-superusers may connect …
using password authentication or with GSSAPI delegated credentials"
(`gssdelegation 'true'`); `use_scram_passthrough` (PG17+) forwards SCRAM
secrets; `password_required 'false'` is superuser-only, carries a CVE warning,
and must not be set on `public`. One connection per user mapping. `IMPORT
FOREIGN SCHEMA` imports remote definitions and the remote user needs the
corresponding privileges.

## 11. Databricks Lakehouse Federation, briefly

A connection object holds credentials (`CREATE CONNECTION … OPTIONS (host,
port, user, password)`), with `secret('<scope>','<key>')` recommended. Needed
privileges: `CREATE CONNECTION`, `USE CONNECTION`, `CREATE FOREIGN CATALOG`.
Access to a foreign catalog is governed by standard Unity Catalog privileges;
on dedicated-access compute "you must own the underlying connection".
Snowflake connections support OAuth via Entra ID, Okta or Snowflake's built-in
OAuth, an OAuth access token, a PEM private key, or basic auth. Per-user
credential passthrough is not documented, and row filters and column masks
cannot be applied to foreign tables.

## 12. Snowflake as a federator, briefly

Iceberg REST catalog integrations authenticate with OAuth client credentials,
bearer or PAT, or SigV4 for Glue, with vended credentials or external volumes,
and rotate via `ALTER CATALOG INTEGRATION … SET REST_AUTHENTICATION`. Storage
integrations "avoid the need for passing explicit cloud provider credentials"
through a Snowflake-provisioned IAM identity and external-ID trust. External
access integrations pair network rules with `CREATE SECRET` objects consumed by
functions and procedures.

---

## 13. Patterns this tier establishes

1. **Per-source toggle is universal.** Denodo, Starburst, Dremio, TIBCO and
   Fabric all decide service versus delegated per source, never globally.
   ADR-0004's per-source `service | delegated` mode matches the field.
2. **Three delegation families.** Passthrough of the client's credential or
   token (Trino extra-credentials, Starburst passthrough types, Denodo
   passthrough, TIBCO, Fabric passthrough, Power BI SSO); impersonation by a
   trusted service (Hadoop proxyuser, Starburst auth-to-local, Dremio as
   Snowflake OAuth issuer, Denodo constrained delegation); and per-user
   credential mapping (postgres_fdw, Db2 user mapping, Denodo's `@{USER_NAME}`
   vault pattern, TIBCO per-session credentials).
3. **Introspection stays on the service identity** even when queries pass
   through (Denodo explicitly, Stardog in the graph tier). Only Trino's base
   JDBC path opens metadata connections with the session identity.
4. **Caches and accelerations break under delegation.** Denodo's cache
   ignores the populating user; Dremio disables reflections on impersonated
   sources. CDF's assembly cache keyed on `(canonical sub-query, as-of,
   entitlement scope)` in the M12 survey is the right answer to this.
5. **Token lifetime is an execution constraint.** Starburst refuses to refresh
   a passed-through OAuth token mid-query. CDF's per-leg deadlines must be
   bounded by the delegated token's remaining lifetime.
6. **Keyless cloud trust is replacing stored secrets.** BigQuery Omni
   web-identity federation, Snowflake storage integrations, and credential
   vending from Unity, Polaris and Lake Formation all avoid a long-lived
   secret in the engine.
7. **Fail-closed tenancy is a documented gotcha.** Athena warns that users
   holding `lambda:InvokeFunction` directly bypass Lake Formation. Any
   engine-enforced policy is only as strong as the network and IAM around
   the engine.

---

## Sources

Trino: https://trino.io/docs/current/security/overview.html · https://trino.io/docs/current/connector/postgresql.html · https://trino.io/docs/current/security/secrets.html · https://trino.io/docs/current/security/password-file.html · https://trino.io/docs/current/client/jdbc.html · https://trino.io/docs/current/connector/snowflake.html · https://trino.io/docs/current/object-storage/file-system-hdfs.html · https://trino.io/docs/current/object-storage/metastores.html · https://trino.io/docs/current/security/file-system-access-control.html · https://trino.io/docs/current/security/opa-access-control.html · https://trino.io/docs/current/security/ranger-access-control.html · https://trino.io/docs/current/develop/connectors.html · https://github.com/trinodb/trino/blob/master/plugin/trino-base-jdbc/src/main/java/io/trino/plugin/jdbc/BaseJdbcClient.java

Starburst: https://docs.starburst.io/latest/security/biac-overview.html · https://docs.starburst.io/latest/security/password-passthrough.html · https://docs.starburst.io/latest/security/oauth2-passthrough.html · https://docs.starburst.io/latest/connector/snowflake.html · https://docs.starburst.io/latest/security/impersonation.html · https://docs.starburst.io/latest/connector/starburst-delta-lake-unity.html · https://docs.starburst.io/latest/connector/starburst-connectors.html · https://docs.starburst.io/starburst-galaxy/reference/access-control/ · https://docs.starburst.io/starburst-galaxy/working-with-data/create-catalogs/object-storage/s3.html · https://docs.starburst.io/security/starburst-galaxy.html · https://docs.starburst.io/latest/security/ranger-overview.html · https://docs.starburst.io/latest/security/immuta-overview.html · https://docs.starburst.io/starburst-galaxy/security-and-compliance/manage-data-access/access-control-policy-types.html

Denodo: https://community.denodo.com/docs/html/browse/9.1/en/vdp/administration/creating_data_sources_and_base_views/jdbc_sources/jdbc_sources · https://community.denodo.com/docs/html/browse/9.1/en/vdp/administration/appendix/considerations_when_configuring_data_sources_with_pass-through_credentials/considerations_when_configuring_data_sources_with_pass-through_credentials · https://community.denodo.com/docs/html/browse/9.3/en/vdp/administration/server_configuration/credentials_vault/credentials_vault · https://community.denodo.com/docs/html/browse/9.3/en/vdp/administration/databases_users_and_access_rights_in_virtual_dataport/global_security_policies/global_security_policies · https://community.denodo.com/docs/html/browse/9.3/en/vdp/administration/databases_users_and_access_rights_in_virtual_dataport/fine_grain_view_privileges/fine_grain_view_privileges · https://community.denodo.com/docs/html/browse/9.1/en/platform/administration/setting-up_kerberos_authentication/setting-up_kerberos_authentication · https://community.denodo.com/docs/html/browse/9.1/en/vdp/administration/server_configuration/server_authentication/server_authentication

Dremio: https://docs.dremio.com/current/data-sources/databases/snowflake/ · https://docs.dremio.com/current/data-sources/databases/postgres/ · https://docs.dremio.com/current/data-sources/object/s3/ · https://docs.dremio.com/current/data-sources/lakehouse-catalogs/unity/ · https://docs.dremio.com/current/data-sources/lakehouse-catalogs/iceberg-rest-catalog/ · https://docs.dremio.com/current/security/secrets-management/ · https://docs.dremio.com/current/admin/cli/encryption/ · https://docs.dremio.com/current/data-sources/lakehouse-catalogs/hive/ · https://docs.dremio.com/current/security/authentication/ · https://docs.dremio.com/current/security/rbac/ · https://docs.dremio.com/current/reference/sql/commands/row-column-policies/ · https://docs.dremio.com/25.x/security/rbac/integrations/row-column-policies-ranger/

Athena: https://docs.aws.amazon.com/athena/latest/ug/connectors-snowflake.html · https://docs.aws.amazon.com/athena/latest/ug/connectors-postgresql.html · https://docs.aws.amazon.com/athena/latest/ug/connectors-snowflake-authentication.html · https://docs.aws.amazon.com/athena/latest/ug/federated-query-iam-access.html · https://docs.aws.amazon.com/athena/latest/ug/register-connection-as-gdc.html · https://docs.aws.amazon.com/athena/latest/ug/connect-to-a-data-source-permissions.html

BigQuery: https://docs.cloud.google.com/bigquery/docs/connect-to-sql · https://docs.cloud.google.com/bigquery/docs/create-cloud-resource-connection · https://docs.cloud.google.com/bigquery/docs/omni-aws-create-connection · https://docs.cloud.google.com/bigquery/docs/working-with-connections · https://docs.cloud.google.com/bigquery/docs/spanner-federated-queries · https://docs.cloud.google.com/bigquery/docs/omni-azure-create-connection

TIBCO: https://docs.tibco.com/pub/tdv/8.3.1/doc/html/StudioHelp/StudioHelp/Ch_2_Jdbc.Setting%20Pass-Through%20Credentials%20for%20JDBC%20Client.html · https://docs.tibco.com/pub/tdv/8.8.0/doc/html/en-US/StudioHelp/Administration/About_Configuring_Kerberos_SSO_for_Data_Sources_.html · https://docs.tibco.com/pub/tdv/8.7.0/doc/html/en-US/StudioHelp/Administration/About_Row_Based_Security.html

IBM: https://www.ibm.com/docs/en/cloud-paks/cp-data/5.0.x?topic=connectors-data-virtualization-connection · https://www.ibm.com/docs/en/cloud-paks/cp-data/4.8.x?topic=connections-enabling-use-kerberos-sso-authentication · https://www.ibm.com/docs/en/cloud-paks/cp-data/5.0.x?topic=rules-masking-virtual-data · https://www.ibm.com/docs/en/db2/11.5?topic=statements-create-user-mapping · (secondary) https://www.ibm.com/support/producthub/icpdata/docs/content/SSQNUZ_current/cpd/svc/dv/data_source_configuration.html

Fabric and Power BI: https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcut-security · https://learn.microsoft.com/en-us/fabric/onelake/create-s3-shortcut · https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcuts · https://learn.microsoft.com/en-us/fabric/mirroring/snowflake · https://learn.microsoft.com/en-us/power-bi/connect-data/service-connect-snowflake · https://docs.snowflake.com/en/user-guide/oauth-powerbi

PostgreSQL: https://www.postgresql.org/docs/current/postgres-fdw.html · https://www.postgresql.org/docs/current/sql-createusermapping.html

Databricks: https://docs.databricks.com/aws/en/query-federation/postgresql · https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-privileges/privileges · https://docs.databricks.com/aws/en/query-federation/foreign-catalogs · https://docs.databricks.com/aws/en/query-federation/snowflake · https://docs.databricks.com/aws/en/query-federation/salesforce-data-cloud · https://docs.databricks.com/aws/en/tables/row-and-column-filters

Snowflake: https://docs.snowflake.com/en/user-guide/tables-iceberg-configure-catalog-integration-rest · https://docs.snowflake.com/en/user-guide/data-load-s3-config-storage-integration · https://docs.snowflake.com/en/user-guide/tables-external-intro · https://docs.snowflake.com/en/developer-guide/external-network-access/creating-using-external-network-access

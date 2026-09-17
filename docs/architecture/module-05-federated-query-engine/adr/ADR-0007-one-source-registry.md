---
title: "ADR-0007 — One source registry: the catalog manifest is the query-plane truth; ArGOS registers it by reference"
adr: 0007
module: 05-federated-query-engine
status: proposed
date: 2026-09-17
deciders: ["Arthur Keen"]
related:
  - "[[ADR-0003-authoritative-catalog-manifest|ADR-0003]] (the manifest this ADR keeps authoritative)"
  - "[[ADR-0004-identity-planes-and-policy-enforcement|ADR-0004]] (the sibling seam, Q-11)"
  - "docs/contextual-data-fabric-product-strategy-prd.md §4.2 Q2 (M11, catalog as hub), §5 M14"
  - "ArGOS PRD §9 Q-10, FR-1, FR-3 (`~/code/argos/PRD.md`); ArGOS ROADMAP R0 gate-opener"
  - "docs/research/unified-ontology-mapping-architecture.md §10 Q-1 (publish-by-reference precedent)"
---

# ADR-0007 — One source registry

**Status:** proposed 2026-09-17 — drafted for Arthur's decision as the ArGOS R0
gate-opener (its ROADMAP: "decide Q-10 *before* the first migration"). Becomes
accepted when recorded in both PRDs.

## Context

Two PRDs each want to be the place where "the sources exist":

- **CDF's catalog manifest** (ADR-0003, `deploy/catalog/manifest.json`) is the
  authoritative description of what the fabric federates: one owner per
  concept, every source keyed `kind:ref` from CSI provenance, CSI and R2RML
  artifacts pinned by path, SHA-256, generation, producer and direction,
  statistics snapshots, join keys, entitlements, capabilities (ADR-0005 D4),
  runtime-resolution and auth mode. It carries **metadata only** — credentials,
  DSNs and tokens are rejected at validation. It is deterministic, content-
  hashed, checked into git, and gated in CI by `catalog-integrity`. The planner
  reads it on every query; M11 grows it into a hub-resident catalog.
- **ArGOS FR-3** requires "an inventory of data sources, targets, databases,
  and other catalogs": id, kind, **connection reference**, **owning tenant**,
  **the projects that use it** (FR-3.1), credentials **by secret handle**
  (FR-3.2), and linkage to the extraction and transformation **runs** that
  consumed each source (FR-3.3) so provenance can answer "where did this come
  from".

ArGOS's own PRD names this "the highest-risk duplication between the two
PRDs" and frames the choice: **(a)** ArGOS's registry is master and CDF
manifests are generated projections of it per project, or **(b)** CDF's
manifest stays the query-plane truth and FR-3 registers it *by reference* — a
source-of-sources, not a source-of-truth. Constraint recorded from CDF's side:
whatever is decided must preserve the manifest's reproducible, CI-diffed
property.

The two lists overlap on identity and kind and differ everywhere else. The
manifest knows what a source *is to the query planner* (mappings, keys,
capabilities, entitlements). ArGOS needs what a source *is to the
organisation* (tenant, projects, steward, credential handle, run lineage) —
and it needs the same for targets and databases the fabric never federates
(ontology stores, AOE and r2g working databases).

## Decision

**Option (b). The catalog manifest remains the authoritative, reproducible
description of what the fabric federates. ArGOS registers manifest
*publications* by reference and derives its source records from them; it adds
the organisational facts the manifest deliberately omits; it never writes the
manifest.**

### D1 — The manifest is the query-plane truth, per fabric deployment

Nothing in ADR-0003 changes. A fabric deployment publishes a lineage of
manifest generations; each generation is content-hashed, credential-free,
validated by `catalog-integrity`, and is what the planner reads. Truth about
mappings, ownership, join keys, capabilities and entitlements lives here and
only here.

### D2 — ArGOS FR-3 holds three kinds of record

1. **Manifest publications** — one record per published generation:
   `{project, tenant, manifestRef, catalogManifestVersion, generation,
   contentHash, publishedAt, publishedBy}`. This is the reference. A project
   has one manifest lineage; a lineage belongs to one tenant.
2. **Source records derived from a publication** — projected from the
   manifest's `sources[]` at publication time, keyed by the manifest's
   `sourceId` (`kind:ref`), carrying `kind`, `ref`, the CSI/R2RML artifact
   references and hashes, and `auth.mode`, plus the **ArGOS-native fields the
   manifest does not have**: owning tenant, projects using it, steward,
   credential **handle** (the name M1's `SecretResolver` resolves — never the
   secret), and run linkage (FR-3.3).
3. **Targets, databases and catalogs the fabric does not federate** —
   ArGOS-native records with no manifest counterpart (ontology stores, AOE and
   r2g working databases, external catalogs).

### D3 — Derivation is one-way; divergence is reported, never reconciled

Manifest → ArGOS only. ArGOS must not generate, edit or patch a manifest. If
ArGOS holds a fabric source the current manifest lacks, or the manifest
declares one ArGOS has not registered, that is a **registration gap** shown to
the steward, not an automatic fix in either direction. `catalog-integrity`
stays the sole arbiter of what the planner may read.

### D4 — One shared key: `kind:ref`

The manifest's CSI-provenance-derived `sourceId` is the join key between the
two planes. ArGOS may mint its own URIs for tenancy and provenance, but every
source record derived from a publication carries the manifest `sourceId`
verbatim, and the pair `(manifest lineage, sourceId)` is unique.

### D5 — Credentials stay where ADR-0003 and CC-7 put them

The manifest carries no credential. ArGOS FR-3.2 holds the **handle**; the
fabric's M1 `SecretResolver` resolves handles at connect time. ArGOS is where
handle → tenant → steward is recorded; the fabric is where the handle turns
into a connection. Consistent with Q-11: PDP vocabulary shared, PEP distinct.

### D6 — CDF's obligation: a versioned publication contract

This is the M14 "catalog manifest contract" made concrete. CDF exposes,
read-only and versioned by `catalogManifestVersion`:

- the current manifest with its generation and content hash;
- any retained generation by id;
- the `catalog-integrity` result for a generation (the gate status M14 already
  promised).

The checked-in file remains the source; the endpoints are views of it. An
ArGOS "publish" is a pull of this contract or a CI step that posts the
publication record after `catalog-integrity` passes. Roadmap placement: the
contract-versioning item at S6, with a thin read-only surface pullable into S3.

### D7 — The decision survives the hub-resident catalog

When M11 moves catalog state into ArangoDB, a manifest generation becomes a
published snapshot of that state — still content-hashed, still what ArGOS
registers by reference. This is the same shape the unified-ontology paper
settled for mappings (Q-1: AOE owns the editable artifact; M11 ingests
*published versions*): the plane that edits publishes; the plane that consumes
registers the publication. ArGOS is a consumer of the fabric's publications,
not their editor.

## Why not (a)

- **It puts the portfolio plane on the query path.** The planner would read a
  projection whose truth lives in ArGOS, or read ArGOS itself. ADR-0004's
  standing rule is that fabric data-plane decisions never block on a
  portfolio-plane call; (a) violates it structurally.
- **It loses the property CDF asked to keep.** Registry state is not in git;
  a manifest generated from it is reproducible only if the registry is, and
  `catalog-integrity` would be diffing a build artifact against a moving
  database.
- **It creates the second inventory anyway.** A master plus generated
  projections *is* two copies with a sync process between them — the very
  duplication Q-10 exists to avoid — and every fabric deployment would take
  ArGOS as a build-time dependency.
- **It mis-assigns knowledge.** Join keys, capabilities and entitlements are
  discovered by analyzers and probes against live engines (CC-14) and curated
  by stewards in the fabric's own loop; ArGOS has no way to know them and
  should not pretend to.

## Consequences

- **ArGOS (R1):** FR-3's schema gains the publication record and the
  `sourceId` join key; the "second inventory" risk closes by construction. Q-3
  (which cluster hosts `argos_registry`) is unaffected and still open.
- **CDF (S3/S6):** the read-only publication contract (D6); optionally an
  additive `publication` block on the manifest naming the publishing project
  and identity, so the file itself carries its provenance.
- **Both PRDs:** record this answer under ArGOS §9 Q-10 and CDF product-PRD
  §4.2 / M14 when accepted; ArGOS R0's exit gate ("Q-10 decided in writing")
  is met by that recording.

## Revisit triggers

A second fabric deployment per project; a requirement for ArGOS to *author*
sources the fabric must then federate (that would be a request for a
registration-to-onboarding flow through the `add-source-*` skills, not for
manifest authorship); or M11 landing with a store that makes generations
cheaper to publish than to snapshot.

---
title: "ADR-0002 — Snowflake Cortex (and agentic sources generally) as federation legs"
adr: 0002
module: 05-federated-query-engine
status: proposed
date: 2026-07-21
deciders: ["Arthur Keen", "PJ (Paul Losiewicz)"]
related:
  - "[[ADR-0001-conceptual-query-language|ADR-0001]]"
  - "[[contextual-data-fabric-prd|PRD]] §2.3, §7.7, §10.2"
---

# ADR-0002 — Should the fabric use Snowflake Cortex when federating?

**Status:** Proposed (the standing written answer to a question customers ask
in almost every conversation; documentation-first — no code scheduled).
**Trigger:** the Snowflake sprint (PRD §7.7) made the question concrete.

## Context

Snowflake ships an AI surface — **Cortex Analyst** (natural language → SQL over
a customer-authored *semantic model*), **Cortex Search** (retrieval over
unstructured data inside Snowflake), and **Cortex Agents** (orchestration
across both). Customers reasonably ask: *"if Snowflake can already answer
English questions about Snowflake data, why does the fabric translate SPARQL
to SQL itself — shouldn't the Snowflake leg just be Cortex?"* Databricks Genie
poses the identical question for that platform. As the user framing notes,
using Cortex as a leg means the fabric must **render a query prompt** to send
to Cortex — the "agentic partition" leg already sketched in the README's
query-time diagram and in M5's scope ("agent calls where a source only exposes
an agent").

## Decision

**Default: no — the Snowflake leg is Ontop/R2RML (SPARQL→SQL), same as every
relational source. Cortex is supported *in principle* as an agentic connector
type with explicitly degraded trust semantics, built only when a customer
mandates it.**

Four reasons, in the order customers feel them:

1. **Cost & latency (the political one — PRD §2.2).** Our compiled leg costs
   ~zero tokens and milliseconds per question, forever. A Cortex leg bills
   LLM inference per question on top of warehouse compute. Rah's objection to
   Arango was *token cost*; an architecture that puts an LLM inside every
   federated leg concedes that argument at the design level. (B7/CC-6 exists
   to prove our number.)
2. **Determinism & the gate.** `make gate` before every demo requires
   reproducible legs. Cortex output can vary run-to-run; our SPARQL→SQL is
   deterministic from the mapping. An LLM leg can never sit on the mandatory
   gate path (the same reason the D1 NL front-end is pinned off in the gate —
   `CDF_NL_DISABLED`).
3. **Semantics stay in one place.** The fabric's whole argument (PRD §2.1) is
   *translate once* at the ontology. If each source's cortex re-interprets
   language independently, the N² re-interpretation problem returns wearing an
   AI badge — two cortices can legitimately read "revenue at risk" two ways
   and nothing catches the divergence. If a Cortex leg is ever built, the
   prompt is **rendered from the already-decomposed conceptual partition**
   (concepts, filters, join keys made explicit in words) — never the user's
   raw question.
4. **Provenance quality.** Our legs cite the exact executed SQL/AQL.
   Cortex Analyst does return the SQL it generates — which is precisely what
   makes it the *acceptable* agentic source, partially rescuing provenance —
   but the fabric did not compile that SQL from the mapping, so the citation
   is **attested by the source's agent, not derived by the fabric**. That is
   a different trust class and must be labeled as such.

## The agentic-connector contract (when a customer mandates Cortex/Genie)

A conforming agentic leg MUST return:
1. **structured rows** (not prose) aligned to the partition's variables,
   including the join key (`accountId`);
2. **the query it executed** (Cortex Analyst's generated SQL) for the
   citation, marked `attested` rather than `derived`;
3. **an as-of stamp** (CC-4).

Envelope semantics: attested legs carry a visible **`agent-attested` trust
class** (M7 badge); a claim supported *only* by an attested leg cannot
silently satisfy a load-bearing projection under the strict gate — concierge
mode may accept it, declared. Guardrails (CC-11) apply unchanged: row caps,
timeouts, circuit breaker; plus a token budget per leg.

Build shape (estimate, unscheduled — P3 or on customer demand): a prompt
renderer from the partition + a semantic-model mapping (the Cortex semantic
model must be *generated from the same CSI* so vocabularies can't drift) +
a response adapter + trust-class plumbing in the envelope. Roughly a
one-week package once a real customer environment exists to test against.

## What we tell customers (the two-sentence answer)

> The fabric *can* treat Cortex as a source — it's designed for sources that
> only speak natural language, and Cortex Analyst returning its generated SQL
> makes it the best-behaved of those. We default to direct SPARQL→SQL because
> it's deterministic, token-free, and the citation is the query we compiled —
> and we label agent-answered legs differently because your auditors will ask.

## Consequences

- Sprint 2 (PRD §7.7) is **unaffected**: the Friday Snowflake leg is
  Ontop/R2RML; Cortex is out of scope.
- The README's agentic-partition lane and M5's "agent calls" scope line now
  have a governing ADR instead of an implicit design.
- Revisit trigger: a customer engagement that mandates Cortex/Genie, or the
  P3 multi-source planner work (G5) — whichever comes first.

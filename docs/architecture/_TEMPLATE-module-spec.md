---
title: "Module NN — <Name> — Specification"
module: NN-<slug>
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: <name>
building_block: <Onto Extract | Query | Both>
depends_on_modules: []
depends_on_repos: []
requires_repo_enhancements: []
phase_intro: <1 | 2 | 3>
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module NN — <Name>

> One-line statement of what this module is responsible for.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
What this module owns, in one short paragraph. Why it exists as a separate module.

## 2. Scope
**In scope:** …
**Out of scope:** … (say what a reader might expect here but that lives in another module — link it)

## 3. Interfaces (inputs / outputs)
- **Consumes:** <from which modules/repos, in what shape>
- **Produces:** <to which modules, in what shape>
- **Contract:** <API/CLI/library surface, data formats — OSI/YAML, JSON envelope, etc.>

## 4. Functional requirements
Numbered, each tagged with the phase it lands in.
- **FR-1 (P1):** …
- **FR-2 (P2):** …

## 5. Non-functional requirements
Grounding/citations, cost, latency, determinism, no-data-movement — the North Star principles that bind this module.

## 6. Dependencies
- **Modules:** …
- **Repos:** … (and the required enhancement spec in `_repo-enhancements/`)

## 7. Phase mapping
What of this module ships in P1 / P2 / P3 (mirror the index table row).

## 8. Acceptance criteria / demo
How we know the P1 slice works — ideally a concrete observable (a query, an artifact, a cited answer).

## 9. Open questions
Decisions to resolve before/while building.

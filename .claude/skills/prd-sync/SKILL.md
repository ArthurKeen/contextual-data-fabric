# PRD Sync Skill

## Invocation
`/prd-sync` — audit implementation files against the PRD and write drift gaps to ArangoDB.

## Purpose
Find gaps between what the PRD says the system must do and what is actually implemented.
Every requirement must have a `file:line` evidence reference or be classified as MISSING/PARTIAL.

---

## Protocol

### Phase 0 — Locate the PRD
Read `PRD_FILE` from CLAUDE.md. If not found, search for files matching `*PRD*`, `*requirements*`, `*spec*` in `docs/`. If still not found, ask the user.

### Phase 1 — Extract requirements
Parse the PRD and extract every distinct, testable requirement. A requirement is any statement that describes what the system MUST, SHOULD, or SHALL do.

Number them: `REQ-001`, `REQ-002`, etc.

Output a table:
```
REQ-001 | The system must authenticate users via JWT | PENDING
REQ-002 | The API must return 400 for missing fields | PENDING
...
```

### Phase 2 — Audit implementation

For each requirement, search the implementation (src/, lib/, app/, api/ — wherever code lives):

```bash
grep -rn "<key term from requirement>" src/ lib/ app/ api/ 2>/dev/null | head -20
```

Classify each requirement:
- **IMPLEMENTED** — found in implementation code with `file:line` evidence
- **TEST-ONLY** — found only in test files (`*.test.*`, `*.spec.*`, `*_test.*`)
- **PARTIAL** — some parts implemented, others missing
- **MISSING** — no evidence found anywhere
- **SKIP** — infrastructure/deployment requirement, not verifiable in code

**Never mark IMPLEMENTED without a file:line reference.**

### Phase 3 — Drift report

Emit a structured report:

```
[PRD-SYNC] Drift Report — <project> — <date>

SUMMARY: X implemented | Y partial | Z missing | W test-only | V skip

IMPLEMENTED (X):
  REQ-001 src/auth/jwt.ts:42 — JWT validation middleware
  ...

PARTIAL (Y):
  REQ-007 src/api/users.ts:89 — POST /users exists but missing input validation
  Gap: field validation not present

MISSING (Z):
  REQ-012 — Rate limiting on all endpoints
  REQ-015 — Audit log for admin actions

TEST-ONLY (W):
  REQ-009 tests/auth.test.ts:33 — "should reject expired tokens" (test exists, impl missing)
```

### Phase 4 — Write to ArangoDB (skip if MCP unavailable)

For each MISSING or PARTIAL requirement, write a drift alert with **`save-drift-alert`**
(NOT a raw `upsert-document` into `drift_alerts`). This tool upserts the alert AND
links it to its project node via an `alert_from_project` edge, so drift alerts and
their projects never become orphan nodes in the memory graph:

```
Use tool: save-drift-alert
project_id: "<PROJECT_ID>"
req_id: "<REQ-NNN>"
requirement: "<requirement text>"
classification: "MISSING" | "PARTIAL"
status: "open"
evidence: "<file:line or empty>"
gap_description: "<what is missing>"
detected_at: "<ISO timestamp>"
```

It is idempotent on `<PROJECT_ID>_<REQ_ID>`: identity (`project_id`/`req_id`) is
preserved and only the fields you pass are merged on re-detection, so re-running
`/prd-sync` keeps each alert's identity and its provenance edge.

For each IMPLEMENTED requirement where a previous alert was open, close it with the
same tool (pass `status: "closed"` and the closing evidence):

```
Use tool: save-drift-alert
project_id: "<PROJECT_ID>"
req_id: "<REQ-NNN>"
status: "closed"
closed_at: "<ISO timestamp>"
closed_evidence: "<file:line>"
```

> If your MCP server predates `save-drift-alert`, reload it; as a last resort you
> can still `upsert-document` into `drift_alerts`, but that leaves the alert an
> orphan until `phase2_setup.py` next runs.

Update the project registry:

```
Use tool: upsert-document
collection_name: "project_registry"
search_fields: { "_key": "<PROJECT_ID>" }
document_data: {
  "_key": "<PROJECT_ID>",
  "project_id": "<PROJECT_ID>",
  "project_name": "<PROJECT_NAME from CLAUDE.md>",
  "prd_path": "<PRD_FILE>",
  "last_sync": "<ISO timestamp>",
  "open_gaps": <count of MISSING + PARTIAL>
}
update_data: {
  "last_sync": "<ISO timestamp>",
  "open_gaps": <count of MISSING + PARTIAL>
}
```

If MCP is unavailable: emit `[PRD-SYNC] ArangoDB unavailable — drift report is local only.` and continue.

### Phase 5 — Clear drift queue

```bash
rm -f .prd-drift-queue/*
```

### Phase 6 — Propose fixes (optional)

For each MISSING requirement, propose a concrete implementation:
- Which file should contain the implementation
- What function/class/middleware to add
- Any dependency changes needed

Do not implement without user confirmation.

---

## Key invariants
- Never claim IMPLEMENTED without `file:line` evidence. A test is not an implementation.
- Never skip Phase 2 — even if you believe the code is aligned, grep it.
- If the PRD itself is ambiguous, note the ambiguity in the drift report but do not block the audit.

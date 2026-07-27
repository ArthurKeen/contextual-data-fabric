# Pattern Search Skill

## Invocation
`/pattern-search <problem description>` — retrieve relevant solutions from shared memory BEFORE solving a problem.

## Purpose
Query `shared_patterns` for solutions other projects have already verified.
Run this before attempting any non-trivial problem from scratch.

Retrieval is **server-side hybrid**: the `pattern-search` MCP tool embeds your query, fuses semantic
(vector) + keyword (BM25) rankings via RRF, and re-ranks by graded salience (importance + recency +
usage). You pass text and get ranked results — no embeddings or AQL in the agent.

---

## Protocol

### Phase 1 — Search
Call the server-side tool with the raw problem description. Pass `project_id` (from
CLAUDE.md) so the read is logged for per-project analytics:
```
Use tool: pattern-search
query_text: "<the full problem description>"
project_id: "<PROJECT_ID from CLAUDE.md>"
limit: 8
```
Returns `{ mode: "hybrid"|"bm25", count, patterns: [ { _key, project_id, project_type,
problem_category, problem_description, solution_summary, tags, score, relevance, importance,
usage_count, ... } ] }`, already ranked. `mode:"bm25"` means the vector index/embeddings aren't
available yet (keyword-only) — still useful.

If the `pattern-search` tool is unavailable, fall back to the appendix query.

### Phase 2 — Present results
Show up to 5, highest `score` first:
```
[PATTERN-SEARCH] Found N patterns for: "<query>"  (mode: hybrid)

━━━ Pattern 1 (score 2.70 | other | project: arango-shared-memory | used 0× | 2026-07-05) ━━━
Problem: <problem_description>
Solution: <solution_summary>
Tags: <tags>   Source: <source_file>

━━━ Pattern 2 ...
```
If 0 results: `[PATTERN-SEARCH] No patterns found for "<query>". Solve it, then run /pattern-save.`

### Phase 3 — Offer application
```
Apply any of these to the current problem? [1/2/.../none]
```
Summarize how to adapt the selected pattern; note framework/data-model/constraint differences —
never blindly copy.

### Phase 4 — Reinforce on use (IMPORTANT — closes the feedback loop)
If you actually **apply** one or more of the surfaced patterns to solve the problem, record it
with a single tool call (this is the APPLY side of the read-path funnel — search records what was
*surfaced*, this records what was *reused*, and it feeds ranking so useful patterns rise over time):
```
Use tool: pattern-applied
keys: ["<applied pattern _key>", "..."]
```
Pass only the `_key`(s) you genuinely used — not every result shown. Do this as soon as you've
applied the pattern (don't defer it to session end, where it's easily forgotten). One call, no AQL.

---

## Key invariants
- A pattern is a starting point, not a guarantee. It worked in a different context.
- Always show `project_id` and `created_at` — stale patterns from very different projects may not apply.
- Only reinforce (Phase 4) patterns the user actually applies — not every result shown.
- If MCP unavailable: `[PATTERN-SEARCH] ArangoDB unavailable — proceeding without shared memory.`

## Appendix — fallback (only if the `pattern-search` tool is absent)
```
Use tool: execute-aql-query   database_name: "memory"   bind_vars: { "q": "<problem>" }
query:
FOR p IN patterns_search
  SEARCH ANALYZER(p.problem_description IN TOKENS(@q,"text_en")
    OR p.solution_summary IN TOKENS(@q,"text_en")
    OR p.tags IN TOKENS(@q,"text_en"), "text_en")
  SORT BM25(p) DESC LIMIT 8 RETURN p
```
(If even the `patterns_search` view is absent: `FOR p IN shared_patterns FILTER @kw IN p.tags ...`.)

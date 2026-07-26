"""Federated executor over a :class:`PartitionPlan` (M5 / E2).

Runs each per-source sub-query through a pluggable :class:`SourceExecutor`,
inner-joins the per-source result sets on the plan's cross-source **join keys**,
projects the query's variables, and assembles a **retrieval path** for E3/M7 to
cite. The genuinely net-new logic lives here — the join/reassembly, the
retrieval path, and the partial-failure semantics — while *how* a sub-query
becomes SQL/AQL against a live source is the adapter's job (Ontop for the
relational leg, ``arango-sparql-py`` for the AQL leg), injected as a
:class:`SourceExecutor`.

Design choices tied to the M5 spec:

- **No data movement** — each leg runs where its data lives; only result rows
  (bindings) come back and are joined in-engine.
- **Never silent omission** (FR-11) — a failed or unroutable leg is *declared*
  in the result (``partial``, ``failed_sources``, ``unavailable_vars``,
  ``unresolved``), never dropped. The caller (M7) decides partial-answer vs.
  refuse.
- **As-of stamps** (FR-12) — every retrieval step carries the source's as-of
  time when the adapter supplies one.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .types import PartitionPlan, SubQuery

# A single result row: SPARQL variable name (bare, no "?") -> value.
Binding = dict[str, Any]


@dataclass(frozen=True)
class SourceResult:
    """What a :class:`SourceExecutor` returns for one sub-query."""

    rows: tuple[Binding, ...]
    native_query: str | None = None
    """The actual SQL/AQL the adapter ran (for the retrieval path / citations)."""
    as_of: str | None = None
    """Source freshness stamp (execution time for live legs; last-ingest time
    for the Arango graph). Recorded per FR-12."""
    source_objects: tuple[str, ...] = ()
    """The physical objects the leg touched (e.g. ``"public.orders"``, a
    collection or document id) — the adapter knows them; E3/M7 cite them
    (FR-2)."""


class SourceExecutor(Protocol):
    """Runs one sub-query against a single source and returns its bindings.

    Concrete adapters: an Ontop-backed executor (SPARQL→SQL) for a relational
    source, an ``arango-sparql-py``-backed executor (SPARQL→AQL) for the graph.
    Tests supply in-memory fakes.
    """

    def execute(self, subquery: SubQuery) -> SourceResult: ...


@dataclass(frozen=True)
class RetrievalStep:
    """One leg of the retrieval path — what ran where, and whether it succeeded."""

    source_id: str
    kind: str
    sparql: str
    status: str  # "ok" | "failed"
    row_count: int = 0
    native_query: str | None = None
    as_of: str | None = None
    source_objects: tuple[str, ...] = ()
    error: str | None = None
    seeded_vars: tuple[str, ...] = ()
    """Join variables bind-joined into this leg (FR-13): the prior legs'
    distinct key rows were pushed down as a trailing ``VALUES`` clause, visible
    in :attr:`sparql`. Empty when the leg ran unseeded."""


@dataclass(frozen=True)
class FederatedResult:
    """The assembled answer plus the retrieval path that produced it."""

    bindings: tuple[Binding, ...]
    retrieval_path: tuple[RetrievalStep, ...]
    partial: bool = False
    failed_sources: tuple[str, ...] = ()
    unavailable_vars: tuple[str, ...] = ()
    """Projected variables that no *successful* leg could supply."""
    unresolved: tuple[Any, ...] = ()
    """Triples the plan could not route to any source (carried through from the
    :class:`PartitionPlan`)."""


def _bare(var: str) -> str:
    return var[1:] if var.startswith("?") else var


def _inner_join(left: list[Binding], right: list[Binding]) -> list[Binding]:
    """Inner-join two binding lists on their shared variables (SPARQL BGP
    conjunction semantics). No shared variables → cartesian product."""
    if not left or not right:
        return []
    shared = [k for k in left[0] if k in right[0]]
    index: dict[tuple, list[Binding]] = {}
    for rb in right:
        index.setdefault(tuple(rb[k] for k in shared), []).append(rb)
    out: list[Binding] = []
    for ra in left:
        for rb in index.get(tuple(ra[k] for k in shared), []):
            out.append({**rb, **ra})
    return out


def _sparql_term(value: Any) -> str:
    """Serialize a Python value as a SPARQL VALUES term (plain-literal default)."""
    if value is None:
        return "UNDEF"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _with_values(sparql: str, variables: list[str], rows: list[Binding]) -> str:
    """Append a trailing ``VALUES`` clause (SPARQL 1.1 inline data) to a SELECT.

    This is the bind-join carrier (CC-11 / FR-13): the earlier leg's join-key
    rows constrain the later leg *inside its own engine* — arango-sparql-py and
    Ontop both accept the trailing-VALUES form — so the seeded leg never
    over-fetches. The seeded SPARQL is what gets executed and therefore what
    gets *cited*, keeping the pushdown visible in the retrieval path.
    """
    var_list = " ".join(f"?{v}" for v in variables)
    row_terms = " ".join(
        "(" + " ".join(_sparql_term(r.get(v)) for v in variables) + ")" for r in rows
    )
    return f"{sparql.rstrip()}\nVALUES ({var_list}) {{ {row_terms} }}"


def _distinct_rows(rows: list[Binding], variables: list[str]) -> list[Binding]:
    seen: set[tuple[Any, ...]] = set()
    out: list[Binding] = []
    for r in rows:
        key = tuple(r.get(v) for v in variables)
        if key not in seen and any(k is not None for k in key):
            seen.add(key)
            out.append({v: r.get(v) for v in variables})
    return out


#: CC-11: engine-side bind-joins ship a *bounded* key set. Beyond the cap the
#: leg runs unseeded (correct, just less pushed-down) — the admission-control
#: layer (FR-14) is the place that turns an over-budget plan into a refusal.
SEED_CAP = 1000


def execute_plan(
    plan: PartitionPlan,
    executors: Mapping[str, SourceExecutor],
    *,
    seed_cap: int = SEED_CAP,
    max_workers: int = 8,
) -> FederatedResult:
    """Execute a partition plan and reassemble one federated result.

    Legs run in **two stages** (the locked join design, FR-13): first the
    relational legs — they carry the selective filters and the small key set —
    then the graph (arango) leg(s), **bind-joined**: the distinct join-key rows
    accumulated from the first stage's join are pushed down as a trailing
    ``VALUES`` clause, capped at ``seed_cap`` keys (CC-11).

    Within a stage, independent legs run **concurrently** on a thread pool —
    legs are I/O-bound network calls, so the GIL is released and threads
    genuinely overlap; wall clock per stage ≈ the slowest leg, not the sum.
    Retrieval-path order, join semantics, and failure semantics are identical
    to the historical sequential loop: steps are recorded in plan order, a
    failed leg is declared (never raised), and the graph stage sees exactly the
    joined relational keys. One deliberate change: a relational leg no longer
    receives seeds from an *earlier relational leg* (an incidental coupling of
    the sequential loop); the in-engine join guarantees the same bindings.
    Statistics-driven staging (M4 FR-8) replaces this heuristic in M12.

    Args:
        plan: the :class:`PartitionPlan` from :func:`cdf.query.partition_query`.
        executors: source_id → :class:`SourceExecutor`. A source with no
            executor is treated as a failed (declared) leg.
        seed_cap: maximum distinct key rows to push down per leg.
        max_workers: upper bound on concurrent legs within a stage.

    Returns:
        A :class:`FederatedResult`. When every leg succeeds and the plan is
        fully resolved, ``partial`` is ``False`` and ``bindings`` is the joined,
        projected answer; otherwise the shortfalls are declared, never hidden.
    """
    steps: list[RetrievalStep] = []
    successful: list[tuple[SubQuery, SourceResult]] = []
    failed: list[str] = []
    join_vars = {_bare(v) for v in plan.join_keys}
    accumulated: list[Binding] = []

    # Stage split (P1 heuristic, per the locked join design): relational legs
    # first, graph legs seeded second.
    ordered = sorted(plan.sub_queries, key=lambda s: s.source.kind == "arango")
    stages: list[list[SubQuery]] = [
        [sq for sq in ordered if sq.source.kind != "arango"],
        [sq for sq in ordered if sq.source.kind == "arango"],
    ]

    def _run_leg(
        prepared: tuple[SubQuery, tuple[str, ...]],
    ) -> tuple[SubQuery, RetrievalStep, SourceResult | None]:
        sq, seeded_vars = prepared
        executor = executors.get(sq.source.source_id)
        if executor is None:
            step = RetrievalStep(
                source_id=sq.source.source_id,
                kind=sq.source.kind,
                sparql=sq.sparql,
                status="failed",
                error="no executor registered for source",
            )
            return sq, step, None
        try:
            result = executor.execute(sq)
        except Exception as exc:  # noqa: BLE001 — a failed leg must be declared, not raised
            step = RetrievalStep(
                source_id=sq.source.source_id,
                kind=sq.source.kind,
                sparql=sq.sparql,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            return sq, step, None
        step = RetrievalStep(
            source_id=sq.source.source_id,
            kind=sq.source.kind,
            sparql=sq.sparql,
            status="ok",
            row_count=len(result.rows),
            native_query=result.native_query,
            as_of=result.as_of,
            source_objects=result.source_objects,
            seeded_vars=seeded_vars,
        )
        return sq, step, result

    for stage in stages:
        if not stage:
            continue
        # Bind-join: seed every leg in this stage with the join-key rows
        # already in hand from prior stages.
        prepared_legs: list[tuple[SubQuery, tuple[str, ...]]] = []
        for sq in stage:
            shared = sorted(join_vars & {_bare(v) for v in sq.variables})
            seeded_vars: tuple[str, ...] = ()
            if shared and accumulated:
                seed_rows = _distinct_rows(accumulated, shared)
                if seed_rows and len(seed_rows) <= seed_cap:
                    sq = replace(sq, sparql=_with_values(sq.sparql, shared, seed_rows))
                    seeded_vars = tuple(shared)
            prepared_legs.append((sq, seeded_vars))

        if len(prepared_legs) == 1:
            outcomes = [_run_leg(prepared_legs[0])]
        else:
            with ThreadPoolExecutor(
                max_workers=min(len(prepared_legs), max_workers)
            ) as pool:
                # pool.map preserves input order, so the retrieval path and the
                # running join below stay deterministic regardless of which leg
                # finishes first.
                outcomes = list(pool.map(_run_leg, prepared_legs))

        for sq, step, result in outcomes:
            steps.append(step)
            if result is None:
                failed.append(sq.source.source_id)
                continue
            successful.append((sq, result))
            accumulated = (
                [dict(r) for r in result.rows]
                if not accumulated
                else _inner_join(accumulated, [dict(r) for r in result.rows])
            )

    # The accumulated running join *is* the joined result.
    joined: list[Binding] = accumulated

    # Variables any *successful* leg can supply (independent of row count, so a
    # legitimately empty leg still "provides" its variables).
    available: set[str] = set()
    for sq, _result in successful:
        available.update(_bare(v) for v in sq.variables)

    projection = [_bare(v) for v in plan.projection]
    unavailable = tuple(v for v in projection if v not in available)

    if projection:
        bindings = tuple(
            {k: row[k] for k in projection if k in row} for row in joined
        )
    else:
        bindings = tuple(joined)

    partial = bool(failed) or bool(plan.unresolved) or bool(unavailable)
    return FederatedResult(
        bindings=bindings,
        retrieval_path=tuple(steps),
        partial=partial,
        failed_sources=tuple(failed),
        unavailable_vars=unavailable,
        unresolved=plan.unresolved,
    )

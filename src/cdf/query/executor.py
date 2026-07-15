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
from dataclasses import dataclass
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
    error: str | None = None


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


def execute_plan(
    plan: PartitionPlan,
    executors: Mapping[str, SourceExecutor],
) -> FederatedResult:
    """Execute a partition plan and reassemble one federated result.

    Args:
        plan: the :class:`PartitionPlan` from :func:`cdf.query.partition_query`.
        executors: source_id → :class:`SourceExecutor`. A source with no
            executor is treated as a failed (declared) leg.

    Returns:
        A :class:`FederatedResult`. When every leg succeeds and the plan is
        fully resolved, ``partial`` is ``False`` and ``bindings`` is the joined,
        projected answer; otherwise the shortfalls are declared, never hidden.
    """
    steps: list[RetrievalStep] = []
    successful: list[tuple[SubQuery, SourceResult]] = []
    failed: list[str] = []

    for sq in plan.sub_queries:
        executor = executors.get(sq.source.source_id)
        if executor is None:
            steps.append(
                RetrievalStep(
                    source_id=sq.source.source_id,
                    kind=sq.source.kind,
                    sparql=sq.sparql,
                    status="failed",
                    error="no executor registered for source",
                )
            )
            failed.append(sq.source.source_id)
            continue
        try:
            result = executor.execute(sq)
        except Exception as exc:  # noqa: BLE001 — a failed leg must be declared, not raised
            steps.append(
                RetrievalStep(
                    source_id=sq.source.source_id,
                    kind=sq.source.kind,
                    sparql=sq.sparql,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            failed.append(sq.source.source_id)
            continue
        steps.append(
            RetrievalStep(
                source_id=sq.source.source_id,
                kind=sq.source.kind,
                sparql=sq.sparql,
                status="ok",
                row_count=len(result.rows),
                native_query=result.native_query,
                as_of=result.as_of,
            )
        )
        successful.append((sq, result))

    # Join the successful legs on their shared variables.
    joined: list[Binding] | None = None
    for _sq, result in successful:
        rows = [dict(r) for r in result.rows]
        joined = rows if joined is None else _inner_join(joined, rows)
    joined = joined or []

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

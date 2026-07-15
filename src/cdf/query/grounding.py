"""Grounding & provenance envelope + cite-or-refuse gate (M5 / E3).

Wraps a :class:`~cdf.query.executor.FederatedResult` into a **grounded, cited
answer envelope** and applies the **deterministic grounding gate**: refuse
rather than present an answer that isn't fully traceable to real source data.
This is the M5→M7 boundary — E3 produces the structured, gated envelope
(bindings + citations + retrieval path + status); M7 (customer-context) adds the
natural-language answer and the citation/traversal UI on top.

The gate (M7 FR-3/FR-6, PRD "no confident-wrong-answers"):

- **grounded** — every leg succeeded and every query pattern routed; the answer
  is fully cited.
- **refused** — the answer cannot be faithfully produced: a requested
  (projected) variable has no successful source, or (in the default strict mode)
  any leg failed / any pattern was unroutable. No bindings are returned.
- **partial** — only in the opt-in concierge mode (``allow_partial=True``) and
  only when every *projected* variable is still available: the answer is
  returned but a leg failure / dropped constraint is **declared**, never hidden.

Strict mode (default) is the cite-or-refuse contract; the ``allow_partial`` knob
mirrors the deterministic-vs-concierge split without branching the caller's
code.
"""

from __future__ import annotations

from dataclasses import dataclass

from .executor import Binding, FederatedResult, RetrievalStep


@dataclass(frozen=True)
class Citation:
    """One source-backed citation for the answer (M7 FR-2)."""

    source_id: str
    kind: str
    sparql: str
    native_query: str | None = None
    source_objects: tuple[str, ...] = ()
    as_of: str | None = None
    row_count: int = 0


@dataclass(frozen=True)
class AnswerEnvelope:
    """The gated, cited envelope E3 hands to M7."""

    status: str  # "grounded" | "partial" | "refused"
    bindings: tuple[Binding, ...]
    citations: tuple[Citation, ...]
    retrieval_path: tuple[RetrievalStep, ...]
    refusal_reason: str | None = None
    failed_sources: tuple[str, ...] = ()
    unavailable_vars: tuple[str, ...] = ()
    unresolved: tuple[object, ...] = ()

    @property
    def is_grounded(self) -> bool:
        return self.status == "grounded"

    @property
    def is_refused(self) -> bool:
        return self.status == "refused"


def _citations(result: FederatedResult) -> tuple[Citation, ...]:
    return tuple(
        Citation(
            source_id=step.source_id,
            kind=step.kind,
            sparql=step.sparql,
            native_query=step.native_query,
            source_objects=step.source_objects,
            as_of=step.as_of,
            row_count=step.row_count,
        )
        for step in result.retrieval_path
        if step.status == "ok"
    )


def ground(result: FederatedResult, *, allow_partial: bool = False) -> AnswerEnvelope:
    """Apply the grounding gate to a federated result.

    Args:
        result: the :class:`FederatedResult` from
            :func:`cdf.query.execute_plan`.
        allow_partial: when ``True`` (concierge mode), return a **partial**
            answer if a leg failed or a pattern was unroutable but every
            *projected* variable is still available — with the shortfall
            declared. When ``False`` (default, strict cite-or-refuse), any such
            shortfall refuses.

    Returns:
        An :class:`AnswerEnvelope`. ``bindings`` is empty when refused.
    """
    citations = _citations(result)

    reasons: list[str] = []
    if result.unavailable_vars:
        reasons.append(
            "no source can supply requested variable(s): "
            + ", ".join(result.unavailable_vars)
        )
    if result.failed_sources:
        reasons.append("source leg(s) failed: " + ", ".join(result.failed_sources))
    if result.unresolved:
        reasons.append(
            f"{len(result.unresolved)} query pattern(s) mapped to no known source"
        )

    if not result.partial:
        return AnswerEnvelope(
            status="grounded",
            bindings=result.bindings,
            citations=citations,
            retrieval_path=result.retrieval_path,
            failed_sources=result.failed_sources,
            unavailable_vars=result.unavailable_vars,
            unresolved=result.unresolved,
        )

    # Partial: a requested column with no source is always load-bearing; and in
    # strict mode ANY shortfall refuses (a dropped constraint can silently
    # broaden the answer — we don't present that as complete).
    load_bearing = bool(result.unavailable_vars) or not allow_partial
    if load_bearing:
        return AnswerEnvelope(
            status="refused",
            bindings=(),
            citations=citations,
            retrieval_path=result.retrieval_path,
            refusal_reason="; ".join(reasons) or "answer is not fully grounded",
            failed_sources=result.failed_sources,
            unavailable_vars=result.unavailable_vars,
            unresolved=result.unresolved,
        )

    return AnswerEnvelope(
        status="partial",
        bindings=result.bindings,
        citations=citations,
        retrieval_path=result.retrieval_path,
        refusal_reason=None,
        failed_sources=result.failed_sources,
        unavailable_vars=result.unavailable_vars,
        unresolved=result.unresolved,
    )

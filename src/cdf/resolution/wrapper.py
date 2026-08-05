"""Independent CDF guards around a canonical-hub candidate resolver."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, cast

from .contracts import (
    BackendResolveRequest,
    CandidateResolver,
    ResolutionPolicy,
    ResolveEvidence,
    ResolveRequest,
    ResolveResult,
    ResolveStatus,
    finite_unit_interval,
)

_ORACLE_KEYS = frozenset(
    {
        "canonical_id",
        "expected_canonical_id",
        "gold_id",
        "ground_truth_id",
        "match_id",
        "oracle_id",
        "resolved_to",
    }
)


class GuardedResolver:
    """Enforce scope, oracle, deadline, score, and margin rules in CDF."""

    def __init__(
        self,
        backend: CandidateResolver,
        policy: ResolutionPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.policy = policy
        self._clock = clock
        # Candidate backends may wrap clients that are not safe for concurrent
        # use. The CDF-owned default wrapper therefore serializes backend calls;
        # plan-scoped runtime caching still de-duplicates observations before
        # they reach this boundary.
        self._backend_lock = threading.RLock()

    def resolve(self, request: ResolveRequest) -> ResolveResult:
        started = self._clock()

        def result(
            status: ResolveStatus,
            reason: str,
            *,
            canonical_id: str | None = None,
            score: float | None = None,
            margin: float | None = None,
            evidence: ResolveEvidence | None = None,
            candidate_account_scope: str | None = None,
        ) -> ResolveResult:
            return ResolveResult(
                status=status,
                canonical_id=canonical_id,
                reason=reason,
                score=score,
                margin=margin,
                evidence=evidence,
                candidate_account_scope=candidate_account_scope,
                deadline_at=request.deadline_at,
                elapsed_ms=max(0.0, (self._clock() - started) * 1000),
            )

        scope = request.account_scope.strip()
        if not scope:
            return result("refused", "account_scope_required")
        if not math.isfinite(request.deadline_at) or request.deadline_at <= started:
            return result("abstained", "deadline_exceeded")

        normalized_keys = {_normalized_key(key) for key in request.attributes}
        if normalized_keys & _ORACLE_KEYS or any(
            "oracle" in key or "canonical" in key for key in normalized_keys
        ):
            return result("refused", "oracle_identifier_in_input")
        if set(request.attributes) - set(self.policy.observable_fields):
            return result("refused", "non_observable_field")

        attributes: dict[str, str | int | float | bool] = {}
        for field in self.policy.observable_fields:
            if field not in request.attributes:
                continue
            value = _observable_value(request.attributes[field])
            if value is None:
                return result("refused", "non_scalar_observable_value")
            attributes[field] = value
        if not attributes:
            return result("abstained", "no_observable_fields")
        if self._clock() >= request.deadline_at:
            return result("abstained", "deadline_exceeded")

        try:
            with self._backend_lock:
                backend = self.backend.resolve(
                    BackendResolveRequest(
                        account_scope=scope,
                        attributes=attributes,
                        deadline_at=request.deadline_at,
                        request_id=request.request_id,
                    )
                )
        except Exception:
            return result("abstained", "backend_unavailable")
        if self._clock() >= request.deadline_at:
            return result("abstained", "deadline_exceeded")
        if backend.status not in {"resolved", "abstained", "refused"}:
            return result("abstained", "backend_status_invalid")

        evidence = backend.evidence if _valid_evidence(backend.evidence, self.policy) else None
        if backend.status != "resolved":
            return result(
                cast(ResolveStatus, backend.status),
                backend.reason or f"backend_{backend.status}",
                score=backend.score,
                margin=backend.margin,
                evidence=evidence,
                candidate_account_scope=backend.candidate_account_scope,
            )

        if backend.candidate_account_scope != scope:
            return result(
                "refused",
                "cross_account_candidate",
                score=backend.score,
                margin=backend.margin,
                evidence=evidence,
                candidate_account_scope=backend.candidate_account_scope,
            )
        if not backend.canonical_id or not backend.canonical_id.strip():
            return result(
                "refused",
                "candidate_canonical_id_required",
                score=backend.score,
                margin=backend.margin,
                evidence=evidence,
                candidate_account_scope=backend.candidate_account_scope,
            )
        if not finite_unit_interval(backend.score):
            return result("abstained", "backend_score_invalid")
        if not finite_unit_interval(backend.margin):
            return result("abstained", "backend_margin_invalid", score=backend.score)
        if backend.score < self.policy.resolve_threshold:
            return result(
                "abstained",
                "below_threshold",
                score=backend.score,
                margin=backend.margin,
                evidence=evidence,
                candidate_account_scope=backend.candidate_account_scope,
            )
        if backend.margin < self.policy.minimum_margin:
            return result(
                "abstained",
                "ambiguous_margin",
                score=backend.score,
                margin=backend.margin,
                evidence=evidence,
                candidate_account_scope=backend.candidate_account_scope,
            )
        if evidence is None:
            return result(
                "abstained",
                "backend_evidence_incomplete",
                score=backend.score,
                margin=backend.margin,
                candidate_account_scope=backend.candidate_account_scope,
            )
        return result(
            "resolved",
            backend.reason or "threshold_and_margin_satisfied",
            canonical_id=backend.canonical_id,
            score=backend.score,
            margin=backend.margin,
            evidence=evidence,
            candidate_account_scope=backend.candidate_account_scope,
        )

    def resolve_batch(
        self,
        requests: Sequence[ResolveRequest],
        *,
        deadline_at: float,
    ) -> tuple[ResolveResult, ...]:
        """Resolve sequentially under one absolute shared deadline."""
        return tuple(
            self.resolve(
                ResolveRequest(
                    account_scope=request.account_scope,
                    attributes=request.attributes,
                    deadline_at=min(request.deadline_at, deadline_at),
                    request_id=request.request_id,
                )
            )
            for request in requests
        )


def _normalized_key(key: Any) -> str:
    return str(key).strip().casefold().replace("-", "_").replace(".", "_")


def _observable_value(value: Any) -> str | int | float | bool | None:
    if isinstance(value, str):
        normalized = " ".join(value.split())
        return normalized or None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value
    return None


def _valid_evidence(
    evidence: ResolveEvidence | None,
    policy: ResolutionPolicy,
) -> bool:
    if (
        evidence is None
        or not evidence.profile
        or evidence.candidate_count < 1
        or not finite_unit_interval(evidence.vector_score)
        or not evidence.field_scores
    ):
        return False
    allowed = set(policy.observable_fields)
    return all(
        field.field in allowed
        and finite_unit_interval(field.similarity)
        and math.isfinite(field.weight)
        and field.weight > 0
        for field in evidence.field_scores
    )

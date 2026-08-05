"""Stable, JSON-safe contracts for canonical-hub resolution."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Protocol, TypeGuard

ResolveStatus = Literal["resolved", "abstained", "refused"]


@dataclass(frozen=True)
class ResolutionPolicy:
    """CDF's independent resolution safety policy."""

    observable_fields: tuple[str, ...]
    resolve_threshold: float = 0.88
    minimum_margin: float = 0.08

    def __post_init__(self) -> None:
        if not self.observable_fields or len(set(self.observable_fields)) != len(
            self.observable_fields
        ):
            raise ValueError("observable_fields must be non-empty and unique")
        if not 0 <= self.resolve_threshold <= 1:
            raise ValueError("resolve_threshold must be within [0, 1]")
        if not 0 <= self.minimum_margin <= 1:
            raise ValueError("minimum_margin must be within [0, 1]")


@dataclass(frozen=True)
class ResolveRequest:
    """One scoped observation with an absolute monotonic deadline."""

    account_scope: str
    attributes: Mapping[str, Any]
    deadline_at: float
    request_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


@dataclass(frozen=True)
class FieldEvidence:
    field: str
    similarity: float
    weight: float

    def to_dict(self) -> dict[str, str | float]:
        return {
            "field": self.field,
            "similarity": self.similarity,
            "weight": self.weight,
        }


@dataclass(frozen=True)
class ResolveEvidence:
    profile: str
    candidate_count: int
    field_scores: tuple[FieldEvidence, ...]
    vector_score: float
    verifier_used: bool = False
    verifier_decision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "candidate_count": self.candidate_count,
            "field_scores": [field.to_dict() for field in self.field_scores],
            "vector_score": self.vector_score,
            "verifier_used": self.verifier_used,
            "verifier_decision": self.verifier_decision,
        }


@dataclass(frozen=True)
class ResolveResult:
    """CDF-owned result contract independent of the installed AER version."""

    status: ResolveStatus
    canonical_id: str | None
    reason: str
    score: float | None
    margin: float | None
    evidence: ResolveEvidence | None
    candidate_account_scope: str | None
    deadline_at: float
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "canonical_id": self.canonical_id,
            "reason": self.reason,
            "score": self.score,
            "margin": self.margin,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
            "candidate_account_scope": self.candidate_account_scope,
            "deadline_at": self.deadline_at,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class BackendResolveRequest:
    """Sanitized request passed across the backend seam."""

    account_scope: str
    attributes: Mapping[str, str | int | float | bool]
    deadline_at: float
    request_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))


@dataclass(frozen=True)
class BackendResolveResult:
    """Backend-neutral response consumed by the independent CDF guard."""

    status: str
    canonical_id: str | None
    reason: str
    score: float | None = None
    margin: float | None = None
    evidence: ResolveEvidence | None = None
    candidate_account_scope: str | None = None


class CandidateResolver(Protocol):
    """AER or deterministic backend hidden behind the CDF guard."""

    def resolve(self, request: BackendResolveRequest) -> BackendResolveResult: ...


def finite_unit_interval(value: float | None) -> TypeGuard[float]:
    return value is not None and math.isfinite(value) and 0 <= value <= 1

"""Optional lazy adapter for the local AER WP-13 service."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .contracts import (
    BackendResolveRequest,
    BackendResolveResult,
    FieldEvidence,
    ResolveEvidence,
)


class AERCandidateResolver:
    """Map the optional AER API into CDF-owned contracts."""

    def __init__(self, service: Any, request_type: type[Any]) -> None:
        self._service = service
        self._request_type = request_type

    @classmethod
    def from_components(
        cls,
        *,
        profile: Any,
        embedder: Any,
        candidate_provider: Any,
        verifier: Any | None = None,
    ) -> AERCandidateResolver:
        """Import AER only when explicitly configuring this optional path."""
        try:
            module = import_module(
                "entity_resolution.services.fabric_canonical_hub_resolver_wp13"
            )
        except ImportError as exc:
            raise RuntimeError(
                "AER canonical-hub API is unavailable; install a released AER "
                "version containing fabric_canonical_hub_resolver_wp13"
            ) from exc
        service = module.SemanticCanonicalHubResolver(
            profile,
            embedder,
            candidate_provider,
            verifier=verifier,
        )
        return cls(service, module.ResolveRequest)

    def resolve(self, request: BackendResolveRequest) -> BackendResolveResult:
        aer_request = self._request_type(
            account_scope=request.account_scope,
            attributes=request.attributes,
            deadline_at=request.deadline_at,
            request_id=request.request_id,
        )
        aer_result = self._service.resolve(aer_request)
        evidence = _map_evidence(getattr(aer_result, "evidence", None))
        return BackendResolveResult(
            status=str(aer_result.status),
            canonical_id=aer_result.canonical_id,
            reason=str(aer_result.reason),
            score=aer_result.score,
            margin=aer_result.margin,
            evidence=evidence,
            candidate_account_scope=getattr(
                aer_result,
                "candidate_account_scope",
                None,
            ),
        )


def _map_evidence(value: Any | None) -> ResolveEvidence | None:
    if value is None:
        return None
    return ResolveEvidence(
        profile=str(value.profile),
        candidate_count=int(value.candidate_count),
        field_scores=tuple(
            FieldEvidence(
                field=str(field.field),
                similarity=float(field.similarity),
                weight=float(field.weight),
            )
            for field in value.field_scores
        ),
        vector_score=float(value.vector_score),
        verifier_used=bool(value.verifier_used),
        verifier_decision=(
            str(value.verifier_decision)
            if value.verifier_decision is not None
            else None
        ),
    )

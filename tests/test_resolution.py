"""P2.3 WP-13 CDF resolution contract and independent safety guards."""

from __future__ import annotations

import json

from cdf.resolution import (
    AERCandidateResolver,
    BackendResolveResult,
    FieldEvidence,
    GuardedResolver,
    ResolutionPolicy,
    ResolveEvidence,
    ResolveRequest,
)


def _policy() -> ResolutionPolicy:
    return ResolutionPolicy(
        observable_fields=("name", "email_domain", "country"),
        resolve_threshold=0.88,
        minimum_margin=0.08,
    )


def _evidence() -> ResolveEvidence:
    return ResolveEvidence(
        profile="fabric_canonical_hub",
        candidate_count=1,
        field_scores=(FieldEvidence("name", 1.0, 0.3),),
        vector_score=0.98,
    )


class _Backend:
    def __init__(self, result: BackendResolveResult) -> None:
        self.result = result
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return self.result


def _resolved(**overrides) -> BackendResolveResult:
    values = {
        "status": "resolved",
        "canonical_id": "canonical/northstar",
        "reason": "backend_match",
        "score": 0.98,
        "margin": 1.0,
        "evidence": _evidence(),
        "candidate_account_scope": "acct-a",
    }
    values.update(overrides)
    return BackendResolveResult(**values)


def test_wrapper_maps_resolved_result_to_json_safe_contract() -> None:
    backend = _Backend(_resolved())
    result = GuardedResolver(backend, _policy(), clock=lambda: 100.0).resolve(
        ResolveRequest(
            "acct-a",
            {"name": "Northstar", "country": "US"},
            deadline_at=101.0,
        )
    )
    assert result.status == "resolved"
    assert result.canonical_id == "canonical/northstar"
    assert backend.requests[0].attributes == {"name": "Northstar", "country": "US"}
    assert json.loads(json.dumps(result.to_dict()))["evidence"]["profile"] == (
        "fabric_canonical_hub"
    )


def test_wrapper_refuses_scope_or_oracle_breaches_before_resolution() -> None:
    backend = _Backend(_resolved())
    wrapper = GuardedResolver(backend, _policy(), clock=lambda: 100.0)
    oracle = wrapper.resolve(
        ResolveRequest(
            "acct-a",
            {"name": "Northstar", "canonical_id": "oracle/must-not-pass"},
            deadline_at=101.0,
        )
    )
    missing_scope = wrapper.resolve(
        ResolveRequest(" ", {"name": "Northstar"}, deadline_at=101.0)
    )
    cross_scope = GuardedResolver(
        _Backend(_resolved(candidate_account_scope="acct-b")),
        _policy(),
        clock=lambda: 100.0,
    ).resolve(ResolveRequest("acct-a", {"name": "Northstar"}, deadline_at=101.0))
    assert (oracle.status, oracle.reason) == ("refused", "oracle_identifier_in_input")
    assert (missing_scope.status, missing_scope.reason) == (
        "refused",
        "account_scope_required",
    )
    assert (cross_scope.status, cross_scope.reason) == (
        "refused",
        "cross_account_candidate",
    )
    assert not backend.requests


def test_wrapper_reapplies_canonical_threshold_margin_and_evidence_gates() -> None:
    request = ResolveRequest("acct-a", {"name": "Northstar"}, deadline_at=101.0)
    cases = (
        (_resolved(canonical_id=None), "refused", "candidate_canonical_id_required"),
        (_resolved(score=0.87), "abstained", "below_threshold"),
        (_resolved(margin=0.07), "abstained", "ambiguous_margin"),
        (_resolved(evidence=None), "abstained", "backend_evidence_incomplete"),
    )
    for backend_result, status, reason in cases:
        result = GuardedResolver(
            _Backend(backend_result),
            _policy(),
            clock=lambda: 100.0,
        ).resolve(request)
        assert (result.status, result.reason) == (status, reason)


def test_deadline_and_batch_share_one_absolute_budget() -> None:
    backend = _Backend(_resolved())
    wrapper = GuardedResolver(backend, _policy(), clock=lambda: 100.0)
    expired = wrapper.resolve(
        ResolveRequest("acct-a", {"name": "Late"}, deadline_at=100.0)
    )
    batch = wrapper.resolve_batch(
        (
            ResolveRequest("acct-a", {"name": "One"}, deadline_at=105.0),
            ResolveRequest("acct-a", {"name": "Two"}, deadline_at=106.0),
        ),
        deadline_at=101.0,
    )
    assert (expired.status, expired.reason) == ("abstained", "deadline_exceeded")
    assert all(result.deadline_at == 101.0 for result in batch)
    assert len(backend.requests) == 2


class _AERRequest:
    def __init__(self, **values) -> None:
        self.values = values


class _AERService:
    def resolve(self, request):
        assert request.values["account_scope"] == "acct-a"
        return type(
            "AERResult",
            (),
            {
                "status": "resolved",
                "canonical_id": "canonical/northstar",
                "reason": "matched",
                "score": 0.98,
                "margin": 1.0,
                "candidate_account_scope": "acct-a",
                "evidence": type(
                    "AEREvidence",
                    (),
                    {
                        "profile": "fabric_canonical_hub",
                        "candidate_count": 1,
                        "field_scores": (
                            type(
                                "AERField",
                                (),
                                {"field": "name", "similarity": 1.0, "weight": 0.3},
                            )(),
                        ),
                        "vector_score": 0.98,
                        "verifier_used": False,
                        "verifier_decision": None,
                    },
                )(),
            },
        )()


def test_lazy_aer_adapter_maps_without_importing_aer_at_core_import_time() -> None:
    adapter = AERCandidateResolver(_AERService(), _AERRequest)
    result = GuardedResolver(adapter, _policy(), clock=lambda: 100.0).resolve(
        ResolveRequest("acct-a", {"name": "Northstar"}, deadline_at=101.0)
    )
    assert result.status == "resolved"
    assert result.evidence is not None
    assert result.evidence.vector_score == 0.98

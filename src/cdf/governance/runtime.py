"""Execution and postflight helpers for governed rows and masking."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from .contracts import (
    AuthorizationEvent,
    AuthorizationFailure,
    AuthorizationRefusal,
    MaskingKeyResolver,
    PlanAuthorization,
)


def authorization_events_for_source(
    authorization: PlanAuthorization,
    source_id: str,
    *,
    phase: str = "execution",
) -> tuple[AuthorizationEvent, ...]:
    return tuple(
        AuthorizationEvent(
            phase="execution" if phase == "execution" else "postflight",
            source_id=item.source_id if item.disclose_source else None,
            resource_type=item.resource_type if item.disclose_source else "withheld",
            resource_id=item.resource_id if item.disclose_source else "withheld",
            action=item.action,
            policy_ids=item.policy_ids,
            reason=item.reason,
        )
        for item in authorization.decisions
        if item.source_id == source_id
    )


def verify_authorized_rows(
    authorization: PlanAuthorization,
    source_id: str,
    rows: tuple[dict[str, Any], ...],
) -> None:
    """Treat any missing/mismatched scoped row as a fail-closed source violation."""
    constraints = tuple(
        dict.fromkeys(
            (
                row.binding_variable,
                row.expected_value,
                decision.policy_ids,
            )
            for decision in authorization.decisions
            if decision.source_id == source_id
            for row in decision.row_constraints
        )
    )
    for binding in rows:
        for variable, expected, policy_ids in constraints:
            if variable not in binding or binding[variable] != expected:
                raise AuthorizationFailure(
                    AuthorizationRefusal(
                        code="row_scope_violation",
                        phase="execution",
                        refusal_class="cross_tenant_policy_violation",
                        message="source returned a row outside the authorized scope",
                        source_id=source_id,
                        resource_type="row_constraint",
                        resource_id=variable,
                        policy_ids=policy_ids,
                    )
                )


def postflight_refusal(
    before: PlanAuthorization,
    after: PlanAuthorization | None,
) -> AuthorizationRefusal | None:
    if after is None:
        return AuthorizationRefusal(
            code="authorization_evidence_missing",
            phase="postflight",
            refusal_class="policy_evidence_missing",
            message="postflight authorization evidence is missing",
            policy_ids=before.policy_ids,
        )
    if before.context_fingerprint != after.context_fingerprint:
        return AuthorizationRefusal(
            code="authorization_context_changed",
            phase="postflight",
            refusal_class="policy_changed",
            message="request policy context changed during execution",
            policy_ids=before.policy_ids,
        )
    if before.decision_id != after.decision_id:
        return AuthorizationRefusal(
            code="authorization_decision_changed",
            phase="postflight",
            refusal_class="policy_changed",
            message="authorization decision changed during execution",
            policy_ids=tuple(dict.fromkeys((*before.policy_ids, *after.policy_ids))),
        )
    if after.denied:
        return AuthorizationRefusal(
            code="authorization_denied_postflight",
            phase="postflight",
            refusal_class="policy_denied",
            message="postflight authorization denied the answer",
            policy_ids=after.policy_ids,
        )
    return None


def mask_bindings(
    bindings: tuple[dict[str, Any], ...],
    authorization: PlanAuthorization,
    key_resolver: MaskingKeyResolver | None,
) -> tuple[tuple[dict[str, Any], ...], AuthorizationRefusal | None]:
    """Apply projection masks after joins; HMAC is keyed or refused."""
    result = [dict(row) for row in bindings]
    for rule in authorization.masking_rules:
        if rule.mode == "hmac":
            key = key_resolver.resolve(rule.policy_ids) if key_resolver is not None else None
            if key is None:
                return (), AuthorizationRefusal(
                    code="masking_key_unavailable",
                    phase="postflight",
                    refusal_class="policy_evidence_missing",
                    message="required HMAC masking key is unavailable",
                    policy_ids=rule.policy_ids,
                )
        else:
            key = None
        for row in result:
            if rule.variable not in row:
                continue
            if rule.mode == "redact":
                row[rule.variable] = "[REDACTED]"
            elif rule.mode == "hmac":
                assert key is not None
                value = str(row[rule.variable]).encode("utf-8")
                digest = hmac.new(key.reveal(), value, hashlib.sha256).hexdigest()
                row[rule.variable] = f"hmac-sha256:{digest}"
            elif rule.mode == "drop":
                row.pop(rule.variable, None)
    return tuple(result), None

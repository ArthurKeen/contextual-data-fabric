"""Frozen, JSON-safe authorization contracts for the query plane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from cdf.auth import RequestContext
from cdf.auth.contracts import SafeClaim

PolicyAction = Literal["allow", "rewrite", "deny"]
MaskMode = Literal["none", "redact", "hmac", "drop"]
AuthorizationPhase = Literal["preflight", "execution", "postflight", "introspection"]


@dataclass(frozen=True)
class ResourceRequest:
    source_id: str
    resource_type: str
    resource_id: str
    usage: str
    variable: str | None = None
    optional: bool = False
    source_auth_mode: Literal["service", "delegated"] = "service"


@dataclass(frozen=True)
class RowConstraint:
    binding_variable: str
    principal_attribute: str
    expected_value: SafeClaim


@dataclass(frozen=True)
class MaskingRule:
    variable: str
    mode: MaskMode
    policy_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResourceDecision:
    source_id: str
    resource_type: str
    resource_id: str
    usage: str
    action: PolicyAction
    policy_ids: tuple[str, ...] = ()
    classification: str = "internal"
    mask: MaskMode = "none"
    row_constraints: tuple[RowConstraint, ...] = ()
    disclose_source: bool = True
    reason: str | None = None
    variable: str | None = None
    optional: bool = False


@dataclass(frozen=True)
class PlanAuthorization:
    decision_id: str
    catalog_generation: str | None
    context_fingerprint: str
    decisions: tuple[ResourceDecision, ...]
    masking_rules: tuple[MaskingRule, ...] = ()
    withheld_sources: tuple[str, ...] = ()
    policy_ids: tuple[str, ...] = ()

    @property
    def denied(self) -> bool:
        return any(item.action == "deny" for item in self.decisions)


@dataclass(frozen=True)
class AuthorizationEvent:
    phase: AuthorizationPhase
    source_id: str | None
    resource_type: str
    resource_id: str
    action: PolicyAction
    policy_ids: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class AuthorizationRefusal:
    code: str
    phase: AuthorizationPhase
    refusal_class: str
    message: str
    source_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    policy_ids: tuple[str, ...] = ()


class AuthorizationFailure(PermissionError):
    """Fail-closed authorization failure carrying a public-safe refusal."""

    def __init__(self, refusal: AuthorizationRefusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


class PolicyDecisionPoint(Protocol):
    """Backend-neutral PDP contract used at preflight and postflight."""

    def authorize(
        self,
        resources: tuple[ResourceRequest, ...],
        context: RequestContext,
        *,
        catalog_generation: str | None,
    ) -> PlanAuthorization: ...


class SecretMaskingKey:
    """Opaque HMAC material that cannot leak through repr/serialization."""

    __slots__ = ("__value",)

    def __init__(self, value: bytes | str) -> None:
        encoded = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if len(encoded) < 16:
            raise ValueError("masking key must be at least 16 bytes")
        self.__value = encoded

    def reveal(self) -> bytes:
        return self.__value

    def __repr__(self) -> str:
        return "SecretMaskingKey([REDACTED])"

    __str__ = __repr__


class MaskingKeyResolver(Protocol):
    def resolve(self, policy_ids: tuple[str, ...]) -> SecretMaskingKey | None: ...


def json_safe_value(value: Any) -> SafeClaim:
    """Narrow a policy context value to the primitive JSON-safe claim contract."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("policy values must be JSON-safe primitives")

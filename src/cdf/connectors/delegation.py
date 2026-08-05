"""Secret-safe source delegation contracts (WP-15/WP-17 baseline)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from cdf.auth import AuthenticatedPrincipal, RequestContext
from cdf.auth.contracts import normalize_identifier

SourceAuthMode = Literal["service", "delegated"]


class DelegationError(PermissionError):
    """A safe fail-closed source delegation failure."""


class SecretMaterial:
    """Opaque short-lived material that never reveals itself via str/repr."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("source identity secret must be non-empty")
        self.__value = value

    def reveal(self) -> str:
        """Return the material only to a source adapter at the execution edge."""
        return self.__value

    def __repr__(self) -> str:
        return "SecretMaterial([REDACTED])"

    __str__ = __repr__


@dataclass(frozen=True)
class BaseSourceIdentity:
    """Operator-owned base identity reference used for an exchange."""

    source_id: str
    identity_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_id",
            normalize_identifier(self.source_id, "source_id"),
        )
        object.__setattr__(
            self,
            "identity_id",
            normalize_identifier(self.identity_id, "identity_id"),
        )


@dataclass(frozen=True)
class SourceIdentity:
    """Short-lived delegated identity. Secret material is opaque and repr-safe."""

    source_id: str
    subject: str
    expires_at: datetime
    scheme: str
    material: SecretMaterial = field(init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        source_id: str,
        subject: str,
        expires_at: datetime,
        scheme: str,
        token: str,
    ) -> None:
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "scheme", scheme)
        object.__setattr__(self, "material", SecretMaterial(token))
        self.__post_init__()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_id",
            normalize_identifier(self.source_id, "source_id"),
        )
        object.__setattr__(
            self,
            "subject",
            normalize_identifier(self.subject, "source subject"),
        )
        object.__setattr__(
            self,
            "scheme",
            normalize_identifier(self.scheme, "source identity scheme"),
        )
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("source identity expiry must be absolute")


class DelegationBroker(Protocol):
    """Exchange query authority for one source-specific short-lived identity."""

    def exchange(
        self,
        principal: AuthenticatedPrincipal,
        source_id: str,
        base_identity: BaseSourceIdentity,
        *,
        deadline: datetime,
    ) -> SourceIdentity: ...


@dataclass(frozen=True)
class SourceExecutionContext:
    """Explicit request/delegation context delivered to an aware executor."""

    request: RequestContext
    source_id: str
    auth_mode: SourceAuthMode
    identity: SourceIdentity | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_id",
            normalize_identifier(self.source_id, "source_id"),
        )
        if self.auth_mode not in ("service", "delegated"):
            raise DelegationError("source execution auth mode is invalid")
        if self.auth_mode == "delegated" and self.identity is None:
            raise DelegationError("delegated source execution requires an identity")
        if self.auth_mode == "service" and self.identity is not None:
            raise DelegationError("service source execution cannot carry delegated identity")
        if self.identity is not None and self.identity.source_id != self.source_id:
            raise DelegationError("delegated identity belongs to another source")

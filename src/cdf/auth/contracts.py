"""Immutable, secret-free identity contracts for the query plane."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Literal
from uuid import uuid4

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,255}$")
_REQUEST_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/~-]{0,127}$")
_PURPOSE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _./:~-]{0,127}$")
_FORBIDDEN_CLAIM_PARTS = ("token", "secret", "password", "credential", "authorization")
SafeClaim = str | int | float | bool | None
AuthenticationMethod = Literal["oidc", "mcp", "anonymous-dev"]


class AuthenticationError(ValueError):
    """A safe authentication failure suitable for an edge response."""


def normalize_identifier(value: Any, name: str) -> str:
    """Normalize a required identity value and reject control/ambiguous forms."""
    if not isinstance(value, str):
        raise AuthenticationError(f"{name} must be a string")
    normalized = value.strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise AuthenticationError(f"{name} is invalid")
    return normalized


def normalize_optional_identifier(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return normalize_identifier(value, name)


def normalize_string_set(
    value: Any,
    name: str,
    *,
    space_delimited: bool = False,
) -> tuple[str, ...]:
    """Normalize a claim into a stable, duplicate-free tuple."""
    if value is None:
        return ()
    if space_delimited and isinstance(value, str):
        raw = value.split()
    elif isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raise AuthenticationError(f"{name} must be a string or string array")
    normalized = [normalize_identifier(item, f"{name} entry") for item in raw]
    return tuple(dict.fromkeys(normalized))


def safe_claim_subset(
    claims: Mapping[str, Any],
    allowed_names: tuple[str, ...],
) -> tuple[tuple[str, SafeClaim], ...]:
    """Copy only explicitly allowed primitive claims into immutable storage."""
    result: list[tuple[str, SafeClaim]] = []
    for name in allowed_names:
        normalized_name = normalize_identifier(name, "safe claim name")
        folded = normalized_name.casefold()
        if any(part in folded for part in _FORBIDDEN_CLAIM_PARTS):
            raise AuthenticationError(f"safe claim name {normalized_name!r} is forbidden")
        if normalized_name not in claims:
            continue
        value = claims[normalized_name]
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise AuthenticationError(
                f"safe claim {normalized_name!r} must contain a primitive value"
            )
        if isinstance(value, float) and not isfinite(value):
            raise AuthenticationError(f"safe claim {normalized_name!r} is invalid")
        if isinstance(value, str):
            value = value.strip()
            if len(value) > 512 or any(ord(char) < 32 for char in value):
                raise AuthenticationError(f"safe claim {normalized_name!r} is invalid")
        result.append((normalized_name, value))
    return tuple(result)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Verified asker identity. The stable key is the issuer/subject pair."""

    issuer: str
    subject: str
    client_id: str | None = None
    scopes: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    tenant: str | None = None
    claims: tuple[tuple[str, SafeClaim], ...] = ()
    authentication_method: AuthenticationMethod = "oidc"

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer", normalize_identifier(self.issuer, "issuer"))
        object.__setattr__(self, "subject", normalize_identifier(self.subject, "subject"))
        object.__setattr__(
            self,
            "client_id",
            normalize_optional_identifier(self.client_id, "client_id"),
        )
        object.__setattr__(self, "scopes", normalize_string_set(self.scopes, "scopes"))
        object.__setattr__(self, "roles", normalize_string_set(self.roles, "roles"))
        object.__setattr__(self, "groups", normalize_string_set(self.groups, "groups"))
        object.__setattr__(
            self,
            "tenant",
            normalize_optional_identifier(self.tenant, "tenant"),
        )
        claims = tuple(self.claims)
        for claim_name, claim_value in claims:
            safe_claim_subset({claim_name: claim_value}, (claim_name,))
        object.__setattr__(self, "claims", claims)
        if self.authentication_method not in ("oidc", "mcp", "anonymous-dev"):
            raise AuthenticationError("authentication_method is invalid")

    @property
    def principal_key(self) -> str:
        return f"{self.issuer}|{self.subject}"


ANONYMOUS_DEV_PRINCIPAL = AuthenticatedPrincipal(
    issuer="cdf:dev",
    subject="anonymous",
    authentication_method="anonymous-dev",
)


@dataclass(frozen=True)
class RequestMetadata:
    """Safe request metadata permitted in envelopes and telemetry."""

    request_id: str
    trace_id: str
    purpose: str | None
    tenant: str | None
    principal_key: str
    identity_plane: Literal["query"] = "query"


@dataclass(frozen=True)
class RequestContext:
    """Immutable query-plane authority and request bounds.

    A bearer token is deliberately not a field. Callers must pass only a
    normalized principal created after verification.
    """

    principal: AuthenticatedPrincipal
    request_id: str
    trace_id: str
    purpose: str | None
    deadline: datetime
    identity_plane: Literal["query"] = "query"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_id",
            normalize_request_identifier(self.request_id, "request_id"),
        )
        object.__setattr__(
            self,
            "trace_id",
            normalize_request_identifier(self.trace_id, "trace_id"),
        )
        object.__setattr__(self, "purpose", normalize_purpose(self.purpose))
        if self.deadline.tzinfo is None or self.deadline.utcoffset() is None:
            raise ValueError("deadline must be an absolute timezone-aware datetime")
        object.__setattr__(self, "deadline", self.deadline.astimezone(timezone.utc))

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.deadline.astimezone(timezone.utc)

    def safe_metadata(self) -> RequestMetadata:
        return RequestMetadata(
            request_id=self.request_id,
            trace_id=self.trace_id,
            purpose=self.purpose,
            tenant=self.principal.tenant,
            principal_key=self.principal.principal_key,
        )


def normalize_request_identifier(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not _REQUEST_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{name} is invalid")
    return normalized


def normalize_purpose(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("purpose must be a string")
    normalized = " ".join(value.split())
    if not _PURPOSE.fullmatch(normalized):
        raise ValueError("purpose is invalid")
    return normalized


def anonymous_request_context(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    purpose: str | None = None,
    timeout: timedelta = timedelta(seconds=30),
) -> RequestContext:
    """Create the explicit backward-compatible local-development context."""
    normalized_request_id = request_id or uuid4().hex
    return RequestContext(
        principal=ANONYMOUS_DEV_PRINCIPAL,
        request_id=normalized_request_id,
        trace_id=trace_id or normalized_request_id,
        purpose=normalize_purpose(purpose),
        deadline=datetime.now(timezone.utc) + timeout,
    )

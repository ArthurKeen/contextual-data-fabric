"""Optional generic OIDC JWT verification with bounded JWKS behavior."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from time import time
from typing import Any

from .contracts import (
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthenticationMethod,
    normalize_identifier,
    normalize_optional_identifier,
    normalize_string_set,
    safe_claim_subset,
)

Decoder = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class OIDCVerifierConfig:
    issuer: str
    audience: str | tuple[str, ...]
    algorithms: tuple[str, ...] = ("RS256",)
    jwks_uri: str | None = None
    jwks_timeout_seconds: float = 3.0
    jwks_cache_seconds: float = 300.0
    jwks_max_cached_keys: int = 16
    leeway_seconds: float = 30.0
    tenant_claim: str = "tenant"
    groups_claim: str = "groups"
    roles_claim: str = "roles"
    scopes_claim: str = "scope"
    client_claims: tuple[str, ...] = ("azp", "client_id")
    safe_claim_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer", normalize_identifier(self.issuer, "issuer"))
        audiences = (
            (self.audience,) if isinstance(self.audience, str) else tuple(self.audience)
        )
        if not audiences:
            raise ValueError("at least one OIDC audience is required")
        object.__setattr__(
            self,
            "audience",
            tuple(normalize_identifier(audience, "audience") for audience in audiences),
        )
        algorithms = tuple(self.algorithms)
        if not algorithms:
            raise ValueError("at least one JWT algorithm is required")
        for algorithm in algorithms:
            normalized = normalize_identifier(algorithm, "algorithm")
            if normalized.casefold() == "none":
                raise ValueError("the unsigned JWT algorithm is forbidden")
        object.__setattr__(self, "algorithms", algorithms)
        for attribute in (
            "tenant_claim",
            "groups_claim",
            "roles_claim",
            "scopes_claim",
        ):
            object.__setattr__(
                self,
                attribute,
                normalize_identifier(getattr(self, attribute), attribute),
            )
        object.__setattr__(
            self,
            "client_claims",
            tuple(
                normalize_identifier(name, "client claim name")
                for name in self.client_claims
            ),
        )
        safe_claim_subset({}, tuple(self.safe_claim_names))
        object.__setattr__(self, "safe_claim_names", tuple(self.safe_claim_names))
        if not 0 <= self.leeway_seconds <= 300:
            raise ValueError("OIDC leeway must be between 0 and 300 seconds")
        if self.jwks_uri is None:
            return
        if not self.jwks_uri.startswith("https://"):
            raise ValueError("jwks_uri must use HTTPS")
        if not 0.1 <= self.jwks_timeout_seconds <= 10:
            raise ValueError("JWKS timeout must be between 0.1 and 10 seconds")
        if not 1 <= self.jwks_cache_seconds <= 3600:
            raise ValueError("JWKS cache lifetime must be between 1 and 3600 seconds")
        if not 1 <= self.jwks_max_cached_keys <= 64:
            raise ValueError("JWKS key cache must contain between 1 and 64 entries")

    @property
    def audiences(self) -> tuple[str, ...]:
        return (self.audience,) if isinstance(self.audience, str) else tuple(self.audience)


class OIDCVerifier:
    """Verify a bearer JWT and emit only a normalized, secret-free principal."""

    def __init__(
        self,
        config: OIDCVerifierConfig,
        *,
        signing_key: Any | None = None,
        decoder: Decoder | None = None,
    ) -> None:
        if signing_key is None and config.jwks_uri is None:
            raise ValueError("configure signing_key or jwks_uri")
        self.config = config
        self._signing_key = signing_key
        self._decoder = decoder
        self._jwk_client: Any | None = None
        if signing_key is None:
            try:
                from jwt import PyJWKClient
            except ModuleNotFoundError as exc:  # pragma: no cover - optional extra
                raise RuntimeError('install the optional "auth" dependency') from exc
            assert config.jwks_uri is not None
            self._jwk_client = PyJWKClient(
                config.jwks_uri,
                cache_keys=True,
                max_cached_keys=config.jwks_max_cached_keys,
                cache_jwk_set=True,
                lifespan=config.jwks_cache_seconds,
                timeout=config.jwks_timeout_seconds,
            )

    def verify(self, token: str) -> AuthenticatedPrincipal:
        if not isinstance(token, str) or not token or len(token) > 16_384:
            raise AuthenticationError("bearer token is invalid")
        try:
            import jwt
        except ModuleNotFoundError as exc:  # pragma: no cover - optional extra
            raise RuntimeError('install the optional "auth" dependency') from exc

        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            if not isinstance(algorithm, str) or algorithm not in self.config.algorithms:
                raise AuthenticationError("bearer token algorithm is not allowed")
            key = self._signing_key
            if key is None:
                assert self._jwk_client is not None
                key = self._jwk_client.get_signing_key_from_jwt(token).key
            decode = self._decoder or jwt.decode
            claims = decode(
                token,
                key=key,
                algorithms=list(self.config.algorithms),
                audience=list(self.config.audiences),
                issuer=self.config.issuer,
                leeway=self.config.leeway_seconds,
                options={
                    "require": ["exp", "iat", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError("bearer token validation failed") from exc
        if not isinstance(claims, Mapping):
            raise AuthenticationError("bearer token claims are invalid")
        _validate_registered_claims(claims, self.config)
        return principal_from_claims(
            claims,
            issuer=self.config.issuer,
            client_claims=self.config.client_claims,
            scopes_claim=self.config.scopes_claim,
            roles_claim=self.config.roles_claim,
            groups_claim=self.config.groups_claim,
            tenant_claim=self.config.tenant_claim,
            safe_claim_names=self.config.safe_claim_names,
            authentication_method="oidc",
        )


def principal_from_claims(
    claims: Mapping[str, Any],
    *,
    issuer: str | None = None,
    client_id: str | None = None,
    scopes: Sequence[str] | None = None,
    client_claims: tuple[str, ...] = ("azp", "client_id"),
    scopes_claim: str = "scope",
    roles_claim: str = "roles",
    groups_claim: str = "groups",
    tenant_claim: str = "tenant",
    safe_claim_names: tuple[str, ...] = (),
    authentication_method: AuthenticationMethod = "oidc",
) -> AuthenticatedPrincipal:
    """Normalize claims already validated by an OIDC/MCP resource server."""
    claim_issuer = normalize_identifier(issuer or claims.get("iss"), "issuer")
    subject = normalize_identifier(claims.get("sub"), "subject")
    normalized_client = normalize_optional_identifier(client_id, "client_id")
    if normalized_client is None:
        for name in client_claims:
            candidate = claims.get(name)
            if candidate is not None:
                normalized_client = normalize_optional_identifier(candidate, name)
                break
    normalized_scopes = (
        normalize_string_set(list(scopes), "scopes")
        if scopes is not None
        else normalize_string_set(claims.get(scopes_claim), scopes_claim, space_delimited=True)
    )
    return AuthenticatedPrincipal(
        issuer=claim_issuer,
        subject=subject,
        client_id=normalized_client,
        scopes=normalized_scopes,
        roles=normalize_string_set(claims.get(roles_claim), roles_claim),
        groups=normalize_string_set(claims.get(groups_claim), groups_claim),
        tenant=normalize_optional_identifier(claims.get(tenant_claim), tenant_claim),
        claims=safe_claim_subset(claims, safe_claim_names),
        authentication_method=authentication_method,
    )


def _validate_registered_claims(
    claims: Mapping[str, Any],
    config: OIDCVerifierConfig,
) -> None:
    """Recheck registered claims so an injected decoder cannot weaken policy."""
    if claims.get("iss") != config.issuer:
        raise AuthenticationError("bearer token issuer is invalid")
    audience = claims.get("aud")
    if isinstance(audience, str):
        audiences = (audience,)
    elif isinstance(audience, (list, tuple)) and all(
        isinstance(item, str) for item in audience
    ):
        audiences = tuple(audience)
    else:
        raise AuthenticationError("bearer token audience is invalid")
    if not set(audiences).intersection(config.audiences):
        raise AuthenticationError("bearer token audience is invalid")

    now = time()
    leeway = config.leeway_seconds
    expires_at = _numeric_date(claims.get("exp"), "exp")
    issued_at = _numeric_date(claims.get("iat"), "iat")
    not_before = (
        _numeric_date(claims["nbf"], "nbf") if "nbf" in claims else None
    )
    if expires_at <= now - leeway:
        raise AuthenticationError("bearer token is expired")
    if issued_at > now + leeway:
        raise AuthenticationError("bearer token issued-at time is in the future")
    if not_before is not None and not_before > now + leeway:
        raise AuthenticationError("bearer token is not active")
    if issued_at >= expires_at:
        raise AuthenticationError("bearer token time range is invalid")
    if not_before is not None and not_before >= expires_at:
        raise AuthenticationError("bearer token time range is invalid")


def _numeric_date(value: Any, name: str) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise AuthenticationError(f"bearer token {name} is invalid")
        return value.timestamp()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthenticationError(f"bearer token {name} is invalid")
    parsed = float(value)
    if not isfinite(parsed):
        raise AuthenticationError(f"bearer token {name} is invalid")
    return parsed

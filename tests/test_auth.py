"""OIDC verification and immutable query identity contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from cdf.auth import (
    AuthenticatedPrincipal,
    AuthenticationError,
    OIDCVerifier,
    OIDCVerifierConfig,
    RequestContext,
)
from cdf.connectors import SourceIdentity

ISSUER = "https://idp.example/tenant"
AUDIENCE = "cdf-api"
HMAC_KEY = "shared-test-secret-with-32-bytes-minimum"


@pytest.fixture(scope="module")
def rsa_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def _claims(**overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "azp": "agent-client",
        "exp": now + timedelta(minutes=5),
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "scope": "fabric.query profile",
        "groups": ["csm", "readers"],
        "roles": ["analyst"],
        "tenant": "customer-a",
        "email": "not-copied@example.com",
        "email_verified": True,
    }
    claims.update(overrides)
    return claims


def _verifier(public_key, **config):
    return OIDCVerifier(
        OIDCVerifierConfig(
            issuer=ISSUER,
            audience=AUDIENCE,
            safe_claim_names=("email_verified",),
            **config,
        ),
        signing_key=public_key,
    )


def test_oidc_verifies_and_strictly_normalizes_claims(rsa_keys) -> None:
    private, public = rsa_keys
    principal = _verifier(public).verify(jwt.encode(_claims(), private, algorithm="RS256"))

    assert principal.principal_key == f"{ISSUER}|user-123"
    assert principal.client_id == "agent-client"
    assert principal.scopes == ("fabric.query", "profile")
    assert principal.groups == ("csm", "readers")
    assert principal.roles == ("analyst",)
    assert principal.tenant == "customer-a"
    assert principal.claims == (("email_verified", True),)
    assert "not-copied@example.com" not in repr(principal)


@pytest.mark.parametrize(
    ("claim_overrides", "config_overrides"),
    [
        ({"iss": "https://other.example"}, {}),
        ({"aud": "other-api"}, {}),
        ({"exp": datetime.now(timezone.utc) - timedelta(minutes=1)}, {}),
        ({"sub": None}, {}),
    ],
)
def test_oidc_rejects_wrong_issuer_audience_expiry_or_subject(
    rsa_keys,
    claim_overrides,
    config_overrides,
) -> None:
    private, public = rsa_keys
    token = jwt.encode(_claims(**claim_overrides), private, algorithm="RS256")
    with pytest.raises(AuthenticationError):
        _verifier(public, **config_overrides).verify(token)


def test_oidc_rejects_algorithm_outside_allowlist() -> None:
    token = jwt.encode(_claims(), HMAC_KEY, algorithm="HS256")
    verifier = OIDCVerifier(
        OIDCVerifierConfig(issuer=ISSUER, audience=AUDIENCE),
        signing_key=HMAC_KEY,
    )
    with pytest.raises(AuthenticationError, match="algorithm"):
        verifier.verify(token)


def test_oidc_decoder_is_injectable_without_jwks_network() -> None:
    token = jwt.encode(_claims(), HMAC_KEY, algorithm="HS256")
    seen = {}

    def decoder(encoded, **kwargs):
        seen["token"] = encoded
        seen.update(kwargs)
        return _claims()

    verifier = OIDCVerifier(
        OIDCVerifierConfig(
            issuer=ISSUER,
            audience=AUDIENCE,
            algorithms=("HS256",),
        ),
        signing_key=HMAC_KEY,
        decoder=decoder,
    )
    assert verifier.verify(token).subject == "user-123"
    assert seen["algorithms"] == ["HS256"]
    assert seen["issuer"] == ISSUER


def test_request_contract_is_immutable_and_contains_no_bearer_material() -> None:
    bearer = "eyJ.secret.bearer"
    principal = AuthenticatedPrincipal(
        issuer=ISSUER,
        subject="user-123",
        scopes=["fabric.query"],  # type: ignore[arg-type]
        claims=(("email_verified", True),),
    )
    context = RequestContext(
        principal=principal,
        request_id="req-1",
        trace_id="trace-1",
        purpose="customer support",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    serialized = asdict(context)

    assert bearer not in repr(context)
    assert bearer not in repr(serialized)
    assert "token" not in repr(serialized).casefold()
    assert context.principal.scopes == ("fabric.query",)
    with pytest.raises(FrozenInstanceError):
        context.request_id = "changed"  # type: ignore[misc]


def test_source_identity_hides_short_lived_token_from_repr_and_asdict() -> None:
    token = "source-secret-token"
    identity = SourceIdentity(
        source_id="snowflake:telemetry",
        subject="source-user-123",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        scheme="Bearer",
        token=token,
    )
    assert token not in repr(identity)
    assert token not in repr(asdict(identity))
    assert identity.material.reveal() == token

"""Query-plane authentication and immutable request-context contracts."""

from .contracts import (
    ANONYMOUS_DEV_PRINCIPAL,
    AuthenticatedPrincipal,
    AuthenticationError,
    RequestContext,
    RequestMetadata,
    anonymous_request_context,
    normalize_purpose,
    normalize_request_identifier,
)
from .oidc import OIDCVerifier, OIDCVerifierConfig, principal_from_claims

__all__ = [
    "ANONYMOUS_DEV_PRINCIPAL",
    "AuthenticatedPrincipal",
    "AuthenticationError",
    "OIDCVerifier",
    "OIDCVerifierConfig",
    "RequestContext",
    "RequestMetadata",
    "anonymous_request_context",
    "normalize_purpose",
    "normalize_request_identifier",
    "principal_from_claims",
]

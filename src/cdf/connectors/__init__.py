"""Secret-safe connector contracts, resolution, rotation, and redaction."""

from .delegation import (
    BaseSourceIdentity,
    DelegationBroker,
    DelegationError,
    SecretMaterial,
    SourceAuthMode,
    SourceExecutionContext,
    SourceIdentity,
)
from .redaction import REDACTED, credential_values, redact, sanitize, scrub_exception
from .registry import (
    ConnectorHealth,
    ConnectorOperationalError,
    ConnectorRegistry,
    ReloadingExecutor,
)
from .secrets import (
    ConnectorRef,
    EnvSecretResolver,
    FileSecretResolver,
    ResolvedConnector,
    SecretFields,
    SecretResolver,
    resolver_from_env,
)

__all__ = [
    "REDACTED",
    "BaseSourceIdentity",
    "ConnectorHealth",
    "ConnectorOperationalError",
    "ConnectorRef",
    "ConnectorRegistry",
    "DelegationBroker",
    "DelegationError",
    "EnvSecretResolver",
    "FileSecretResolver",
    "ReloadingExecutor",
    "ResolvedConnector",
    "SecretFields",
    "SecretMaterial",
    "SecretResolver",
    "SourceAuthMode",
    "SourceExecutionContext",
    "SourceIdentity",
    "credential_values",
    "redact",
    "resolver_from_env",
    "sanitize",
    "scrub_exception",
]


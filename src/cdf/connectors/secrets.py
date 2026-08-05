"""SecretResolver contracts and environment/mounted-file backends."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from .redaction import REDACTED, credential_values

_GENERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REGISTRY_KEYS = frozenset({"version", "sources"})
_ENTRY_KEYS = frozenset({"kind", "ref", "generation", "fields", "file"})
_SECRET_FILE_KEYS = frozenset({"generation", "fields"})


@dataclass(frozen=True)
class ConnectorRef:
    """Logical, credential-free source identity shared with CSI routing."""

    source_id: str
    kind: str
    ref: str = ""

    def __post_init__(self) -> None:
        if not self.source_id or not self.kind:
            raise ValueError("connector source_id and kind must be non-empty")


class SecretFields:
    """Immutable use-time fields whose repr and generic JSON encoding are safe."""

    __slots__ = ("_values",)
    _values: Mapping[str, str]

    def __init__(self, values: Mapping[str, str]) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)

    def values(self) -> tuple[str, ...]:
        return tuple(self._values.values())

    def reveal(self) -> dict[str, str]:
        """Return a use-time copy for a connector library call."""
        return dict(self._values)

    def __repr__(self) -> str:
        return (
            "SecretFields({"
            + ", ".join(f"{key!r}: {REDACTED!r}" for key in self._values)
            + "})"
        )

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("SecretFields is immutable")

    def __deepcopy__(self, _memo: dict[int, Any]) -> SecretFields:
        return self


class ResolvedConnector:
    """Immutable resolved connector; only explicit field access reveals values."""

    __slots__ = ("_backend", "_fields", "_generation", "_ref")
    _backend: str
    _fields: SecretFields
    _generation: str
    _ref: ConnectorRef

    def __init__(
        self,
        ref: ConnectorRef,
        *,
        fields: Mapping[str, str],
        generation: str,
        backend: str,
    ) -> None:
        _validate_generation(generation)
        validated = _validate_fields(fields, f"sources.{ref.source_id}.fields")
        _validate_connector_fields(ref.kind, validated, ref.source_id)
        object.__setattr__(self, "_ref", ref)
        object.__setattr__(self, "_fields", SecretFields(validated))
        object.__setattr__(self, "_generation", generation)
        object.__setattr__(self, "_backend", backend)

    @property
    def source_id(self) -> str:
        return self._ref.source_id

    @property
    def kind(self) -> str:
        return self._ref.kind

    @property
    def ref(self) -> str:
        return self._ref.ref

    @property
    def fields(self) -> SecretFields:
        return self._fields

    @property
    def generation(self) -> str:
        return self._generation

    @property
    def backend(self) -> str:
        return self._backend

    def safe_metadata(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "ref": self.ref,
            "backend": self.backend,
            "generation": self.generation,
        }

    def redaction_values(self) -> tuple[str, ...]:
        """Credential-only values safe to register while this generation is active."""
        return credential_values(self.fields.reveal())

    def __repr__(self) -> str:
        return (
            "ResolvedConnector("
            f"source_id={self.source_id!r}, kind={self.kind!r}, ref={self.ref!r}, "
            f"generation={self.generation!r}, backend={self.backend!r}, fields={self.fields!r})"
        )

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("ResolvedConnector is immutable")

    def __deepcopy__(self, _memo: dict[int, Any]) -> ResolvedConnector:
        return self


class SecretResolver(Protocol):
    """Resolve a logical source immediately before its executor is built."""

    backend: str

    def resolve(self, source: ConnectorRef) -> ResolvedConnector | None: ...


class EnvSecretResolver:
    """Backward-compatible env resolver with optional per-source JSON registry."""

    backend = "env"

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else os.environ

    @property
    def has_registry(self) -> bool:
        return bool(self._environ.get("CDF_SECRET_REGISTRY_JSON"))

    def resolve(self, source: ConnectorRef) -> ResolvedConnector | None:
        registry_json = self._environ.get("CDF_SECRET_REGISTRY_JSON")
        if registry_json:
            registry = _parse_registry(json.loads(registry_json), allow_files=False)
            entry = registry.get(source.source_id)
            if entry is None:
                return None
            return _resolved_from_entry(source, entry, backend=self.backend)
        fields = _legacy_fields(source, self._environ)
        if not fields:
            return None
        generation_key = "CDF_SECRET_GENERATION_" + re.sub(
            r"[^A-Za-z0-9]", "_", source.source_id
        ).upper()
        generation = self._environ.get(
            generation_key,
            self._environ.get("CDF_SECRET_GENERATION", "legacy"),
        )
        return ResolvedConnector(
            source,
            fields=fields,
            generation=generation,
            backend=self.backend,
        )


class FileSecretResolver:
    """Production resolver for Docker/Kubernetes mounted JSON secret files."""

    backend = "file"

    def __init__(self, registry_path: str | Path, mount_path: str | Path | None = None) -> None:
        self.registry_path = Path(registry_path)
        self.mount_path = Path(mount_path) if mount_path is not None else self.registry_path.parent

    def resolve(self, source: ConnectorRef) -> ResolvedConnector | None:
        registry = _parse_registry(
            json.loads(_secure_read(self.registry_path)),
            allow_files=True,
        )
        entry = registry.get(source.source_id)
        if entry is None:
            return None
        if "file" not in entry:
            return _resolved_from_entry(source, entry, backend=self.backend)
        secret_name = entry["file"]
        secret_path = (self.mount_path / secret_name).resolve()
        mount = self.mount_path.resolve()
        if not secret_path.is_relative_to(mount):
            raise ValueError("mounted secret file must stay inside CDF_SECRET_MOUNT_PATH")
        document = json.loads(_secure_read(secret_path))
        if not isinstance(document, dict) or set(document) - _SECRET_FILE_KEYS:
            raise ValueError("mounted secret must contain only generation and fields")
        merged = {
            "kind": entry["kind"],
            "ref": entry.get("ref", ""),
            "generation": document.get("generation"),
            "fields": document.get("fields"),
        }
        return _resolved_from_entry(source, merged, backend=self.backend)


def resolver_from_env(environ: Mapping[str, str] | None = None) -> SecretResolver:
    """Build the selected resolver without including LLM provider credentials."""
    env = environ if environ is not None else os.environ
    backend = env.get("CDF_SECRET_BACKEND", "env").strip().casefold()
    if backend == "env":
        return EnvSecretResolver(env)
    if backend == "file":
        registry = env.get("CDF_SECRET_REGISTRY_PATH")
        if not registry:
            raise ValueError("CDF_SECRET_REGISTRY_PATH is required for file secret backend")
        return FileSecretResolver(registry, env.get("CDF_SECRET_MOUNT_PATH"))
    raise ValueError("CDF_SECRET_BACKEND must be 'env' or 'file'")


def _parse_registry(value: Any, *, allow_files: bool) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) - _REGISTRY_KEYS:
        raise ValueError("secret registry must contain only version and sources")
    if value.get("version") != 1:
        raise ValueError("secret registry version must be 1")
    sources = value.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("secret registry sources must be an object")
    parsed: dict[str, dict[str, Any]] = {}
    for source_id, raw in sources.items():
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("secret registry source IDs must be non-empty strings")
        if not isinstance(raw, dict) or set(raw) - _ENTRY_KEYS:
            raise ValueError(f"secret registry entry {source_id!r} has unknown fields")
        if not allow_files and "file" in raw:
            raise ValueError("environment secret registry cannot reference mounted files")
        if bool(raw.get("file")) == bool(raw.get("fields")):
            raise ValueError(
                f"secret registry entry {source_id!r} requires exactly one of fields or file"
            )
        if not isinstance(raw.get("kind"), str) or not raw["kind"]:
            raise ValueError(f"secret registry entry {source_id!r} requires kind")
        if "file" in raw and (
            not isinstance(raw["file"], str)
            or not raw["file"]
            or Path(raw["file"]).name != raw["file"]
        ):
            raise ValueError(f"secret registry entry {source_id!r} has invalid file")
        parsed[source_id] = raw
    return parsed


def _resolved_from_entry(
    source: ConnectorRef,
    entry: Mapping[str, Any],
    *,
    backend: str,
) -> ResolvedConnector:
    if entry.get("kind") != source.kind:
        raise ValueError(f"secret registry kind mismatch for {source.source_id!r}")
    entry_ref = entry.get("ref", "")
    if entry_ref and entry_ref != source.ref:
        raise ValueError(f"secret registry ref mismatch for {source.source_id!r}")
    generation = entry.get("generation")
    if not isinstance(generation, str):
        raise ValueError(f"secret registry entry {source.source_id!r} requires generation")
    fields = entry.get("fields")
    if not isinstance(fields, dict):
        raise ValueError(f"secret registry entry {source.source_id!r} requires fields")
    return ResolvedConnector(
        source,
        fields=fields,
        generation=generation,
        backend=backend,
    )


def _validate_generation(generation: str) -> None:
    if not _GENERATION.fullmatch(generation):
        raise ValueError("secret generation must be an opaque identifier using [A-Za-z0-9._-]")


def _validate_fields(fields: Mapping[str, Any], path: str) -> dict[str, str]:
    if not fields:
        raise ValueError(f"{path} must be a non-empty object")
    result: dict[str, str] = {}
    for key, value in fields.items():
        if not isinstance(key, str) or not key or not isinstance(value, str):
            raise ValueError(f"{path} keys and values must be strings")
        if not value:
            raise ValueError(f"{path}.{key} must be non-empty")
        result[key] = value
    return result


def _validate_connector_fields(
    kind: str,
    fields: Mapping[str, str],
    source_id: str,
) -> None:
    schemas = {
        "arango": ({"url", "database", "user", "password"}, {"url"}),
        "assembly": ({"url", "database", "user", "password"}, {"url"}),
        "clickhouse": ({"dsn"}, {"dsn"}),
        "postgresql": (
            {"endpoint", "reformulate_endpoint"},
            {"endpoint"},
        ),
        "snowflake": (
            {
                "account",
                "user",
                "password",
                "private_key_file",
                "private_key_file_pwd",
                "warehouse",
                "database",
                "schema",
                "role",
            },
            {"account", "user"},
        ),
    }
    allowed, required = schemas.get(kind, ({"endpoint"}, {"endpoint"}))
    unknown = set(fields) - allowed
    missing = required - set(fields)
    if unknown:
        raise ValueError(
            f"connector {source_id!r} has unknown fields: {', '.join(sorted(unknown))}"
        )
    if missing:
        raise ValueError(
            f"connector {source_id!r} requires fields: {', '.join(sorted(missing))}"
        )
    if kind == "snowflake":
        if bool(fields.get("password")) == bool(fields.get("private_key_file")):
            raise ValueError(
                f"connector {source_id!r} requires exactly one of password or private_key_file"
            )
        if fields.get("private_key_file_pwd") and not fields.get("private_key_file"):
            raise ValueError(
                f"connector {source_id!r} private_key_file_pwd requires private_key_file"
            )


def _secure_read(path: Path) -> str:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{path} must be a regular file")
    if os.name == "posix":
        if info.st_uid not in (os.geteuid(), 0):
            raise PermissionError(f"{path} must be owned by the service user or root")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PermissionError(f"{path} permissions must not grant group/other access")
    return path.read_text(encoding="utf-8")


def _legacy_fields(source: ConnectorRef, env: Mapping[str, str]) -> dict[str, str]:
    if source.source_id == "cdf:assembly":
        base_names = {
            "url": "ARANGO_URL",
            "database": "ARANGO_DB",
            "user": "ARANGO_USER",
            "password": "ARANGO_PASSWORD",
        }
        override_names = {
            "url": "CDF_ASSEMBLY_ARANGO_URL",
            "database": "CDF_ASSEMBLY_ARANGO_DATABASE",
            "user": "CDF_ASSEMBLY_ARANGO_USER",
            "password": "CDF_ASSEMBLY_ARANGO_PASSWORD",
        }
        fields = {
            field: env[name] for field, name in base_names.items() if env.get(name)
        }
        fields.update(
            {
                field: env[name]
                for field, name in override_names.items()
                if env.get(name)
            }
        )
        return fields
    names_by_kind = {
        "arango": {
            "url": "ARANGO_URL",
            "database": "ARANGO_DB",
            "user": "ARANGO_USER",
            "password": "ARANGO_PASSWORD",
        },
        "clickhouse": {"dsn": "CLICKHOUSE_DSN"},
        "postgresql": {
            "endpoint": "ONTOP_SPARQL_ENDPOINT",
            "reformulate_endpoint": "ONTOP_REFORMULATE_ENDPOINT",
        },
        "snowflake": {
            "account": "SNOWFLAKE_ACCOUNT",
            "user": "SNOWFLAKE_USER",
            "password": "SNOWFLAKE_PASSWORD",
            "private_key_file": "SNOWFLAKE_PRIVATE_KEY_FILE",
            "private_key_file_pwd": "SNOWFLAKE_PRIVATE_KEY_FILE_PWD",
            "warehouse": "SNOWFLAKE_WAREHOUSE",
            "database": "SNOWFLAKE_DATABASE",
            "schema": "SNOWFLAKE_SCHEMA",
            "role": "SNOWFLAKE_ROLE",
        },
    }
    names = names_by_kind.get(source.kind, {"endpoint": "ONTOP_SPARQL_ENDPOINT"})
    return {field: env[name] for field, name in names.items() if env.get(name)}


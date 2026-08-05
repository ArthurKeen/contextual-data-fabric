"""P2.3 connector secret resolution, rotation, lifecycle, and redaction."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import pytest

from cdf.auth import anonymous_request_context
from cdf.connectors import (
    ConnectorRef,
    DelegationError,
    EnvSecretResolver,
    FileSecretResolver,
    ReloadingExecutor,
    ResolvedConnector,
    SourceExecutionContext,
    SourceIdentity,
    redact,
    resolver_from_env,
    scrub_exception,
)
from cdf.query import SourceResult


def _resolved(source: ConnectorRef, generation: str, password: str) -> ResolvedConnector:
    return ResolvedConnector(
        source,
        fields={"url": "https://db.example", "password": password},
        generation=generation,
        backend="test",
    )


def test_legacy_env_resolution_is_backward_compatible_and_redacted() -> None:
    resolved = EnvSecretResolver(
        {
            "SNOWFLAKE_ACCOUNT": "org-account",
            "SNOWFLAKE_USER": "reader",
            "SNOWFLAKE_PASSWORD": "legacy-secret",
        }
    ).resolve(ConnectorRef("snowflake:one", "snowflake", "one"))
    assert resolved is not None
    assert resolved.fields["password"] == "legacy-secret"
    assert resolved.generation == "legacy"
    assert "legacy-secret" not in repr(resolved)


def test_postgresql_env_resolution_carries_optional_ontop_reformulation_endpoint() -> None:
    resolved = EnvSecretResolver(
        {
            "ONTOP_SPARQL_ENDPOINT": "http://ontop:8080/sparql",
            "ONTOP_REFORMULATE_ENDPOINT": "http://ontop:8080/ontop/reformulate",
        }
    ).resolve(ConnectorRef("postgresql:crm", "postgresql", "crm"))

    assert resolved is not None
    assert resolved.fields["endpoint"] == "http://ontop:8080/sparql"
    assert (
        resolved.fields["reformulate_endpoint"]
        == "http://ontop:8080/ontop/reformulate"
    )


def test_env_registry_keeps_same_kind_sources_distinct() -> None:
    registry = {
        "version": 1,
        "sources": {
            "snowflake:east": {
                "kind": "snowflake",
                "ref": "east",
                "generation": "east-v1",
                "fields": {"account": "east-account", "user": "reader", "password": "east-pass"},
            },
            "snowflake:west": {
                "kind": "snowflake",
                "ref": "west",
                "generation": "west-v4",
                "fields": {"account": "west-account", "user": "reader", "password": "west-pass"},
            },
        },
    }
    resolver = EnvSecretResolver({"CDF_SECRET_REGISTRY_JSON": json.dumps(registry)})
    east = resolver.resolve(ConnectorRef("snowflake:east", "snowflake", "east"))
    west = resolver.resolve(ConnectorRef("snowflake:west", "snowflake", "west"))
    assert east is not None and west is not None
    assert east.fields["account"] == "east-account"
    assert west.fields["account"] == "west-account"
    assert east.generation != west.generation


def test_file_resolver_reads_mounted_json_and_validates_schema(tmp_path) -> None:
    registry_path = tmp_path / "registry.json"
    secret_path = tmp_path / "crm.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": {
                    "postgresql:crm": {
                        "kind": "postgresql",
                        "ref": "crm",
                        "file": "crm.json",
                    }
                },
            }
        )
    )
    secret_path.write_text(
        json.dumps(
            {
                "generation": "rotation-7",
                "fields": {"endpoint": "http://ontop.internal/sparql"},
            }
        )
    )
    registry_path.chmod(0o600)
    secret_path.chmod(0o600)

    resolved = FileSecretResolver(registry_path, tmp_path).resolve(
        ConnectorRef("postgresql:crm", "postgresql", "crm")
    )
    assert resolved is not None
    assert resolved.generation == "rotation-7"
    assert resolved.fields["endpoint"] == "http://ontop.internal/sparql"

    secret_path.write_text(json.dumps({"generation": "bad generation", "fields": {"x": "y"}}))
    with pytest.raises(ValueError, match="opaque identifier"):
        FileSecretResolver(registry_path, tmp_path).resolve(
            ConnectorRef("postgresql:crm", "postgresql", "crm")
        )


def test_resolver_factory_requires_file_registry_path() -> None:
    assert isinstance(resolver_from_env({"CDF_SECRET_BACKEND": "env"}), EnvSecretResolver)
    with pytest.raises(ValueError, match="CDF_SECRET_REGISTRY_PATH"):
        resolver_from_env({"CDF_SECRET_BACKEND": "file"})
    with pytest.raises(ValueError, match="env.*file"):
        resolver_from_env({"CDF_SECRET_BACKEND": "unknown"})


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
def test_file_resolver_rejects_group_or_other_readable_files(tmp_path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": 1, "sources": {}}))
    path.chmod(0o644)
    with pytest.raises(PermissionError, match="group/other"):
        FileSecretResolver(path).resolve(ConnectorRef("arango:x", "arango", "x"))


def test_secret_objects_do_not_leak_through_repr_or_asdict() -> None:
    secret = _resolved(ConnectorRef("arango:crm", "arango", "crm"), "v1", "hidden")

    @dataclass
    class Holder:
        connector: object

    serialized = asdict(Holder(secret))
    assert "hidden" not in repr(secret)
    assert "hidden" not in repr(serialized)
    with pytest.raises(TypeError):
        json.dumps(serialized)
    with pytest.raises(AttributeError):
        secret._generation = "changed"
    with pytest.raises(AttributeError):
        secret.fields._values = {}


class _MutableResolver:
    backend = "test"

    def __init__(self, resolved):
        self.resolved = resolved

    def resolve(self, _source):
        if isinstance(self.resolved, BaseException):
            raise self.resolved
        return self.resolved


class _Executor:
    def __init__(self, label: str, *, entered=None, release=None):
        self.label = label
        self.entered = entered
        self.release = release
        self.drains = 0

    def execute(self, _subquery):
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(timeout=2)
        return SourceResult(rows=({"generation": self.label},))

    def drain(self):
        self.drains += 1


def test_rotation_swaps_and_drains_old_once_after_in_flight_finishes() -> None:
    source = ConnectorRef("arango:crm", "arango", "crm")
    resolver = _MutableResolver(_resolved(source, "v1", "old-secret"))
    entered, release = threading.Event(), threading.Event()
    built = {
        "v1": _Executor("v1", entered=entered, release=release),
        "v2": _Executor("v2"),
    }
    proxy = ReloadingExecutor(source, resolver, lambda value: built[value.generation])

    old_result = []
    thread = threading.Thread(target=lambda: old_result.append(proxy.execute(None)))
    thread.start()
    assert entered.wait(timeout=1)
    resolver.resolved = _resolved(source, "v2", "new-secret")
    assert proxy.execute(None).rows == ({"generation": "v2"},)
    assert built["v1"].drains == 0
    release.set()
    thread.join(timeout=1)
    assert old_result[0].rows == ({"generation": "v1"},)
    assert built["v1"].drains == 1
    assert proxy.execute(None).rows == ({"generation": "v2"},)
    assert built["v1"].drains == 1
    proxy.close()


def test_reloading_registry_proxy_preserves_context_and_legacy_service_mode() -> None:
    source = ConnectorRef("arango:crm", "arango", "crm")
    resolver = _MutableResolver(_resolved(source, "v1", "registry-context-value"))

    class Aware:
        def __init__(self):
            self.context = None

        def execute_with_context(self, _subquery, context):
            self.context = context
            return SourceResult(rows=({"ok": True},))

    aware = Aware()
    proxy = ReloadingExecutor(source, resolver, lambda _resolved: aware)
    legacy = ReloadingExecutor(source, resolver, lambda _resolved: _Executor("legacy"))
    try:
        context = SourceExecutionContext(
            request=anonymous_request_context(),
            source_id=source.source_id,
            auth_mode="service",
        )
        assert proxy.execute_with_context(None, context).rows == ({"ok": True},)
        assert aware.context is context

        delegated_identity = SourceExecutionContext(
            request=anonymous_request_context(),
            source_id=source.source_id,
            auth_mode="delegated",
            identity=SourceIdentity(
                source_id=source.source_id,
                subject="user",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
                scheme="Bearer",
                token="downstream",
            ),
        )
        with pytest.raises(DelegationError, match="does not support"):
            legacy.execute_with_context(None, delegated_identity)
    finally:
        proxy.close()
        legacy.close()


def test_failed_rotation_keeps_last_known_good_and_logs_scrubbed(caplog) -> None:
    source = ConnectorRef("arango:crm", "arango", "crm")
    resolver = _MutableResolver(_resolved(source, "v1", "old-secret"))
    old = _Executor("v1")

    def build(value):
        if value.generation == "v2":
            raise RuntimeError(f"driver rejected {value.fields['password']}")
        return old

    proxy = ReloadingExecutor(source, resolver, build)
    resolver.resolved = _resolved(source, "v2", "new-secret")
    with caplog.at_level(logging.ERROR):
        assert proxy.execute(None).rows == ({"generation": "v1"},)
    assert old.drains == 0
    assert proxy.health().generation == "v1"
    assert proxy.health().last_reload_status == "failed"
    assert "new-secret" not in caplog.text
    proxy.close()


def test_only_credential_values_are_registered_for_unlabelled_errors() -> None:
    source = ConnectorRef("snowflake:visible", "snowflake", "visible")
    resolved = ResolvedConnector(
        source,
        fields={
            "account": "business-account-visible",
            "user": "reader-visible",
            "password": "password-must-hide",
            "database": "analytics-visible",
            "schema": "public-visible",
        },
        generation="v1",
        backend="test",
    )
    proxy = ReloadingExecutor(source, _MutableResolver(resolved), lambda _value: _Executor("v1"))
    try:
        safe = scrub_exception(
            RuntimeError(
                "driver echoed business-account-visible reader-visible "
                "analytics-visible public-visible password-must-hide"
            )
        )
        assert "business-account-visible" in safe
        assert "reader-visible" in safe
        assert "analytics-visible" in safe
        assert "public-visible" in safe
        assert "password-must-hide" not in safe
    finally:
        proxy.close()


def test_private_key_passphrase_but_not_key_path_is_registered() -> None:
    source = ConnectorRef("snowflake:keypair", "snowflake", "keypair")
    resolved = ResolvedConnector(
        source,
        fields={
            "account": "account-visible",
            "user": "reader-visible",
            "private_key_file": "/run/secrets/key-path-visible.p8",
            "private_key_file_pwd": "passphrase-must-hide",
        },
        generation="v1",
        backend="test",
    )
    proxy = ReloadingExecutor(source, _MutableResolver(resolved), lambda _value: _Executor("v1"))
    try:
        safe = scrub_exception(
            RuntimeError(
                "driver echoed /run/secrets/key-path-visible.p8 passphrase-must-hide"
            )
        )
        assert "/run/secrets/key-path-visible.p8" in safe
        assert "passphrase-must-hide" not in safe
    finally:
        proxy.close()


def test_dsn_embedded_password_is_registered_without_registering_username() -> None:
    source = ConnectorRef("clickhouse:secure", "clickhouse", "secure")
    resolved = ResolvedConnector(
        source,
        fields={"dsn": "clickhouse://reader-visible:dsn-password-hide@db.internal/analytics"},
        generation="v1",
        backend="test",
    )
    proxy = ReloadingExecutor(source, _MutableResolver(resolved), lambda _value: _Executor("v1"))
    try:
        safe = scrub_exception(
            RuntimeError("driver echoed reader-visible and dsn-password-hide")
        )
        assert "reader-visible" in safe
        assert "dsn-password-hide" not in safe
        assert "dsn-password-hide" not in (
            redact("clickhouse://reader-visible:dsn-password-hide@db.internal") or ""
        )
    finally:
        proxy.close()


def test_rotation_releases_obsolete_secret_after_old_slot_drains() -> None:
    source = ConnectorRef("arango:lease", "arango", "lease")
    resolver = _MutableResolver(_resolved(source, "v1", "obsolete-lease-secret"))
    proxy = ReloadingExecutor(source, resolver, lambda value: _Executor(value.generation))
    assert "obsolete-lease-secret" not in (redact("obsolete-lease-secret") or "")

    resolver.resolved = _resolved(source, "v2", "current-lease-secret")
    proxy.execute(None)
    safe = redact("obsolete-lease-secret current-lease-secret") or ""
    assert "obsolete-lease-secret" in safe
    assert "current-lease-secret" not in safe
    proxy.close()


@pytest.mark.parametrize(
    "unsafe",
    [
        "postgres://reader:s3cr3t@db.example/app",
        "CLICKHOUSE_DSN=clickhouse://reader:s3cr3t@db.example/app",
        "Authorization: Bearer abc.def.ghi",
        '{"password":"s3cr3t","api_key":"abcdef"}',
        "https://service/path?token=abcdef",
    ],
)
def test_central_redaction_covers_urls_dsns_auth_and_pairs(unsafe) -> None:
    safe = redact(unsafe)
    assert safe is not None
    assert "s3cr3t" not in safe
    assert "abcdef" not in safe
    assert "abc.def.ghi" not in safe


def test_scrub_exception_removes_unlabelled_known_resolved_value() -> None:
    source = ConnectorRef("arango:y", "arango", "y")
    resolved = _resolved(source, "v1", "unlabelled-value")
    proxy = ReloadingExecutor(source, _MutableResolver(resolved), lambda _value: _Executor("v1"))
    try:
        safe = scrub_exception(RuntimeError("driver echoed unlabelled-value"))
        assert "unlabelled-value" not in safe
    finally:
        proxy.close()


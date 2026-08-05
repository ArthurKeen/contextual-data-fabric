"""Central secret redaction for connector and public error surfaces."""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

REDACTED = "[REDACTED]"

_KNOWN_LOCK = threading.Lock()
_KNOWN_SECRETS: dict[str, int] = {}
_SECRET_KEY = re.compile(
    r"(?i)(?:password|passwd|pwd|passphrase|token|api[_-]?key|secret|credential|"
    r"authorization|access[_-]?key|client[_-]?secret|private[_-]?key|(?:^|[_-])key$)"
)
_NON_SECRET_KEY_REFERENCE = re.compile(r"(?i)(?:key|secret)[_-]?(?:file|path)$")
_URL_AUTH = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+)(?::([^@\s/]*))?@"
)
_JDBC_AUTH = re.compile(
    r"(?i)\b(jdbc:[a-z][a-z0-9+.-]*://)([^/\s:@]+)(?::([^@\s/]*))?@"
)
_AUTH_HEADER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_PAIR = re.compile(
    r"""(?ix)
    (["']?(?:password|passwd|pwd|passphrase|token|api[_-]?key|secret|credential|
       authorization|access[_-]?key|client[_-]?secret|private[_-]?key)
       ["']?\s*[:=]\s*)
    (?:
      ["'][^"']*["'] |
      [^\s,;}\]]+
    )
    """
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:password|passwd|pwd|passphrase|token|api[_-]?key|secret|"
    r"credential|authorization|access[_-]?key|client[_-]?secret)=)"
    r"[^&#\s]*"
)


class SecretValueLease:
    """A ref-counted lease for active values used in unlabelled driver errors."""

    __slots__ = ("_closed", "_values")

    def __init__(self, values: Sequence[str]) -> None:
        self._values = tuple(dict.fromkeys(value for value in values if len(value) >= 4))
        self._closed = False
        with _KNOWN_LOCK:
            for value in self._values:
                _KNOWN_SECRETS[value] = _KNOWN_SECRETS.get(value, 0) + 1

    def close(self) -> None:
        if self._closed:
            return
        with _KNOWN_LOCK:
            if self._closed:
                return
            self._closed = True
            for value in self._values:
                remaining = _KNOWN_SECRETS.get(value, 0) - 1
                if remaining > 0:
                    _KNOWN_SECRETS[value] = remaining
                else:
                    _KNOWN_SECRETS.pop(value, None)

    def __repr__(self) -> str:
        return f"SecretValueLease(values={REDACTED}, closed={self._closed})"

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # pragma: no cover - interpreter shutdown best effort
            pass


def credential_values(fields: Mapping[str, Any]) -> tuple[str, ...]:
    """Select only credential-bearing values, including credentials inside URLs."""
    selected: list[str] = []
    for key, raw in fields.items():
        if raw is None:
            continue
        value = str(raw)
        key_text = str(key)
        if _SECRET_KEY.search(key_text) and not _NON_SECRET_KEY_REFERENCE.search(key_text):
            selected.append(value)
        selected.extend(_embedded_credentials(value))
    return tuple(dict.fromkeys(value for value in selected if len(value) >= 4))


def register_secret_values(values: Sequence[str]) -> SecretValueLease:
    """Lease active credential values for unlabelled exception scrubbing."""
    return SecretValueLease(values)


def _embedded_credentials(value: str) -> tuple[str, ...]:
    candidate = value[5:] if value.casefold().startswith("jdbc:") else value
    if "://" not in candidate:
        return ()
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ()
    embedded: list[str] = []
    if parsed.password:
        embedded.append(unquote(parsed.password))
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if _SECRET_KEY.search(key) and item:
            embedded.append(item)
    return tuple(embedded)


def _registered_values() -> tuple[str, ...]:
    with _KNOWN_LOCK:
        return tuple(_KNOWN_SECRETS)


def redact(value: str | None, *, known_values: Sequence[str] = ()) -> str | None:
    """Redact common credential forms and exact known resolved values."""
    if value is None:
        return None
    result = str(value)
    registered = _registered_values()
    for secret in sorted((*registered, *known_values), key=len, reverse=True):
        if len(secret) >= 4:
            result = result.replace(secret, REDACTED)
    result = _JDBC_AUTH.sub(r"\1[REDACTED]@", result)
    result = _URL_AUTH.sub(r"\1[REDACTED]@", result)
    result = _AUTH_HEADER.sub(lambda match: f"{match.group(1)} {REDACTED}", result)
    result = _PAIR.sub(rf"\1{REDACTED}", result)
    result = _QUERY_SECRET.sub(rf"\1{REDACTED}", result)
    return result


def scrub_exception(
    error: BaseException,
    *,
    known_values: Sequence[str] = (),
) -> str:
    """Return a public-safe exception summary."""
    return redact(
        f"{type(error).__name__}: {error}",
        known_values=known_values,
    ) or type(error).__name__


def sanitize(value: Any) -> Any:
    """Recursively sanitize a value before serialization or persistence."""
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _SECRET_KEY.search(str(key)) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize(item) for item in value)
    if isinstance(value, str):
        return redact(value)
    return value


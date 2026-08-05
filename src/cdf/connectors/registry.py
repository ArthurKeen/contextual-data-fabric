"""Generation-aware, in-flight-safe executor rotation."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from .delegation import DelegationError, SourceExecutionContext
from .redaction import SecretValueLease, register_secret_values, scrub_exception
from .secrets import ConnectorRef, ResolvedConnector, SecretResolver

_LOG = logging.getLogger(__name__)
ExecutorBuilder = Callable[[ResolvedConnector], Any]


class ConnectorOperationalError(RuntimeError):
    """Safe operational failure suitable for retrieval paths and APIs."""


@dataclass(frozen=True)
class ConnectorHealth:
    """Credential state without connection values."""

    configured: bool
    backend: str
    generation: str | None
    last_reload_status: str
    last_reload_time: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "backend": self.backend,
            "generation": self.generation,
            "last_reload_status": self.last_reload_status,
            "last_reload_time": self.last_reload_time,
        }


@dataclass
class _Slot:
    executor: Any
    resolved: ResolvedConnector
    secret_lease: SecretValueLease
    active: int = 0
    retired: bool = False
    closed: bool = False


class ReloadingExecutor:
    """Atomically swap executor generations while old in-flight calls finish."""

    def __init__(
        self,
        source: ConnectorRef,
        resolver: SecretResolver,
        builder: ExecutorBuilder,
        *,
        poll_interval: float = 0.0,
        build_initial: bool = True,
    ) -> None:
        if poll_interval < 0:
            raise ValueError("connector poll interval must be non-negative")
        self.source = source
        self._resolver = resolver
        self._builder = builder
        self._poll_interval = poll_interval
        self._lock = threading.RLock()
        self._slot: _Slot | None = None
        self._next_poll = 0.0
        self._last_reload_status = "not_loaded"
        self._last_reload_time: str | None = None
        if build_initial:
            self._refresh(force=True)

    def execute(self, subquery: Any) -> Any:
        return self._execute(subquery, context=None)

    def execute_with_context(
        self,
        subquery: Any,
        context: SourceExecutionContext,
    ) -> Any:
        """Propagate context to aware adapters without weakening delegated mode."""
        return self._execute(subquery, context=context)

    def supports_execution_context(self) -> bool:
        """Report whether the active generation consumes source context."""
        self._refresh()
        with self._lock:
            return self._slot is not None and callable(
                getattr(self._slot.executor, "execute_with_context", None)
            )

    def _execute(
        self,
        subquery: Any,
        *,
        context: SourceExecutionContext | None,
    ) -> Any:
        self._refresh()
        with self._lock:
            slot = self._slot
            if slot is None:
                raise ConnectorOperationalError(
                    f"connector {self.source.source_id!r} is not configured"
                )
            slot.active += 1
        try:
            contextual_execute = getattr(slot.executor, "execute_with_context", None)
            if context is not None and callable(contextual_execute):
                return contextual_execute(subquery, context)
            if context is not None and context.auth_mode == "delegated":
                raise DelegationError(
                    f"source {self.source.source_id!r} does not support delegated identity"
                )
            return slot.executor.execute(subquery)
        except ConnectorOperationalError:
            raise
        except DelegationError:
            raise
        except Exception as exc:
            safe = scrub_exception(exc, known_values=slot.resolved.redaction_values())
            raise ConnectorOperationalError(safe) from exc
        finally:
            close_slot = False
            with self._lock:
                slot.active -= 1
                close_slot = slot.retired and slot.active == 0
            if close_slot:
                self._close_slot(slot)

    def health(self) -> ConnectorHealth:
        with self._lock:
            slot = self._slot
            return ConnectorHealth(
                configured=slot is not None,
                backend=self._resolver.backend,
                generation=slot.resolved.generation if slot is not None else None,
                last_reload_status=self._last_reload_status,
                last_reload_time=self._last_reload_time,
            )

    def close(self) -> None:
        with self._lock:
            slot = self._slot
            self._slot = None
            if slot is None:
                return
            slot.retired = True
            close_now = slot.active == 0
        if close_now:
            self._close_slot(slot)

    drain = close

    def _refresh(self, *, force: bool = False) -> None:
        now = monotonic()
        with self._lock:
            if not force and now < self._next_poll:
                return
            self._next_poll = now + self._poll_interval
            current = self._slot
            resolved: ResolvedConnector | None = None
            try:
                resolved = self._resolver.resolve(self.source)
                if resolved is None:
                    if current is None:
                        self._record_reload("unconfigured")
                    return
                if current is not None and current.resolved.generation == resolved.generation:
                    return
                replacement_executor = self._builder(resolved)
                replacement = _Slot(
                    replacement_executor,
                    resolved,
                    register_secret_values(resolved.redaction_values()),
                )
            except Exception as exc:
                known = tuple(
                    dict.fromkeys(
                        (
                            *(
                                current.resolved.redaction_values()
                                if current is not None
                                else ()
                            ),
                            *(resolved.redaction_values() if resolved is not None else ()),
                        )
                    )
                )
                safe = scrub_exception(exc, known_values=known)
                self._record_reload("failed")
                _LOG.error(
                    "connector reload failed for %s: %s",
                    self.source.source_id,
                    safe,
                )
                if current is None:
                    raise ConnectorOperationalError(safe) from exc
                return
            self._slot = replacement
            self._record_reload("succeeded")
            if current is not None:
                current.retired = True
                close_old = current.active == 0
            else:
                close_old = False
        if close_old and current is not None:
            self._close_slot(current)

    def _record_reload(self, status: str) -> None:
        self._last_reload_status = status
        self._last_reload_time = datetime.now(timezone.utc).isoformat()

    def _close_slot(self, slot: _Slot) -> None:
        with self._lock:
            if slot.closed:
                return
            slot.closed = True
        try:
            lifecycle = getattr(slot.executor, "drain", None)
            if not callable(lifecycle):
                lifecycle = getattr(slot.executor, "close", None)
            if callable(lifecycle):
                lifecycle()
        except Exception as exc:  # best-effort retirement; never revive old credentials
            _LOG.error(
                "connector drain failed for %s: %s",
                self.source.source_id,
                scrub_exception(exc, known_values=slot.resolved.redaction_values()),
            )
        finally:
            slot.secret_lease.close()


class ConnectorRegistry(Mapping[str, ReloadingExecutor]):
    """Thread-safe source_id → generation-aware executor mapping."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._executors: dict[str, ReloadingExecutor] = {}

    def register(
        self,
        source: ConnectorRef,
        resolver: SecretResolver,
        builder: ExecutorBuilder,
        *,
        poll_interval: float = 0.0,
        build_initial: bool = True,
    ) -> ReloadingExecutor:
        proxy = ReloadingExecutor(
            source,
            resolver,
            builder,
            poll_interval=poll_interval,
            build_initial=build_initial,
        )
        with self._lock:
            if source.source_id in self._executors:
                raise ValueError(f"connector already registered: {source.source_id}")
            self._executors[source.source_id] = proxy
        return proxy

    def __getitem__(self, source_id: str) -> ReloadingExecutor:
        with self._lock:
            return self._executors[source_id]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(tuple(self._executors))

    def __len__(self) -> int:
        with self._lock:
            return len(self._executors)

    def health(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            items = tuple(self._executors.items())
        return {source_id: proxy.health().as_dict() for source_id, proxy in items}

    def close(self) -> None:
        with self._lock:
            proxies = tuple(self._executors.values())
        for proxy in proxies:
            proxy.close()

    drain = close


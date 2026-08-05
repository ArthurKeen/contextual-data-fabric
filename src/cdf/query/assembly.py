"""Bounded, job-scoped assembled execution substrate (P2.2 WP-12).

The deterministic federated join remains in :mod:`cdf.query.executor`; this
module mirrors source rows and joined intermediates into an isolated temporary
graph so graph-native analytics can consume a bounded lineage substrate without
turning assembly into the default execution path.
"""

from __future__ import annotations

import json
import math
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from time import perf_counter, time
from typing import Any, Literal, Protocol

from cdf.connectors.redaction import redact, sanitize, scrub_exception
from cdf.resolution import ResolutionEvent, ResolutionEventSummary

from .optimizer import PlanEstimate

ExecutionMode = Literal["virtual", "assembled"]


@dataclass(frozen=True)
class AssemblyMetrics:
    """Additive, secret-free telemetry for one execution mode."""

    mode: ExecutionMode = "virtual"
    backend: str | None = None
    job_id: str | None = None
    materialized_rows: int = 0
    materialized_bytes: int = 0
    ttl_seconds: int | None = None
    cleanup_status: str = "not_started"
    cleanup_error: str | None = None


@dataclass(frozen=True)
class AssemblyRefusal:
    """Structured assembled-mode refusal or failure."""

    code: str
    phase: str
    message: str
    metric: str | None = None
    observed: int | float | str | None = None
    limit: int | float | None = None


@dataclass(frozen=True)
class AssemblyPolicy:
    """Mandatory hard budgets for an explicitly requested assembled run."""

    max_rows: int = 50_000
    max_serialized_bytes: int = 64 * 1024 * 1024
    wall_time_ms: float = 30_000.0
    ttl_seconds: int = 300

    def __post_init__(self) -> None:
        if self.max_rows <= 0:
            raise ValueError("assembly max_rows must be positive")
        if self.max_serialized_bytes <= 0:
            raise ValueError("assembly max_serialized_bytes must be positive")
        if self.wall_time_ms <= 0:
            raise ValueError("assembly wall_time_ms must be positive")
        if self.ttl_seconds <= 0:
            raise ValueError("assembly ttl_seconds must be positive")

    def preflight(self, estimate: PlanEstimate) -> AssemblyRefusal | None:
        """Require statistics-backed estimates and enforce assembly budgets."""
        unknown = [leg.source_id for leg in estimate.legs if not leg.used_statistics]
        if unknown:
            return AssemblyRefusal(
                code="assembly_estimate_unknown",
                phase="preflight",
                metric="statistics",
                observed=",".join(unknown),
                message=(
                    "assembled execution requires statistics-backed row and byte "
                    f"estimates for every source; missing for {', '.join(unknown)}"
                ),
            )
        estimated_rows = sum(leg.estimated_rows for leg in estimate.legs)
        if len(estimate.legs) > 1:
            estimated_rows += estimate.estimated_rows
        if estimated_rows > self.max_rows:
            return _budget_refusal(
                "preflight",
                "estimated_materialized_rows",
                estimated_rows,
                self.max_rows,
            )
        estimated_bytes = estimate.estimated_bytes
        if len(estimate.legs) > 1:
            joined_width = sum(
                leg.estimated_bytes / leg.estimated_rows
                for leg in estimate.legs
                if leg.estimated_rows > 0
            )
            estimated_bytes += math.ceil(estimate.estimated_rows * joined_width)
        if estimated_bytes > self.max_serialized_bytes:
            return _budget_refusal(
                "preflight",
                "estimated_serialized_bytes",
                estimated_bytes,
                self.max_serialized_bytes,
            )
        return None

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> AssemblyPolicy:
        return cls(
            max_rows=_positive_int(environ, "CDF_ASSEMBLY_MAX_ROWS", 50_000),
            max_serialized_bytes=_positive_int(
                environ, "CDF_ASSEMBLY_MAX_BYTES", 64 * 1024 * 1024
            ),
            wall_time_ms=_positive_float(
                environ, "CDF_ASSEMBLY_WALL_TIME_MS", 30_000.0
            ),
            ttl_seconds=_positive_int(environ, "CDF_ASSEMBLY_TTL_SECONDS", 300),
        )


@dataclass(frozen=True)
class AssemblyLineage:
    """Lineage stored with one temporary vertex."""

    stage: str
    source_id: str | None = None
    subquery: str | None = None
    native_query: str | None = None
    as_of: str | None = None
    resolution_events: tuple[ResolutionEventSummary, ...] = ()


@dataclass(frozen=True)
class AssemblyRecord:
    """Backend-neutral vertex plus direct derived-from edges."""

    row_id: str
    kind: str
    values: Mapping[str, Any]
    lineage: AssemblyLineage
    input_row_ids: tuple[str, ...]
    expires_at: float

    def document(self, job_id: str) -> dict[str, Any]:
        return {
            "_key": self.row_id,
            "job_id": job_id,
            "kind": self.kind,
            "values": _sanitize_mapping(self.values),
            "lineage": asdict(self.lineage),
            "expires_at": self.expires_at,
        }


class AssemblyJob(Protocol):
    """One isolated temporary graph owned by a single assembled request."""

    job_id: str

    def write(self, records: Sequence[AssemblyRecord]) -> None: ...

    def cleanup(self) -> None: ...


class AssemblyBackend(Protocol):
    """Injectable factory for job-scoped temporary graph resources."""

    name: str

    def create_job(self, job_id: str, ttl_seconds: int) -> AssemblyJob: ...


class AssemblyFailure(RuntimeError):
    """Internal control-flow exception carrying a public structured refusal."""

    def __init__(self, refusal: AssemblyRefusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


class AssemblyExecution:
    """Budget enforcement and materialization around an :class:`AssemblyJob`."""

    def __init__(
        self,
        job: AssemblyJob,
        *,
        backend_name: str,
        policy: AssemblyPolicy,
        clock: Callable[[], float] = perf_counter,
        epoch_clock: Callable[[], float] = time,
    ) -> None:
        self.job = job
        self.backend_name = backend_name
        self.policy = policy
        self._clock = clock
        self._started = clock()
        self._expires_at = epoch_clock() + policy.ttl_seconds
        self._rows = 0
        self._bytes = 0
        self._sequence = 0

    def materialize_source(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        source_id: str,
        subquery: str,
        native_query: str | None,
        as_of: str | None,
        resolution_events: Sequence[ResolutionEvent] = (),
    ) -> tuple[str, ...]:
        lineage = AssemblyLineage(
            stage="source",
            source_id=_redact(source_id),
            subquery=_redact(subquery),
            native_query=_redact(native_query),
            as_of=_redact(as_of),
            resolution_events=tuple(event.summary() for event in resolution_events),
        )
        return self._materialize("source_row", rows, lineage, [()] * len(rows))

    def materialize_join(
        self,
        rows: Sequence[Mapping[str, Any]],
        inputs: Sequence[tuple[str, ...]],
    ) -> tuple[str, ...]:
        return self._materialize(
            "joined_intermediate",
            rows,
            AssemblyLineage(stage="deterministic_join"),
            inputs,
        )

    def check_wall_time(self) -> None:
        elapsed = (self._clock() - self._started) * 1000
        if elapsed > self.policy.wall_time_ms:
            raise AssemblyFailure(
                _budget_refusal(
                    "runtime",
                    "assembly_wall_time_ms",
                    elapsed,
                    self.policy.wall_time_ms,
                )
            )

    def metrics(
        self,
        *,
        cleanup_status: str = "pending",
        cleanup_error: str | None = None,
    ) -> AssemblyMetrics:
        return AssemblyMetrics(
            mode="assembled",
            backend=self.backend_name,
            job_id=self.job.job_id,
            materialized_rows=self._rows,
            materialized_bytes=self._bytes,
            ttl_seconds=self.policy.ttl_seconds,
            cleanup_status=cleanup_status,
            cleanup_error=_redact(cleanup_error),
        )

    def _materialize(
        self,
        kind: str,
        rows: Sequence[Mapping[str, Any]],
        lineage: AssemblyLineage,
        inputs: Sequence[tuple[str, ...]],
    ) -> tuple[str, ...]:
        self.check_wall_time()
        records: list[AssemblyRecord] = []
        for row, input_ids in zip(rows, inputs, strict=True):
            self._sequence += 1
            records.append(
                AssemblyRecord(
                    row_id=f"r_{self._sequence}",
                    kind=kind,
                    values=_sanitize_mapping(row),
                    lineage=lineage,
                    input_row_ids=tuple(input_ids),
                    expires_at=self._expires_at,
                )
            )
        serialized_bytes = sum(
            len(
                json.dumps(
                    {
                        **record.document(self.job.job_id),
                        "input_row_ids": record.input_row_ids,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            )
            for record in records
        )
        proposed_rows = self._rows + len(records)
        proposed_bytes = self._bytes + serialized_bytes
        if proposed_rows > self.policy.max_rows:
            raise AssemblyFailure(
                _budget_refusal(
                    "runtime",
                    "assembly_materialized_rows",
                    proposed_rows,
                    self.policy.max_rows,
                )
            )
        if proposed_bytes > self.policy.max_serialized_bytes:
            raise AssemblyFailure(
                _budget_refusal(
                    "runtime",
                    "assembly_serialized_bytes",
                    proposed_bytes,
                    self.policy.max_serialized_bytes,
                )
            )
        try:
            self.job.write(records)
        except Exception as exc:  # noqa: BLE001
            raise AssemblyFailure(
                AssemblyRefusal(
                    code="assembly_backend_write_failed",
                    phase="runtime",
                    message=f"assembly backend write failed: {_safe_exception(exc)}",
                )
            ) from exc
        self._rows = proposed_rows
        self._bytes = proposed_bytes
        self.check_wall_time()
        return tuple(record.row_id for record in records)


class ArangoAssemblyBackend:
    """Production python-arango backend; receives an already connected DB."""

    name = "arango"

    def __init__(
        self,
        db: Any,
        *,
        client: Any | None = None,
        prefix: str = "cdf_assembly",
    ) -> None:
        self._db = db
        self._client = client
        self._prefix = prefix

    def create_job(self, job_id: str, ttl_seconds: int) -> ArangoAssemblyJob:
        job = ArangoAssemblyJob(
            self._db,
            job_id=job_id,
            ttl_seconds=ttl_seconds,
            prefix=self._prefix,
        )
        job.create()
        return job

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if not callable(close):
            close = getattr(self._db, "close", None)
        if callable(close):
            close()


class ArangoAssemblyJob:
    """Job-scoped named graph and vertex/edge collections."""

    def __init__(
        self,
        db: Any,
        *,
        job_id: str,
        ttl_seconds: int,
        prefix: str,
    ) -> None:
        self._db = db
        self.job_id = job_id
        self.ttl_seconds = ttl_seconds
        safe_id = re.sub(r"[^A-Za-z0-9_]", "_", job_id)
        self.graph_name = f"{prefix}_{safe_id}"
        self.vertex_collection = f"{self.graph_name}_v"
        self.edge_collection = f"{self.graph_name}_e"

    def create(self) -> None:
        created: list[str] = []
        graph_created = False
        try:
            vertices = self._db.create_collection(self.vertex_collection)
            created.append(self.vertex_collection)
            edges = self._db.create_collection(self.edge_collection, edge=True)
            created.append(self.edge_collection)
            vertices.add_ttl_index(
                fields=["expires_at"],
                expiry_time=0,
                name=f"{self.vertex_collection}_ttl",
            )
            edges.add_ttl_index(
                fields=["expires_at"],
                expiry_time=0,
                name=f"{self.edge_collection}_ttl",
            )
            self._db.create_graph(
                self.graph_name,
                edge_definitions=[
                    {
                        "edge_collection": self.edge_collection,
                        "from_vertex_collections": [self.vertex_collection],
                        "to_vertex_collections": [self.vertex_collection],
                    }
                ],
            )
            graph_created = True
        except Exception as exc:
            rollback_errors = self._drop_resources(
                graph_created=graph_created,
                collections=created,
            )
            if rollback_errors:
                raise RuntimeError(
                    f"assembly setup failed: {_safe_exception(exc)}; "
                    + "; ".join(rollback_errors)
                ) from exc
            raise

    def write(self, records: Sequence[AssemblyRecord]) -> None:
        if not records:
            return
        vertices = [record.document(self.job_id) for record in records]
        edges: list[dict[str, Any]] = []
        for record in records:
            for ordinal, input_id in enumerate(record.input_row_ids):
                edges.append(
                    {
                        "_key": f"{record.row_id}_{ordinal}",
                        "_from": f"{self.vertex_collection}/{record.row_id}",
                        "_to": f"{self.vertex_collection}/{input_id}",
                        "job_id": self.job_id,
                        "relation": "derived_from",
                        "expires_at": record.expires_at,
                    }
                )
        self._db.collection(self.vertex_collection).insert_many(vertices)
        if edges:
            self._db.collection(self.edge_collection).insert_many(edges)

    def cleanup(self) -> None:
        errors = self._drop_resources(
            graph_created=True,
            collections=[self.edge_collection, self.vertex_collection],
        )
        if errors:
            raise RuntimeError("; ".join(errors))

    def _drop_resources(
        self,
        *,
        graph_created: bool,
        collections: Sequence[str],
    ) -> list[str]:
        errors: list[str] = []
        if graph_created:
            try:
                self._db.delete_graph(
                    self.graph_name,
                    drop_collections=False,
                    ignore_missing=True,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"graph cleanup failed: {_safe_exception(exc)}")
        for name in collections:
            try:
                self._db.delete_collection(name, ignore_missing=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"collection cleanup failed: {_safe_exception(exc)}")
        return errors


def new_job_id() -> str:
    """Return an unpredictable identifier safe for Arango resource names."""
    return secrets.token_hex(16)


def virtual_metrics() -> AssemblyMetrics:
    return AssemblyMetrics(mode="virtual", cleanup_status="not_applicable")


def cleanup_refusal(error: BaseException) -> AssemblyRefusal:
    return AssemblyRefusal(
        code="assembly_cleanup_failed",
        phase="cleanup",
        message=f"assembly cleanup failed: {_safe_exception(error)}",
    )


def backend_refusal(error: BaseException) -> AssemblyRefusal:
    return AssemblyRefusal(
        code="assembly_backend_create_failed",
        phase="setup",
        message=f"assembly backend setup failed: {_safe_exception(error)}",
    )


def _budget_refusal(
    phase: str,
    metric: str,
    observed: int | float,
    limit: int | float,
) -> AssemblyRefusal:
    return AssemblyRefusal(
        code="assembly_budget_exceeded",
        phase=phase,
        metric=metric,
        observed=observed,
        limit=limit,
        message=f"{metric} {observed} exceeds assembly limit {limit}",
    )


def _redact(value: str | None) -> str | None:
    return redact(value)


def _sanitize_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = sanitize(values)
    assert isinstance(sanitized, dict)
    return sanitized


def _safe_exception(error: BaseException) -> str:
    return scrub_exception(error)


def _positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    try:
        value = default if raw is None or not raw.strip() else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name)
    try:
        value = default if raw is None or not raw.strip() else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value

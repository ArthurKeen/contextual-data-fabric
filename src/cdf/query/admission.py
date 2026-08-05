"""Preflight admission and runtime resource policies (P2.2 WP-11)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .optimizer import PlanEstimate


@dataclass(frozen=True)
class AdmissionRefusal:
    """Structured reason a plan was not admitted or was stopped."""

    code: str
    phase: str
    metric: str
    observed: int | float
    limit: int | float
    message: str


@dataclass(frozen=True)
class PlanAdmissionPolicy:
    """Optional estimate/runtime caps. ``None`` disables a numeric cap."""

    max_estimated_rows: int | None = None
    max_estimated_bytes: int | None = None
    max_estimated_cost_usd: float | None = None
    runtime_wall_time_ms: float | None = None
    max_intermediate_rows: int | None = None
    max_final_rows: int | None = None
    seed_batch_rows: int = 1000
    max_seed_rows: int = 10_000
    max_resolution_calls: int = 1000
    resolution_batch_size: int = 100
    resolution_deadline_ms: float = 5000.0
    allow_partial_on_runtime_cap: bool = False

    def __post_init__(self) -> None:
        for name in (
            "max_estimated_rows",
            "max_estimated_bytes",
            "max_estimated_cost_usd",
            "runtime_wall_time_ms",
            "max_intermediate_rows",
            "max_final_rows",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.seed_batch_rows <= 0:
            raise ValueError("seed_batch_rows must be positive")
        if self.max_seed_rows <= 0:
            raise ValueError("max_seed_rows must be positive")
        if self.seed_batch_rows > self.max_seed_rows:
            raise ValueError("seed_batch_rows cannot exceed max_seed_rows")
        if self.max_resolution_calls <= 0:
            raise ValueError("max_resolution_calls must be positive")
        if self.resolution_batch_size <= 0:
            raise ValueError("resolution_batch_size must be positive")
        if not math.isfinite(self.resolution_deadline_ms) or self.resolution_deadline_ms <= 0:
            raise ValueError("resolution_deadline_ms must be a positive finite number")

    def preflight(self, estimate: PlanEstimate) -> AdmissionRefusal | None:
        checks: tuple[tuple[str, int | float, int | float | None], ...] = (
            ("estimated_rows", estimate.estimated_rows, self.max_estimated_rows),
            ("estimated_bytes", estimate.estimated_bytes, self.max_estimated_bytes),
        )
        for metric, observed, limit in checks:
            if limit is not None and observed > limit:
                return _refusal("preflight_estimate_exceeded", "preflight", metric, observed, limit)
        if (
            self.max_estimated_cost_usd is not None
            and estimate.estimated_cost_usd is not None
            and estimate.estimated_cost_usd > self.max_estimated_cost_usd
        ):
            return _refusal(
                "preflight_estimate_exceeded",
                "preflight",
                "estimated_cost_usd",
                estimate.estimated_cost_usd,
                self.max_estimated_cost_usd,
            )
        if (
            estimate.estimated_resolution_calls is not None
            and estimate.estimated_resolution_calls > self.max_resolution_calls
        ):
            return _refusal(
                "preflight_resolution_calls_exceeded",
                "preflight",
                "estimated_resolution_calls",
                estimate.estimated_resolution_calls,
                self.max_resolution_calls,
            )
        return None

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> PlanAdmissionPolicy:
        """Parse the ``CDF_*`` resource guardrails from an environment mapping."""
        return cls(
            max_estimated_rows=_optional_int(environ, "CDF_MAX_ESTIMATED_ROWS"),
            max_estimated_bytes=_optional_int(environ, "CDF_MAX_ESTIMATED_BYTES"),
            max_estimated_cost_usd=_optional_float(
                environ, "CDF_MAX_ESTIMATED_COST_USD"
            ),
            runtime_wall_time_ms=_optional_float(environ, "CDF_RUNTIME_WALL_TIME_MS"),
            max_intermediate_rows=_optional_int(environ, "CDF_MAX_INTERMEDIATE_ROWS"),
            max_final_rows=_optional_int(environ, "CDF_MAX_FINAL_ROWS"),
            seed_batch_rows=_int_with_default(environ, "CDF_SEED_BATCH_ROWS", 1000),
            max_seed_rows=_int_with_default(environ, "CDF_MAX_SEED_ROWS", 10_000),
            max_resolution_calls=_int_with_default(
                environ, "CDF_MAX_RESOLUTION_CALLS", 1000
            ),
            resolution_batch_size=_int_with_default(
                environ, "CDF_RESOLUTION_BATCH_SIZE", 100
            ),
            resolution_deadline_ms=_positive_float_with_default(
                environ, "CDF_RESOLUTION_DEADLINE_MS", 5000.0
            ),
            allow_partial_on_runtime_cap=_boolean(
                environ.get("CDF_ALLOW_PARTIAL_ON_RUNTIME_CAP", "")
            ),
        )


def runtime_refusal(
    code: str,
    metric: str,
    observed: int | float,
    limit: int | float,
) -> AdmissionRefusal:
    return _refusal(code, "runtime", metric, observed, limit)


def _refusal(
    code: str,
    phase: str,
    metric: str,
    observed: int | float,
    limit: int | float,
) -> AdmissionRefusal:
    return AdmissionRefusal(
        code=code,
        phase=phase,
        metric=metric,
        observed=observed,
        limit=limit,
        message=f"{metric} {observed} exceeds configured limit {limit}",
    )


def _optional_int(
    environ: Mapping[str, str],
    name: str,
    *,
    default: int | None = None,
) -> int | None:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _int_with_default(environ: Mapping[str, str], name: str, default: int) -> int:
    value = _optional_int(environ, name, default=default)
    assert value is not None
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _optional_float(environ: Mapping[str, str], name: str) -> float | None:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return value


def _positive_float_with_default(
    environ: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    value = _optional_float(environ, name)
    selected = default if value is None else value
    if selected <= 0:
        raise ValueError(f"{name} must be a positive number")
    return selected


def _boolean(value: str) -> bool:
    return value.strip().casefold() in ("1", "true", "yes")

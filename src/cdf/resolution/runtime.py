"""Plan-scoped runtime normalization of source bindings to canonical IDs."""

from __future__ import annotations

import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .contracts import ResolveEvidence, ResolveRequest, ResolveResult


class EntityResolver(Protocol):
    """CDF-owned injection seam; implementations return guarded CDF results."""

    def resolve(self, request: ResolveRequest) -> ResolveResult: ...


class RuntimeResolutionBinding(Protocol):
    """Catalog binding shape consumed without depending on a backend package."""

    mode: str
    join_variable: str | None
    canonical_key_regex: str | None
    canonical_key_prefix: str | None
    scope_binding_variable: str | None
    observable_bindings: Mapping[str, str]
    policy_profile: str | None
    resolver: str | None


@dataclass(frozen=True)
class ResolutionEvent:
    """PII-free evidence for one source row's normalization decision."""

    source_id: str
    status: str
    reason: str
    resolver: str
    profile: str
    canonical_id: str | None = None
    score: float | None = None
    margin: float | None = None
    evidence: ResolveEvidence | None = None
    cache_hit: bool = False
    bypassed: bool = False
    duration_ms: float = 0.0

    def summary(self) -> ResolutionEventSummary:
        """Return the intentionally reduced form allowed in assembly lineage."""
        return ResolutionEventSummary(
            status=self.status,
            reason=self.reason,
            resolver=self.resolver,
            profile=self.profile,
            cache_hit=self.cache_hit,
            bypassed=self.bypassed,
        )


@dataclass(frozen=True)
class ResolutionEventSummary:
    """Value-free event summary safe to duplicate into temporary lineage."""

    status: str
    reason: str
    resolver: str
    profile: str
    cache_hit: bool
    bypassed: bool


@dataclass(frozen=True)
class ResolutionShortfall:
    """Counted, explicit reason rows were removed before federation."""

    source_id: str
    status: str
    reason: str
    count: int


@dataclass(frozen=True)
class ResolutionRefusal:
    """Fail-closed resolution guard or runtime-cap refusal."""

    code: str
    phase: str
    source_id: str
    reason: str
    message: str
    metric: str | None = None
    observed: int | float | None = None
    limit: int | float | None = None


@dataclass(frozen=True)
class ResolutionLegMetrics:
    """Resolution work and outcomes for one source leg."""

    source_id: str
    calls: int = 0
    cache_hits: int = 0
    bypasses: int = 0
    resolved: int = 0
    abstained: int = 0
    refused: int = 0
    cross_scope: int = 0
    removed_rows: int = 0
    duration_ms: float = 0.0


@dataclass(frozen=True)
class ResolutionPlanMetrics:
    """Deterministic rollup of all source-leg resolution metrics."""

    calls: int = 0
    cache_hits: int = 0
    bypasses: int = 0
    resolved: int = 0
    abstained: int = 0
    refused: int = 0
    cross_scope: int = 0
    removed_rows: int = 0
    duration_ms: float = 0.0
    legs: tuple[ResolutionLegMetrics, ...] = ()


@dataclass(frozen=True)
class ResolutionRowsResult:
    """Normalized rows and all additive runtime metadata for one leg."""

    rows: tuple[dict[str, Any], ...]
    events: tuple[ResolutionEvent, ...]
    shortfalls: tuple[ResolutionShortfall, ...]
    metrics: ResolutionLegMetrics
    refusal: ResolutionRefusal | None = None


class PlanResolutionRuntime:
    """One request's shared deadline, cache, call budget, and resolver lock."""

    def __init__(
        self,
        resolver: EntityResolver,
        *,
        max_calls: int,
        batch_size: int,
        deadline_at: float,
        source_order: Sequence[str] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._resolver = resolver
        self._max_calls = max_calls
        self._batch_size = batch_size
        self._deadline_at = deadline_at
        self._clock = clock
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._source_order = tuple(source_order)
        self._source_index = 0
        self._skipped_sources: set[str] = set()
        self._cache: dict[tuple[Any, ...], ResolveResult] = {}
        self._calls = 0

    def skip(self, source_id: str) -> None:
        """Advance deterministic ordering when a configured source leg failed."""
        with self._condition:
            self._skipped_sources.add(source_id)
            self._advance_skipped()
            self._condition.notify_all()

    def normalize(
        self,
        source_id: str,
        rows: Sequence[Mapping[str, Any]],
        binding: RuntimeResolutionBinding,
    ) -> ResolutionRowsResult:
        """Normalize one source result before it can seed, join, or materialize."""
        started = self._clock()
        join_variable = binding.join_variable
        scope_variable = binding.scope_binding_variable
        resolver_name = binding.resolver or "unconfigured"
        profile = binding.policy_profile or "unconfigured"
        assert join_variable is not None
        assert scope_variable is not None

        row_kinds: list[tuple[str, tuple[Any, ...] | None]] = []
        groups: dict[tuple[Any, ...], ResolveRequest] = {}
        bypass_events: list[ResolutionEvent] = []
        for row in rows:
            join_value = row.get(join_variable)
            if _is_canonical(join_value, binding):
                row_kinds.append(("bypass", None))
                bypass_events.append(
                    ResolutionEvent(
                        source_id=source_id,
                        status="resolved",
                        reason="canonical_key_bypass",
                        resolver=resolver_name,
                        profile=profile,
                        canonical_id=str(join_value),
                        bypassed=True,
                    )
                )
                continue
            attributes = {
                field: row[variable]
                for field, variable in binding.observable_bindings.items()
                if variable in row
            }
            scope = row.get(scope_variable)
            key = _observation_key(binding, scope, attributes)
            row_kinds.append(("resolve", key))
            groups.setdefault(
                key,
                ResolveRequest(
                    account_scope="" if scope is None else str(scope),
                    attributes=attributes,
                    deadline_at=self._deadline_at,
                    request_id=f"{source_id}:runtime",
                ),
            )

        with self._condition:
            self._wait_for_source(source_id)
            preexisting = set(self._cache)
            uncached = [key for key in groups if key not in self._cache]
            if self._calls + len(uncached) > self._max_calls:
                observed = self._calls + len(uncached)
                cap_refusal = ResolutionRefusal(
                    code="max_resolution_calls_exceeded",
                    phase="runtime",
                    source_id=source_id,
                    reason="resolution_call_cap_exceeded",
                    metric="resolution_calls",
                    observed=observed,
                    limit=self._max_calls,
                    message=(
                        f"resolution_calls {observed} exceeds configured limit "
                        f"{self._max_calls}"
                    ),
                )
                metrics = ResolutionLegMetrics(
                    source_id=source_id,
                    bypasses=len(bypass_events),
                    removed_rows=len(rows) - len(bypass_events),
                    duration_ms=(self._clock() - started) * 1000,
                )
                outcome = ResolutionRowsResult(
                    rows=(),
                    events=tuple(bypass_events),
                    shortfalls=(),
                    metrics=metrics,
                    refusal=cap_refusal,
                )
                self._complete_source(source_id)
                return outcome

            calls_before = self._calls
            for offset in range(0, len(uncached), self._batch_size):
                keys = uncached[offset : offset + self._batch_size]
                requests = [groups[key] for key in keys]
                if self._clock() >= self._deadline_at:
                    results = tuple(
                        _synthetic_result(request, "abstained", "deadline_exceeded")
                        for request in requests
                    )
                    attempted = 0
                else:
                    results = self._resolve_batch(requests)
                    attempted = len(requests)
                self._calls += attempted
                for key, result in zip(keys, results, strict=True):
                    self._cache[key] = _validate_result(result, binding)

            call_count = self._calls - calls_before
            seen_in_leg: set[tuple[Any, ...]] = set()
            normalized: list[dict[str, Any]] = []
            events: list[ResolutionEvent] = []
            shortfall_counter: Counter[tuple[str, str]] = Counter()
            bypass_index = 0
            refused_result: ResolveResult | None = None
            cache_hits = 0
            bypasses = 0
            resolved = 0
            abstained = 0
            refused = 0
            cross_scope = 0
            for row, (kind, observation_key) in zip(rows, row_kinds, strict=True):
                if kind == "bypass":
                    event = bypass_events[bypass_index]
                    bypass_index += 1
                    bypasses += 1
                    normalized.append(dict(row))
                    events.append(event)
                    continue
                assert observation_key is not None
                result = self._cache[observation_key]
                cache_hit = (
                    observation_key in preexisting or observation_key in seen_in_leg
                )
                if cache_hit:
                    cache_hits += 1
                seen_in_leg.add(observation_key)
                event = _event(
                    source_id,
                    resolver_name,
                    profile,
                    result,
                    cache_hit=cache_hit,
                )
                events.append(event)
                if result.status == "resolved":
                    resolved += 1
                    rewritten = dict(row)
                    rewritten[join_variable] = result.canonical_id
                    normalized.append(rewritten)
                else:
                    shortfall_counter[(result.status, result.reason)] += 1
                    if result.status == "refused":
                        refused += 1
                        refused_result = refused_result or result
                        if result.reason == "cross_account_candidate":
                            cross_scope += 1
                    else:
                        abstained += 1

            shortfalls = tuple(
                ResolutionShortfall(source_id, status, reason, count)
                for (status, reason), count in sorted(shortfall_counter.items())
            )
            metrics = ResolutionLegMetrics(
                source_id=source_id,
                calls=call_count,
                cache_hits=cache_hits,
                bypasses=bypasses,
                resolved=resolved,
                abstained=abstained,
                refused=refused,
                cross_scope=cross_scope,
                removed_rows=len(rows) - len(normalized),
                duration_ms=(self._clock() - started) * 1000,
            )
            runtime_refusal: ResolutionRefusal | None = None
            if refused_result is not None:
                runtime_refusal = ResolutionRefusal(
                    code="resolution_guard_refused",
                    phase="runtime",
                    source_id=source_id,
                    reason=refused_result.reason,
                    message=(
                        f"runtime resolution refused source {source_id}: "
                        f"{refused_result.reason}"
                    ),
                )
            outcome = ResolutionRowsResult(
                rows=tuple(normalized),
                events=tuple(events),
                shortfalls=shortfalls,
                metrics=metrics,
                refusal=runtime_refusal,
            )
            self._complete_source(source_id)
            return outcome

    def _resolve_batch(self, requests: Sequence[ResolveRequest]) -> tuple[ResolveResult, ...]:
        batch_method = getattr(self._resolver, "resolve_batch", None)
        try:
            if callable(batch_method):
                values = tuple(batch_method(requests, deadline_at=self._deadline_at))
            else:
                values = tuple(self._resolver.resolve(request) for request in requests)
        except Exception:
            return tuple(
                _synthetic_result(request, "abstained", "backend_unavailable")
                for request in requests
            )
        if len(values) != len(requests):
            return tuple(
                _synthetic_result(request, "abstained", "backend_batch_size_invalid")
                for request in requests
            )
        if self._clock() >= self._deadline_at:
            return tuple(
                _synthetic_result(request, "abstained", "deadline_exceeded")
                for request in requests
            )
        return values

    def _wait_for_source(self, source_id: str) -> None:
        if source_id not in self._source_order:
            return
        while (
            self._source_index < len(self._source_order)
            and self._source_order[self._source_index] != source_id
        ):
            self._condition.wait()

    def _complete_source(self, source_id: str) -> None:
        if (
            self._source_index < len(self._source_order)
            and self._source_order[self._source_index] == source_id
        ):
            self._source_index += 1
            self._advance_skipped()
            self._condition.notify_all()

    def _advance_skipped(self) -> None:
        while (
            self._source_index < len(self._source_order)
            and self._source_order[self._source_index] in self._skipped_sources
        ):
            self._source_index += 1


def rollup_resolution_metrics(
    legs: Sequence[ResolutionLegMetrics],
) -> ResolutionPlanMetrics:
    return ResolutionPlanMetrics(
        calls=sum(item.calls for item in legs),
        cache_hits=sum(item.cache_hits for item in legs),
        bypasses=sum(item.bypasses for item in legs),
        resolved=sum(item.resolved for item in legs),
        abstained=sum(item.abstained for item in legs),
        refused=sum(item.refused for item in legs),
        cross_scope=sum(item.cross_scope for item in legs),
        removed_rows=sum(item.removed_rows for item in legs),
        duration_ms=sum(item.duration_ms for item in legs),
        legs=tuple(legs),
    )


def _is_canonical(value: Any, binding: RuntimeResolutionBinding) -> bool:
    if value is None:
        return False
    text = str(value)
    if binding.canonical_key_prefix is not None and not text.startswith(
        binding.canonical_key_prefix
    ):
        return False
    if binding.canonical_key_regex is not None and re.fullmatch(
        binding.canonical_key_regex, text
    ) is None:
        return False
    return binding.canonical_key_prefix is not None or binding.canonical_key_regex is not None


def _observation_key(
    binding: RuntimeResolutionBinding,
    scope: Any,
    attributes: Mapping[str, Any],
) -> tuple[Any, ...]:
    return (
        binding.resolver,
        binding.policy_profile,
        type(scope).__name__,
        repr(scope),
        tuple(
            (field, type(value).__name__, repr(value))
            for field, value in sorted(attributes.items())
        ),
    )


def _validate_result(
    result: ResolveResult,
    binding: RuntimeResolutionBinding,
) -> ResolveResult:
    if result.status != "resolved":
        return result
    if result.evidence is None or result.evidence.profile != binding.policy_profile:
        return replace(
            result,
            status="refused",
            canonical_id=None,
            reason="policy_profile_mismatch",
        )
    if not _is_canonical(result.canonical_id, binding):
        return replace(
            result,
            status="refused",
            canonical_id=None,
            reason="canonical_key_invalid",
        )
    return result


def _synthetic_result(
    request: ResolveRequest,
    status: str,
    reason: str,
) -> ResolveResult:
    assert status in {"abstained", "refused"}
    return ResolveResult(
        status=status,  # type: ignore[arg-type]
        canonical_id=None,
        reason=reason,
        score=None,
        margin=None,
        evidence=None,
        candidate_account_scope=None,
        deadline_at=request.deadline_at,
        elapsed_ms=0.0,
    )


def _event(
    source_id: str,
    resolver: str,
    profile: str,
    result: ResolveResult,
    *,
    cache_hit: bool,
) -> ResolutionEvent:
    return ResolutionEvent(
        source_id=source_id,
        status=result.status,
        reason=result.reason,
        resolver=resolver,
        profile=profile,
        canonical_id=result.canonical_id,
        score=result.score,
        margin=result.margin,
        evidence=result.evidence,
        cache_hit=cache_hit,
        duration_ms=result.elapsed_ms,
    )



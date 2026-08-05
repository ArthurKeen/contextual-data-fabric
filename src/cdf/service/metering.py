"""LLM metering for the NL question path (M9 metrics strip; CC-6 cost story).

:class:`MeteredLLMClient` is a transparent proxy around the ``nl2sparql``
LLM client: it delegates ``generate(messages)`` unchanged and accumulates
wall-clock time and the token counts every ``LLMResponse`` carries
(``prompt_tokens`` / ``completion_tokens`` / ``cached_tokens`` — zero-valued
for scripted test clients, never ``None``). The NL translation code itself
stays untouched: the service wraps the client per request and reads the
meter afterwards.

Cost uses ``arango_sparql.nl2sparql.cost.estimate_llm_cost_usd`` when the
NL engine is installed. Per that module's contract an unknown
(provider, model) pair estimates to ``0.0`` meaning *unpriced*, so it is
reported here as ``None`` — unpriced is not free.
"""

from __future__ import annotations

import time
from typing import Any

from cdf.query.grounding import NlMetrics

REGISTRY_METRICS = NlMetrics(path="registry", cost_usd=0.0)
"""The metrics block for a prepared-question hit: no LLM call, genuinely $0."""

DETERMINISTIC_METRICS = NlMetrics(path="deterministic", cost_usd=0.0)
"""The metrics block for an exact corpus route: no LLM call, genuinely $0."""


def estimate_cost_usd(
    provider: str | None,
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> float | None:
    """USD estimate for one translation, or ``None`` when it can't be priced."""
    if prompt_tokens == 0 and completion_tokens == 0:
        return 0.0
    try:
        from arango_sparql.nl2sparql.cost import estimate_llm_cost_usd
    except ImportError:  # NL engine not installed — tokens known, price not
        return None
    cost = estimate_llm_cost_usd(provider or "", model or "", prompt_tokens, completion_tokens)
    return cost if cost > 0 else None  # 0.0 = unpriced per cost.py's contract


class MeteredLLMClient:
    """Wraps an LLM client; counts calls, tokens, and wall-clock time."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0
        self.duration_ms = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_tokens = 0

    def generate(self, messages: list[dict[str, str]]) -> Any:
        start = time.perf_counter()
        try:
            response = self.inner.generate(messages)
        finally:
            self.duration_ms += (time.perf_counter() - start) * 1000.0
            self.calls += 1
        self.prompt_tokens += int(getattr(response, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(response, "completion_tokens", 0) or 0)
        self.cached_tokens += int(getattr(response, "cached_tokens", 0) or 0)
        return response

    def metrics(self) -> NlMetrics:
        provider = getattr(self.inner, "provider", None)
        model = getattr(self.inner, "model", None)
        return NlMetrics(
            path="llm",
            duration_ms=round(self.duration_ms, 3),
            llm_calls=self.calls,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cached_tokens=self.cached_tokens,
            cost_usd=estimate_cost_usd(
                provider, model, self.prompt_tokens, self.completion_tokens
            ),
            provider=provider,
            model=model,
        )

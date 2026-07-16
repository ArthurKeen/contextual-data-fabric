"""Ontop-backed relational :class:`SourceExecutor` (M5 / B1).

Ontop (Apache-2.0) is a Virtual Knowledge Graph engine: given an R2RML mapping
(emitted by r2g, WP-A4) it answers SPARQL over a live relational database by
rewriting to SQL — no data movement. This adapter sends a partitioned sub-query
(already SPARQL, full-IRI, from E1) to an Ontop SPARQL endpoint and parses the
SPARQL 1.1 JSON results into the :class:`~cdf.query.executor.SourceResult` the
federated executor (E2) joins.

The HTTP call is injected as a ``transport`` so the parsing/mapping logic is
unit-testable without a live service; the default transport uses the standard
library (no extra dependency). Point a real deployment at ``deploy/ontop`` (see
its README) and pass ``endpoint=".../sparql"``.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from cdf.query.executor import Binding, SourceResult
from cdf.query.types import SubQuery

# A transport takes a SPARQL query string and returns a parsed SPARQL 1.1 Query
# Results JSON object ({"head": ..., "results": {"bindings": [...]}}).
Transport = Callable[[str], dict[str, Any]]

_RESULTS_JSON = "application/sparql-results+json"


def _coerce(cell: dict[str, Any]) -> Any:
    """Coerce one SPARQL result cell to a Python value by its datatype."""
    value = cell.get("value")
    datatype = cell.get("datatype", "")
    if not isinstance(value, str):
        return value
    if datatype:
        dt = datatype.rsplit("#", 1)[-1].lower()
        try:
            if dt in {"integer", "int", "long", "short", "byte", "nonnegativeinteger"}:
                return int(value)
            if dt in {"decimal", "double", "float"}:
                return float(value)
            if dt == "boolean":
                return value.strip().lower() in {"true", "1"}
        except ValueError:
            return value
    return value


def _parse_results(results: dict[str, Any]) -> tuple[Binding, ...]:
    """Parse SPARQL 1.1 JSON results into a tuple of bindings (bare var → value)."""
    bindings = (results.get("results") or {}).get("bindings") or []
    return tuple(
        {var: _coerce(cell) for var, cell in row.items()} for row in bindings
    )


def _urllib_transport(endpoint: str, timeout: float) -> Transport:
    """Default transport: POST the query to a SPARQL endpoint via urllib."""

    def transport(sparql: str) -> dict[str, Any]:
        data = urllib.parse.urlencode({"query": sparql}).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Accept": _RESULTS_JSON,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — trusted, configured endpoint
            return json.loads(response.read().decode("utf-8"))

    return transport


class OntopExecutor:
    """Executes a sub-query against an Ontop SPARQL endpoint."""

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        transport: Transport | None = None,
        source_objects: tuple[str, ...] = (),
        timeout: float = 30.0,
        clock: Callable[[], str] | None = None,
    ) -> None:
        """
        Args:
            endpoint: the Ontop SPARQL endpoint URL (e.g.
                ``http://localhost:8080/sparql``). Required unless ``transport``
                is supplied.
            transport: override the HTTP call (dependency injection for tests).
            source_objects: physical objects this endpoint serves (e.g. the
                mapped tables), recorded on each :class:`SourceResult` for
                citations (FR-2).
            timeout: HTTP timeout in seconds for the default transport.
            clock: returns the as-of stamp for a live leg (FR-12); defaults to
                UTC now in ISO-8601.
        """
        if transport is None:
            if not endpoint:
                raise ValueError("OntopExecutor requires an endpoint or a transport")
            transport = _urllib_transport(endpoint, timeout)
        self._transport = transport
        self._source_objects = tuple(source_objects)
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def execute(self, subquery: SubQuery) -> SourceResult:
        results = self._transport(subquery.sparql)
        return SourceResult(
            rows=_parse_results(results),
            # Ontop rewrites this SPARQL to SQL; the SPARQL sent is the stable
            # citation text (the generated SQL is available in Ontop's query log
            # / explain — surfaced in a later pass).
            native_query=subquery.sparql,
            as_of=self._clock(),
            source_objects=self._source_objects,
        )

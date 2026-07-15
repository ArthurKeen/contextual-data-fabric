"""Golden seed-question regression harness (M10 / F1).

A golden case is a declarative JSON document describing one federated question,
the sources that answer it (each a ``CSI`` document + fixture rows), and the
expected outcome. :func:`run_golden` drives the real M5 pipeline
(:func:`~cdf.query.partition_query` → :func:`~cdf.query.execute_plan` →
:func:`~cdf.query.ground`) with fixture-backed executors and diffs the produced
:class:`~cdf.query.grounding.AnswerEnvelope` against the expectations.

Case schema (only the keys you assert on are checked)::

    {
      "name": "...",
      "question": "PREFIX ... SELECT ...",
      "allow_partial": false,                       # optional, default false
      "sources": [
        { "csi": { ...CSI v1 document... },
          "data": { "rows": [ {"var": value, ...}, ... ],
                    "native_query": "SELECT ...",   # optional
                    "source_objects": ["public.orders"],  # optional
                    "as_of": "2026-07-15T...",      # optional
                    "fail": false } }               # optional, simulate a downed leg
      ],
      "expect": {
        "status": "grounded",                       # optional
        "bindings": [ {"name": "Acme"} ],           # optional, compared as a bag
        "sources_touched": ["postgresql:crm"],      # optional, the OK legs
        "failed_sources": ["arango:tickets"],       # optional
        "citations": [ {"source_id": "...", "source_objects": ["..."]} ],  # optional
        "refusal_contains": ["name"]                # optional substrings
      }
    }
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdf.query import execute_plan, ground, partition_query
from cdf.query.catalog import SourceCatalog, source_ref_from_csi
from cdf.query.executor import SourceResult
from cdf.query.grounding import AnswerEnvelope


class _FixtureExecutor:
    """A source executor that replays fixture rows (or simulates a failure)."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def execute(self, subquery: Any) -> SourceResult:
        if self._data.get("fail"):
            raise RuntimeError(self._data.get("error", "source unavailable"))
        return SourceResult(
            rows=tuple(dict(r) for r in self._data.get("rows", [])),
            native_query=self._data.get("native_query"),
            as_of=self._data.get("as_of"),
            source_objects=tuple(self._data.get("source_objects", [])),
        )


@dataclass(frozen=True)
class GoldenOutcome:
    """Result of running one golden case."""

    name: str
    passed: bool
    mismatches: tuple[str, ...] = ()
    envelope: AnswerEnvelope | None = None


def load_goldens(directory: str | Path) -> list[dict[str, Any]]:
    """Load every ``*.json`` golden case in *directory*, sorted by filename."""
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path(directory).glob("*.json"))
    ]


def _bag(rows: Iterable[dict[str, Any]]) -> list[tuple]:
    """Order-insensitive canonical form for a bag of bindings."""
    return sorted(tuple(sorted(r.items())) for r in rows)


def run_golden(case: dict[str, Any]) -> GoldenOutcome:
    """Execute one golden case through the M5 pipeline and diff vs. expectations."""
    name = case.get("name", "<unnamed>")
    sources = case.get("sources", [])
    csi_docs = [s["csi"] for s in sources]

    catalog = SourceCatalog.from_csi_documents(csi_docs)
    executors = {
        source_ref_from_csi(s["csi"]).source_id: _FixtureExecutor(s.get("data", {}))
        for s in sources
    }

    plan = partition_query(case["question"], catalog)
    result = execute_plan(plan, executors)
    envelope = ground(result, allow_partial=bool(case.get("allow_partial", False)))

    mismatches = _diff(case.get("expect", {}), envelope)
    return GoldenOutcome(
        name=name, passed=not mismatches, mismatches=tuple(mismatches), envelope=envelope
    )


def _diff(expect: dict[str, Any], env: AnswerEnvelope) -> list[str]:
    out: list[str] = []

    if "status" in expect and env.status != expect["status"]:
        out.append(f"status: expected {expect['status']!r}, got {env.status!r}")

    if "bindings" in expect:
        want = _bag(expect["bindings"])
        got = _bag(dict(b) for b in env.bindings)
        if want != got:
            out.append(f"bindings: expected {want}, got {got}")

    if "sources_touched" in expect:
        touched = sorted(s.source_id for s in env.retrieval_path if s.status == "ok")
        if touched != sorted(expect["sources_touched"]):
            out.append(
                f"sources_touched: expected {sorted(expect['sources_touched'])}, got {touched}"
            )

    if "failed_sources" in expect:
        if sorted(env.failed_sources) != sorted(expect["failed_sources"]):
            out.append(
                f"failed_sources: expected {sorted(expect['failed_sources'])}, "
                f"got {sorted(env.failed_sources)}"
            )

    if "citations" in expect:
        by_id = {c.source_id: c for c in env.citations}
        for want_c in expect["citations"]:
            sid = want_c["source_id"]
            cite = by_id.get(sid)
            if cite is None:
                out.append(f"citation: expected a citation for {sid!r}, none found")
                continue
            if "source_objects" in want_c and sorted(cite.source_objects) != sorted(
                want_c["source_objects"]
            ):
                out.append(
                    f"citation[{sid}].source_objects: expected "
                    f"{sorted(want_c['source_objects'])}, got {sorted(cite.source_objects)}"
                )

    for substr in expect.get("refusal_contains", []):
        if not env.refusal_reason or substr not in env.refusal_reason:
            out.append(f"refusal_reason missing {substr!r} (got {env.refusal_reason!r})")

    return out

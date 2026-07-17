"""The engine's HTTP seam: ``POST /federate`` → grounded envelope (M5/M9).

This is the thin service the demo UI (M9) calls — the library pipeline
(:func:`cdf.query.partition_query` → :func:`cdf.query.execute_plan` →
:func:`cdf.query.ground`) behind one endpoint. It owns **no** query logic.

Two ways in, per the P1 plan:

- ``sparql`` — a conceptual query, run as-is (the D1 NL front-end will emit
  these; agents and tests can already send them).
- ``question`` — natural language, resolved against a **prepared-questions
  registry** (the M9 "pre-run" demo mode: the seed questions mapped to their
  conceptual queries). An unknown question is *refused, not guessed* — the
  honest answer until WP-D1 wires the LLM front-end.

Credentials stay in the engine per CC-7: executors are built from environment
variables by :func:`FederationService.from_env`; the HTTP surface never sees a
connection string.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cdf.query import SourceCatalog, execute_plan, ground, partition_query
from cdf.query.executor import SourceExecutor
from cdf.query.grounding import AnswerEnvelope
from cdf.query.planner import UnsupportedQueryError


@dataclass(frozen=True)
class FederationService:
    """The wired engine: a catalog, executors, and optional prepared questions."""

    catalog: SourceCatalog
    executors: Mapping[str, SourceExecutor]
    prepared_questions: Mapping[str, str] = field(default_factory=dict)

    def federate_sparql(self, sparql: str, *, allow_partial: bool = False) -> AnswerEnvelope:
        plan = partition_query(sparql, self.catalog)
        result = execute_plan(plan, self.executors)
        return ground(result, allow_partial=allow_partial)

    def federate_question(self, question: str, *, allow_partial: bool = False) -> AnswerEnvelope:
        sparql = self.prepared_questions.get(_normalize(question))
        if sparql is None:
            # Refuse, never guess: NL→IR is WP-D1; until then only prepared
            # (seed) questions are answerable by name.
            return AnswerEnvelope(
                status="refused",
                bindings=(),
                citations=(),
                retrieval_path=(),
                refusal_reason=(
                    "question is not in the prepared-question registry and the "
                    "NL→conceptual-query front-end (WP-D1) is not wired yet; "
                    "send 'sparql' instead"
                ),
            )
        return self.federate_sparql(sparql, allow_partial=allow_partial)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> FederationService:
        """Wire the two P1 legs from the environment (CC-7/CC-8).

        - ``CDF_CSI_DIR`` — directory of ``*.json`` CSI v1 documents (default
          ``deploy/csi``). Every document becomes a catalog entry.
        - Arango leg (kind ``arango``): ``ARANGO_URL`` (+ ``ARANGO_DB``,
          ``ARANGO_USER``, ``ARANGO_PASSWORD``).
        - Relational leg (any other kind): ``ONTOP_SPARQL_ENDPOINT``.
        - ``CDF_PREPARED_QUESTIONS`` — JSON file of ``{question: sparql}``.
        """
        env = dict(environ if environ is not None else os.environ)

        csi_dir = Path(env.get("CDF_CSI_DIR", "deploy/csi"))
        docs = [json.loads(p.read_text()) for p in sorted(csi_dir.glob("*.json"))]
        catalog = SourceCatalog.from_csi_documents(docs)

        executors: dict[str, SourceExecutor] = {}
        for doc in docs:
            from cdf.query.catalog import source_ref_from_csi

            ref = source_ref_from_csi(doc)
            if ref.kind == "arango" and env.get("ARANGO_URL"):
                import arango  # lazy: engine lib, not a core dep

                from cdf.adapters import ArangoExecutor

                db = arango.ArangoClient(hosts=env["ARANGO_URL"]).db(
                    env.get("ARANGO_DB", "cmf"),
                    username=env.get("ARANGO_USER", "root"),
                    password=env.get("ARANGO_PASSWORD", ""),
                )
                executors[ref.source_id] = ArangoExecutor(
                    csi=doc, db=db, source_objects=(ref.ref,)
                )
            elif ref.kind != "arango" and env.get("ONTOP_SPARQL_ENDPOINT"):
                from cdf.adapters import OntopExecutor

                executors[ref.source_id] = OntopExecutor(
                    endpoint=env["ONTOP_SPARQL_ENDPOINT"], source_objects=(ref.ref,)
                )

        questions: dict[str, str] = {}
        questions_file = env.get("CDF_PREPARED_QUESTIONS")
        if questions_file:
            raw = json.loads(Path(questions_file).read_text())
            questions = {_normalize(q): s for q, s in raw.items()}

        return cls(catalog=catalog, executors=executors, prepared_questions=questions)


def _normalize(question: str) -> str:
    return " ".join(question.lower().split())


def app_from_env() -> Any:
    """Uvicorn factory entry point::

        uvicorn --factory cdf.service.app:app_from_env --port 8600
    """
    return create_app(FederationService.from_env())


try:  # the service extra is optional; the library core must import without it
    from pydantic import BaseModel

    class FederateRequest(BaseModel):
        """Body of ``POST /federate`` — exactly one of ``sparql``/``question``.

        Module-scoped on purpose: under ``from __future__ import annotations``
        FastAPI resolves the endpoint's string annotations against module
        globals, and a closure-local model silently degrades to a query param.
        """

        sparql: str | None = None
        question: str | None = None
        allow_partial: bool = False

except ModuleNotFoundError:  # pragma: no cover
    FederateRequest = None  # type: ignore[assignment,misc]


def create_app(service: FederationService) -> Any:
    """Build the FastAPI app around a wired :class:`FederationService`."""
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Contextual Data Fabric — federated query engine (M5)")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "sources": sorted(service.executors.keys()),
            "prepared_questions": len(service.prepared_questions),
        }

    @app.post("/federate")
    def federate(req: FederateRequest) -> dict[str, Any]:
        if bool(req.sparql) == bool(req.question):
            raise HTTPException(422, "provide exactly one of 'sparql' or 'question'")
        try:
            if req.sparql:
                envelope = service.federate_sparql(req.sparql, allow_partial=req.allow_partial)
            else:
                envelope = service.federate_question(
                    req.question or "", allow_partial=req.allow_partial
                )
        except UnsupportedQueryError as exc:
            # Planner refusal is a feature (never a silently-wrong partition);
            # surface it as a client error with the reason.
            raise HTTPException(422, f"unsupported query construct: {exc}") from exc
        return asdict(envelope)

    return app

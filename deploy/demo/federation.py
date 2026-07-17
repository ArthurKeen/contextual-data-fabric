"""Live federated-query demo wiring (M5 end-to-end).

Ties the whole engine to the two live sources brought up under ``deploy/``:

    question (SPARQL) ─▶ E1 partition ─┬─▶ OntopExecutor  ─▶ Ontop  ─▶ Postgres
                                       └─▶ ArangoExecutor ─▶ ArangoDB (via arango-sparql-py)
                        ─▶ E2 join on the account_id business key ─▶ E3 cite-or-refuse

Both the CLI (``federated_demo.py``) and the browser UI (``server.py``) call
:func:`run`. Endpoints come from the environment (defaults match the two
``deploy/`` stacks on their demo ports).
"""

from __future__ import annotations

import os
from typing import Any

from cdf.adapters import ArangoExecutor, OntopExecutor
from cdf.query import ground, partition_query
from cdf.query.catalog import SourceCatalog

ONTOP_ENDPOINT = os.getenv("ONTOP_SPARQL_ENDPOINT", "http://localhost:8090/sparql")
ARANGO_URL = os.getenv("ARANGO_URL", "http://localhost:8530")
ARANGO_DB = os.getenv("ARANGO_DB", "cmf")
ARANGO_USER = os.getenv("ARANGO_USER", "root")
ARANGO_PASSWORD = os.getenv("ARANGO_PASSWORD", "cdf")

# Relational source (Ontop/Postgres): Accounts, keyed by the account_id business
# key. Matches deploy/ontop/{seed.sql,input/mapping.ttl}.
ACCOUNT_CSI: dict[str, Any] = {
    "csiVersion": "1",
    "conceptualModel": {
        "entities": [
            {
                "name": "Account",
                "properties": [{"name": "account_id"}, {"name": "name"}, {"name": "arr"}],
            }
        ],
        "relationships": [],
    },
    "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
    "provenance": {"producer": "r2g", "direction": "forward",
                   "source": {"kind": "postgresql", "ref": "crm"}},
}

# Graph source (ArangoDB via arango-sparql-py): SupportTickets, same account_id.
# Matches deploy/arango/seed.py.
TICKET_CSI: dict[str, Any] = {
    "csiVersion": "1",
    "conceptualModel": {
        "entities": [
            {
                "name": "Ticket",
                "properties": [
                    {"name": "subject"}, {"name": "severity"}, {"name": "account_id"}
                ],
            }
        ],
        "relationships": [],
    },
    "arangoPhysicalMapping": {
        "entities": {"Ticket": {"style": "COLLECTION", "collectionName": "tickets"}},
        "relationships": {},
    },
    "provenance": {"producer": "analyzer", "direction": "reverse",
                   "source": {"kind": "arango", "ref": "tickets"}},
}

# The seed federated question: high-value context that spans both sources —
# ticket subjects (graph) joined to their account name + ARR (relational).
DEFAULT_QUESTION = """PREFIX c: <urn:arango-sparql:concept#>
SELECT ?name ?subject ?arr WHERE {
  ?t   a c:Ticket  ; c:subject ?subject ; c:account_id ?aid .
  ?acc a c:Account ; c:account_id ?aid ; c:name ?name ; c:arr ?arr .
}"""


def build_catalog() -> SourceCatalog:
    return SourceCatalog.from_csi_documents([ACCOUNT_CSI, TICKET_CSI])


def build_executors() -> dict[str, Any]:
    from arango import ArangoClient

    db = ArangoClient(hosts=ARANGO_URL).db(
        ARANGO_DB, username=ARANGO_USER, password=ARANGO_PASSWORD
    )
    return {
        "postgresql:crm": OntopExecutor(
            endpoint=ONTOP_ENDPOINT, source_objects=("public.accounts",)
        ),
        "arango:tickets": ArangoExecutor(
            csi=TICKET_CSI, db=db, source_objects=("tickets",)
        ),
    }


def run(question: str | None = None, *, allow_partial: bool = False) -> dict[str, Any]:
    """Run one federated question end-to-end and return a JSON-able result."""
    question = question or DEFAULT_QUESTION
    plan = partition_query(question, build_catalog())
    from cdf.query import execute_plan

    envelope = ground(execute_plan(plan, build_executors()), allow_partial=allow_partial)

    return {
        "question": question,
        "status": envelope.status,
        "join_keys": list(plan.join_keys),
        "bindings": [dict(b) for b in envelope.bindings],
        "sub_queries": [
            {
                "source_id": sq.source.source_id,
                "kind": sq.source.kind,
                "sparql": sq.sparql,
            }
            for sq in plan.sub_queries
        ],
        "citations": [
            {
                "source_id": c.source_id,
                "kind": c.kind,
                "source_objects": list(c.source_objects),
                "native_query": c.native_query,
                "as_of": c.as_of,
                "row_count": c.row_count,
            }
            for c in envelope.citations
        ],
        "retrieval_path": [
            {"source_id": s.source_id, "status": s.status, "row_count": s.row_count}
            for s in envelope.retrieval_path
        ],
        "refusal_reason": envelope.refusal_reason,
    }

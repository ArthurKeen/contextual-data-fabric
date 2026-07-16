"""Opt-in live ArangoDB integration test for the graph leg.

Skipped unless ``ARANGO_URL`` is set (see ``deploy/arango``) and both
``arango-sparql-py`` and ``python-arango`` are importable. Run with::

    ARANGO_URL=http://localhost:8529 ARANGO_DB=cmf ARANGO_PASSWORD=cdf \
        .venv/bin/python -m pytest tests/test_arango_live.py -q
"""

from __future__ import annotations

import os

import pytest

from cdf.adapters import ArangoExecutor
from cdf.query.types import SourceRef, SubQuery

ARANGO_URL = os.getenv("ARANGO_URL")

pytestmark = pytest.mark.skipif(
    not ARANGO_URL, reason="set ARANGO_URL to run the live ArangoDB test"
)

_TICKET_CSI = {
    "csiVersion": "1",
    "conceptualModel": {"entities": [{"name": "Ticket", "properties": [{"name": "subject"}]}]},
    "arangoPhysicalMapping": {
        "entities": {"Ticket": {"style": "COLLECTION", "collectionName": "tickets"}},
        "relationships": {},
    },
    "provenance": {"producer": "analyzer", "direction": "reverse",
                   "source": {"kind": "arango", "ref": "tickets"}},
}


def test_live_arango_answers_a_subquery():
    pytest.importorskip("arango_sparql")
    arango = pytest.importorskip("arango")

    client = arango.ArangoClient(hosts=ARANGO_URL)
    db = client.db(
        os.getenv("ARANGO_DB", "cmf"),
        username=os.getenv("ARANGO_USER", "root"),
        password=os.getenv("ARANGO_PASSWORD", "cdf"),
    )

    sq = SubQuery(
        source=SourceRef(source_id="arango:tickets", kind="arango", ref="tickets"),
        triples=(),
        variables=("?s", "?subject"),
        sparql=(
            "PREFIX c: <urn:arango-sparql:concept#> "
            "SELECT ?s ?subject WHERE { ?s a c:Ticket ; c:subject ?subject }"
        ),
    )
    executor = ArangoExecutor(csi=_TICKET_CSI, db=db, source_objects=("tickets",))
    result = executor.execute(sq)

    subjects = {row.get("subject") for row in result.rows}
    assert "login broken" in subjects, f"expected seeded ticket in {subjects}"
    assert result.source_objects == ("tickets",)
    assert result.as_of is not None

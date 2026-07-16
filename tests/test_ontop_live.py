"""Opt-in live Ontop integration test.

Skipped unless a reachable Ontop SPARQL endpoint is provided via the
``ONTOP_SPARQL_ENDPOINT`` environment variable (see ``deploy/ontop`` to stand
one up). Run with::

    ONTOP_SPARQL_ENDPOINT=http://localhost:8080/sparql .venv/bin/pytest tests/test_ontop_live.py
"""

from __future__ import annotations

import os
import urllib.error

import pytest

from cdf.adapters import OntopExecutor
from cdf.query.types import SourceRef, SubQuery

ENDPOINT = os.getenv("ONTOP_SPARQL_ENDPOINT")

pytestmark = pytest.mark.skipif(
    not ENDPOINT, reason="set ONTOP_SPARQL_ENDPOINT to run the live Ontop test"
)


def test_live_ontop_answers_a_subquery():
    # Matches deploy/ontop/seed.sql + the r2g-generated R2RML (Account/name).
    sq = SubQuery(
        source=SourceRef(source_id="postgresql:crm", kind="postgresql", ref="crm"),
        triples=(),
        variables=("?name",),
        sparql=(
            "SELECT ?name WHERE { ?a a <urn:arango-sparql:concept#Account> ; "
            "<urn:arango-sparql:concept#name> ?name . }"
        ),
    )
    executor = OntopExecutor(endpoint=ENDPOINT, source_objects=("public.accounts",), timeout=15.0)
    try:
        result = executor.execute(sq)
    except (urllib.error.URLError, ConnectionError) as exc:  # pragma: no cover
        pytest.skip(f"Ontop endpoint not reachable: {exc}")

    names = {row.get("name") for row in result.rows}
    assert "Acme" in names, f"expected seeded Account 'Acme' in {names}"
    assert result.as_of is not None
    assert result.source_objects == ("public.accounts",)

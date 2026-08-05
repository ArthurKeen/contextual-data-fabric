"""Unit tests for OntopExecutor (mocked transport — no live service)."""

from __future__ import annotations

import pytest

from cdf.adapters import OntopExecutor
from cdf.query.types import SourceRef, SubQuery

_SQ = SubQuery(
    source=SourceRef(source_id="postgresql:shop", kind="postgresql", ref="shop"),
    triples=(),
    variables=("?name", "?arr"),
    sparql="SELECT ?name ?arr WHERE { ?a <urn:c#name> ?name ; <urn:c#arr> ?arr . }",
)


def _results(bindings):
    return {"head": {"vars": ["name", "arr"]}, "results": {"bindings": bindings}}


def test_parses_bindings_and_coerces_datatypes():
    captured = {}

    def transport(sparql):
        captured["sparql"] = sparql
        return _results(
            [
                {
                    "name": {"type": "literal", "value": "Acme"},
                    "arr": {
                        "type": "literal",
                        "value": "50000",
                        "datatype": "http://www.w3.org/2001/XMLSchema#integer",
                    },
                }
            ]
        )

    ex = OntopExecutor(transport=transport, source_objects=("public.accounts",), clock=lambda: "T0")
    result = ex.execute(_SQ)

    assert result.rows == ({"name": "Acme", "arr": 50000},)  # integer coerced
    assert result.source_objects == ("public.accounts",)
    assert result.as_of == "T0"
    assert result.native_query is None
    # The adapter sent the sub-query's SPARQL verbatim.
    assert captured["sparql"] == _SQ.sparql


def test_records_ontop_reformulated_sql_as_native_query():
    captured = {}

    def reformulate(sparql):
        captured["sparql"] = sparql
        return 'SELECT "name" FROM "accounts"'

    result = OntopExecutor(
        transport=lambda _q: _results([]),
        reformulate_transport=reformulate,
    ).execute(_SQ)

    assert captured["sparql"] == _SQ.sparql
    assert result.native_query == 'SELECT "name" FROM "accounts"'


def test_reformulation_failure_does_not_fail_successful_query():
    def unavailable(_sparql):
        raise ConnectionError("reformulation endpoint unavailable")

    result = OntopExecutor(
        transport=lambda _q: _results([]),
        reformulate_transport=unavailable,
    ).execute(_SQ)

    assert result.rows == ()
    assert result.native_query is None


def test_coerces_decimal_boolean_and_leaves_strings():
    def transport(_sparql):
        return _results(
            [
                {
                    "name": {"type": "literal", "value": "x"},
                    "arr": {"value": "1.5", "datatype": "http://www.w3.org/2001/XMLSchema#decimal"},
                    "flag": {"value": "true", "datatype": "http://www.w3.org/2001/XMLSchema#boolean"},
                }
            ]
        )

    result = OntopExecutor(transport=transport).execute(_SQ)
    row = result.rows[0]
    assert row["name"] == "x"
    assert row["arr"] == 1.5
    assert row["flag"] is True


def test_empty_results():
    result = OntopExecutor(transport=lambda _q: _results([])).execute(_SQ)
    assert result.rows == ()


def test_missing_endpoint_and_transport_raises():
    with pytest.raises(ValueError):
        OntopExecutor()


def test_default_clock_stamps_iso_as_of():
    result = OntopExecutor(transport=lambda _q: _results([])).execute(_SQ)
    assert result.as_of is not None
    # ISO-8601-ish (has a date and a 'T').
    assert "T" in result.as_of and result.as_of[:4].isdigit()


def test_ontop_executor_in_full_pipeline():
    """B1 slots into the real engine: partition -> OntopExecutor -> execute ->
    ground yields a grounded, cited answer (relational leg via a mock Ontop)."""
    from cdf.query import (
        SourceCatalog,
        SourceResult,
        execute_plan,
        ground,
        partition_query,
    )

    catalog = SourceCatalog.from_csi_documents(
        [
            {
                "csiVersion": "1",
                "conceptualModel": {
                    "entities": [{"name": "Account", "properties": [{"name": "name"}]}],
                    "relationships": [
                        {"type": "raised", "fromEntity": "Ticket", "toEntity": "Account"}
                    ],
                },
                "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
                "provenance": {"producer": "r2g", "direction": "forward",
                               "source": {"kind": "postgresql", "ref": "crm"}},
            },
            {
                "csiVersion": "1",
                "conceptualModel": {
                    "entities": [{"name": "Ticket", "properties": [{"name": "subject"}]}]
                },
                "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
                "provenance": {"producer": "analyzer", "direction": "reverse",
                               "source": {"kind": "arango", "ref": "tickets"}},
            },
        ]
    )
    q = (
        "PREFIX c: <urn:arango-sparql:concept#> "
        "SELECT ?name ?subject WHERE { ?t a c:Ticket ; c:subject ?subject ; c:raised ?a . "
        "?a a c:Account ; c:name ?name . }"
    )
    plan = partition_query(q, catalog)

    # Relational leg = real OntopExecutor over a mock SPARQL endpoint.
    ontop = OntopExecutor(
        transport=lambda _q: _results([{"a": {"value": "A1"}, "name": {"value": "Acme"}}]),
        source_objects=("public.accounts",),
        clock=lambda: "2026-07-15T00:00:00Z",
    )

    class _ArangoFake:
        def execute(self, _sq):
            return SourceResult(
                rows=({"t": "T1", "subject": "login broken", "a": "A1"},),
                source_objects=("tickets",),
            )

    env = ground(execute_plan(plan, {"postgresql:crm": ontop, "arango:tickets": _ArangoFake()}))
    assert env.status == "grounded"
    assert env.bindings == ({"name": "Acme", "subject": "login broken"},)
    cite = {c.source_id: c for c in env.citations}["postgresql:crm"]
    assert cite.source_objects == ("public.accounts",)

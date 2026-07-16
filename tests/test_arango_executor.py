"""Tests for ArangoExecutor.

Mostly injected translate/transport (no arango-sparql-py or DB needed); one test
exercises the REAL A3→resolver→translate path when arango-sparql-py is importable
(still no database — translation is pure)."""

from __future__ import annotations

import pytest

from cdf.adapters import ArangoExecutor
from cdf.query.types import SourceRef, SubQuery

_SQ = SubQuery(
    source=SourceRef(source_id="arango:tickets", kind="arango", ref="tickets"),
    triples=(),
    variables=("?s", "?subject"),
    sparql=(
        "PREFIX c: <urn:arango-sparql:concept#> "
        "SELECT ?s ?subject WHERE { ?s a c:Ticket ; c:subject ?subject }"
    ),
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


def test_maps_aql_rows_to_bindings_with_injected_seams():
    captured = {}

    def translate(sparql):
        captured["sparql"] = sparql
        return "FOR d IN tickets RETURN { s: d._uri, subject: d.subject }", {"@@c": "tickets"}

    def transport(aql, bind_vars):
        captured["aql"] = aql
        captured["binds"] = bind_vars
        return [{"s": "tickets/1", "subject": "login broken"}]

    ex = ArangoExecutor(
        translate=translate, transport=transport,
        source_objects=("tickets",), clock=lambda: "T0",
    )
    result = ex.execute(_SQ)

    assert result.rows == ({"s": "tickets/1", "subject": "login broken"},)
    assert result.native_query.startswith("FOR d IN tickets")
    assert result.source_objects == ("tickets",)
    assert result.as_of == "T0"
    assert captured["binds"] == {"@@c": "tickets"}
    assert captured["sparql"] == _SQ.sparql


def test_requires_a_mapping_source():
    with pytest.raises(ValueError):
        ArangoExecutor(transport=lambda a, b: [])  # no csi/bundle/resolver/translate


def test_requires_a_transport_or_db():
    with pytest.raises(ValueError):
        ArangoExecutor(csi=_TICKET_CSI)  # translate can default, but no transport/db


def test_real_translation_via_a3_when_available():
    """CSI --A3--> MappingBundle --> resolver --> real arango-sparql-py AQL.

    Skips if arango-sparql-py isn't installed; uses a fake transport so no DB is
    needed (translation is pure)."""
    pytest.importorskip("arango_sparql")

    rows_seen = {}

    def transport(aql, bind_vars):
        rows_seen["aql"] = aql
        rows_seen["binds"] = bind_vars
        return [{"s": "tickets/1", "subject": "login broken"}]

    ex = ArangoExecutor(csi=_TICKET_CSI, transport=transport, source_objects=("tickets",))
    result = ex.execute(_SQ)

    # Real transpiler output: a FOR loop over the mapped collection, RETURN keyed
    # by the SPARQL variable names.
    assert "FOR " in rows_seen["aql"]
    assert "RETURN" in rows_seen["aql"]
    assert "subject" in rows_seen["aql"]
    assert result.rows == ({"s": "tickets/1", "subject": "login broken"},)


def test_arango_executor_in_full_pipeline():
    """The graph leg slots into partition -> execute -> ground (relational leg
    faked), yielding a grounded cited answer."""
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
            _TICKET_CSI,
        ]
    )
    q = (
        "PREFIX c: <urn:arango-sparql:concept#> "
        "SELECT ?name ?subject WHERE { ?t a c:Ticket ; c:subject ?subject ; c:raised ?a . "
        "?a a c:Account ; c:name ?name . }"
    )
    plan = partition_query(q, catalog)

    arango = ArangoExecutor(
        translate=lambda _s: ("FOR d IN tickets RETURN {}", {}),
        transport=lambda _a, _b: [{"t": "T1", "subject": "login broken", "a": "A1"}],
        source_objects=("tickets",),
        clock=lambda: "2026-07-15T00:00:00Z",
    )

    class _PgFake:
        def execute(self, _sq):
            return SourceResult(
                rows=({"a": "A1", "name": "Acme"},), source_objects=("public.accounts",)
            )

    env = ground(
        execute_plan(plan, {"arango:tickets": arango, "postgresql:crm": _PgFake()})
    )
    assert env.status == "grounded"
    assert env.bindings == ({"name": "Acme", "subject": "login broken"},)
    assert {c.source_id for c in env.citations} == {"arango:tickets", "postgresql:crm"}

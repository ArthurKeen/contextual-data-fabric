"""Tests for the ClickHouse executor (compilation logic, no database).

These prove the R2RML parse + BGP→SQL compiler + row mapping with a fake
transport. The emitted *dialect* is validated only by the opt-in live test
(tests/test_clickhouse_live.py) — a fake transport accepts SQL a real server may
reject (see the arango-sparql-py cross-validation lesson)."""

from __future__ import annotations

import pytest

from cdf.adapters import ClickHouseExecutor
from cdf.adapters.clickhouse import ClickHouseError, compile_sql, parse_r2rml
from cdf.query.types import SourceRef, SubQuery

C = "urn:arango-sparql:concept#"
PREFIX = f"PREFIX c: <{C}>\n"

# R2RML in the exact shape r2g `export-r2rml` emits.
R2RML = f"""
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<#accounts> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "accounts" ] ;
    rr:subjectMap [ rr:template "http://r/accounts/{{id}}" ; rr:class <{C}accounts> ] ;
    rr:predicateObjectMap [ rr:predicate <{C}account_id> ; rr:objectMap [ rr:column "account_id" ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}account_name> ; rr:objectMap [ rr:column "account_name" ] ] .

<#usage_metrics> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "usage_metrics" ] ;
    rr:subjectMap [ rr:template "http://r/usage/{{id}}" ; rr:class <{C}usage_metrics> ] ;
    rr:predicateObjectMap [ rr:predicate <{C}account_id> ; rr:objectMap [ rr:column "account_id" ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}query_volume_m> ;
        rr:objectMap [ rr:column "query_volume_m" ; rr:datatype xsd:double ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}graphrag_enabled> ;
        rr:objectMap [ rr:column "graphrag_enabled" ; rr:datatype xsd:boolean ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}edition> ; rr:objectMap [ rr:column "edition" ] ] .
"""

MAPPING = parse_r2rml(R2RML)


def test_parse_r2rml():
    assert set(MAPPING) == {f"{C}accounts", f"{C}usage_metrics"}
    assert MAPPING[f"{C}accounts"]["table"] == "accounts"
    assert MAPPING[f"{C}usage_metrics"]["columns"][f"{C}query_volume_m"] == "query_volume_m"


def test_single_table_with_literal_filter_and_projection():
    sql = compile_sql(
        PREFIX
        + "SELECT ?qv ?aid WHERE { ?u a c:usage_metrics ; c:edition \"Enterprise\" ; "
        "c:query_volume_m ?qv ; c:account_id ?aid }",
        MAPPING,
    )
    assert "FROM `usage_metrics` AS t0" in sql
    assert "t0.`edition` = 'Enterprise'" in sql
    assert "t0.`query_volume_m` AS `qv`" in sql
    assert "t0.`account_id` AS `aid`" in sql


def test_join_two_tables_on_shared_variable():
    sql = compile_sql(
        PREFIX
        + "SELECT ?account_name ?qv WHERE { "
        "?a a c:accounts ; c:account_id ?k ; c:account_name ?account_name . "
        "?u a c:usage_metrics ; c:account_id ?k ; c:query_volume_m ?qv }",
        MAPPING,
    )
    assert "FROM `accounts` AS t0, `usage_metrics` AS t1" in sql
    # the shared ?k becomes an equi-join
    assert "t0.`account_id` = t1.`account_id`" in sql


def test_bind_join_values_becomes_in_list():
    sql = compile_sql(
        PREFIX
        + "SELECT ?qv ?aid WHERE { ?u a c:usage_metrics ; c:query_volume_m ?qv ; "
        "c:account_id ?aid }\nVALUES (?aid) { (\"ACME\") (\"GLOBEX\") }",
        MAPPING,
    )
    assert "t0.`account_id` IN ('ACME', 'GLOBEX')" in sql


def test_boolean_and_string_literal_encoding():
    sql = compile_sql(
        PREFIX
        + "SELECT ?aid WHERE { ?u a c:usage_metrics ; c:account_id ?aid ; "
        "c:graphrag_enabled true }",
        MAPPING,
    )
    assert "t0.`graphrag_enabled` = 1" in sql  # boolean -> 1/0


def test_string_literal_is_escaped():
    sql = compile_sql(
        PREFIX + "SELECT ?aid WHERE { ?u a c:usage_metrics ; c:account_id ?aid ; "
        "c:edition \"a'b\\\\c\" }",
        MAPPING,
    )
    assert "'a\\'b\\\\c'" in sql  # single-quote and backslash escaped for ClickHouse


def test_executor_compiles_and_maps_rows():
    captured = {}

    def transport(sql):
        captured["sql"] = sql
        return [{"aid": "ACME", "qv": 12.5}, {"aid": "GLOBEX", "qv": 3.0}]

    ex = ClickHouseExecutor(
        mapping=MAPPING, transport=transport, source_objects=("usage_metrics",),
        clock=lambda: "T0",
    )
    sq = SubQuery(
        source=SourceRef(source_id="clickhouse:analytics", kind="clickhouse", ref="analytics"),
        triples=(),
        variables=("?aid", "?qv"),
        sparql=PREFIX
        + "SELECT ?aid ?qv WHERE { ?u a c:usage_metrics ; c:account_id ?aid ; c:query_volume_m ?qv }",
    )
    result = ex.execute(sq)
    assert result.rows == ({"aid": "ACME", "qv": 12.5}, {"aid": "GLOBEX", "qv": 3.0})
    assert result.source_objects == ("usage_metrics",)
    assert result.as_of == "T0"
    assert result.native_query == captured["sql"]
    assert "FROM `usage_metrics`" in result.native_query


def test_executor_can_build_mapping_from_r2rml():
    ex = ClickHouseExecutor(r2rml=R2RML, transport=lambda _s: [])
    assert ex.execute(
        SubQuery(
            source=SourceRef("clickhouse:a", "clickhouse", "a"),
            triples=(),
            variables=("?aid",),
            sparql=PREFIX + "SELECT ?aid WHERE { ?u a c:usage_metrics ; c:account_id ?aid }",
        )
    ).rows == ()


# -- error cases -------------------------------------------------------------


def test_untyped_subject_raises():
    with pytest.raises(ClickHouseError):
        compile_sql(PREFIX + "SELECT ?x WHERE { ?x c:account_id ?y }", MAPPING)


def test_unmapped_class_raises():
    with pytest.raises(ClickHouseError):
        compile_sql(PREFIX + "SELECT ?x WHERE { ?x a c:nope ; c:account_id ?y }", MAPPING)


def test_unmapped_property_raises():
    with pytest.raises(ClickHouseError):
        compile_sql(
            PREFIX + "SELECT ?y WHERE { ?u a c:usage_metrics ; c:ghost ?y }", MAPPING
        )


def test_executor_requires_mapping_or_r2rml():
    with pytest.raises(ValueError):
        ClickHouseExecutor(transport=lambda _s: [])


def test_executor_requires_transport_or_dsn():
    with pytest.raises(ValueError):
        ClickHouseExecutor(mapping=MAPPING)


def test_from_env_wires_a_clickhouse_leg(tmp_path):
    """A clickhouse CSI + CLICKHOUSE_DSN + a per-source R2RML file builds a
    ClickHouseExecutor (no connection until execute)."""
    import json

    from cdf.adapters import ClickHouseExecutor as CHExec
    from cdf.service.app import FederationService

    csi_dir = tmp_path / "csi"
    csi_dir.mkdir()
    (csi_dir / "ch.json").write_text(json.dumps({
        "csiVersion": "1",
        "conceptualModel": {"entities": [
            {"name": "usage_metrics", "properties": [{"name": "account_id"}]}]},
        "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
        "provenance": {"producer": "r2g", "direction": "forward",
                       "source": {"kind": "clickhouse", "ref": "analytics"}},
    }))
    r2rml_dir = tmp_path / "r2rml"
    r2rml_dir.mkdir()
    (r2rml_dir / "clickhouse_analytics.ttl").write_text(R2RML)

    service = FederationService.from_env({
        "CDF_CSI_DIR": str(csi_dir),
        "CDF_R2RML_DIR": str(r2rml_dir),
        "CLICKHOUSE_DSN": "clickhouse://u:p@h:8123/analytics",
        "CDF_NL_DISABLED": "1",
    })
    assert isinstance(service.executors["clickhouse:analytics"], CHExec)


def test_clickhouse_leg_in_full_federation_pipeline():
    """ClickHouse (usage) joins an account-context leg through partition→
    execute→ground — the same seam as the Ontop/Arango legs."""
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
                "conceptualModel": {"entities": [
                    {"name": "accounts", "properties": [{"name": "account_id"}, {"name": "account_name"}]}]},
                "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
                "provenance": {"producer": "r2g", "direction": "forward",
                               "source": {"kind": "postgresql", "ref": "crm"}},
            },
            {
                "csiVersion": "1",
                "conceptualModel": {"entities": [
                    {"name": "usage_metrics", "properties": [
                        {"name": "account_id"}, {"name": "query_volume_m"}]}]},
                "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
                "provenance": {"producer": "r2g", "direction": "forward",
                               "source": {"kind": "clickhouse", "ref": "analytics"}},
            },
        ]
    )
    q = (
        PREFIX + "SELECT ?account_name ?query_volume_m WHERE { "
        "?a a c:accounts ; c:account_id ?k ; c:account_name ?account_name . "
        "?u a c:usage_metrics ; c:account_id ?k ; c:query_volume_m ?query_volume_m }"
    )
    plan = partition_query(q, catalog)

    ch = ClickHouseExecutor(
        mapping=MAPPING,
        transport=lambda _sql: [{"k": "ACME", "query_volume_m": 12.5}],
        source_objects=("usage_metrics",),
        clock=lambda: "T0",
    )

    class _Pg:
        def execute(self, _sq):
            return SourceResult(rows=({"k": "ACME", "account_name": "Acme"},))

    env = ground(execute_plan(plan, {"clickhouse:analytics": ch, "postgresql:crm": _Pg()}))
    assert env.status == "grounded"
    assert env.bindings == ({"account_name": "Acme", "query_volume_m": 12.5},)

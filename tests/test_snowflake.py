"""Tests for the Snowflake executor (compilation logic, no database).

Prove the R2RML parse + BGP→Snowflake-SQL compiler + row mapping with a fake
transport, focusing on the three dialect differences from ClickHouse: double-quoted
identifiers (uppercase physical names + case-preserving aliases), ``''`` string
escaping, and ``TRUE``/``FALSE`` booleans. The emitted dialect itself is validated
only by the opt-in live test (tests/test_snowflake_live.py)."""

from __future__ import annotations

import pytest

from cdf.adapters import SnowflakeExecutor
from cdf.adapters.snowflake import SnowflakeError, compile_sql, parse_r2rml
from cdf.query.types import SourceRef, SubQuery

C = "urn:arango-sparql:concept#"
PREFIX = f"PREFIX c: <{C}>\n"

# R2RML in the shape r2g `export-r2rml` emits for Snowflake: CC-12 camelCase concept
# names, UPPERCASE physical names (loaded unquoted → Snowflake upper-folds them).
R2RML = f"""
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<#Account> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "ACCOUNTS" ] ;
    rr:subjectMap [ rr:template "http://r/a/{{ID}}" ; rr:class <{C}Account> ] ;
    rr:predicateObjectMap [ rr:predicate <{C}accountId> ;
        rr:objectMap [ rr:column "ACCOUNT_ID" ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}accountName> ;
        rr:objectMap [ rr:column "ACCOUNT_NAME" ] ] .

<#UsageMetric> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "USAGE_METRICS" ] ;
    rr:subjectMap [ rr:template "http://r/u/{{ID}}" ; rr:class <{C}UsageMetric> ] ;
    rr:predicateObjectMap [ rr:predicate <{C}accountId> ;
        rr:objectMap [ rr:column "ACCOUNT_ID" ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}queryVolumeM> ;
        rr:objectMap [ rr:column "QUERY_VOLUME_M" ; rr:datatype xsd:double ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}graphragEnabled> ;
        rr:objectMap [ rr:column "GRAPHRAG_ENABLED" ; rr:datatype xsd:boolean ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}edition> ; rr:objectMap [ rr:column "EDITION" ] ] .
"""

MAPPING = parse_r2rml(R2RML)


def test_parse_r2rml():
    assert set(MAPPING) == {f"{C}Account", f"{C}UsageMetric"}
    assert MAPPING[f"{C}UsageMetric"]["table"] == "USAGE_METRICS"
    assert MAPPING[f"{C}UsageMetric"]["columns"][f"{C}queryVolumeM"] == "QUERY_VOLUME_M"


def test_identifiers_double_quoted_and_alias_case_preserved():
    sql = compile_sql(
        PREFIX
        + 'SELECT ?qv ?aid WHERE { ?u a c:UsageMetric ; c:edition "Enterprise" ; '
        "c:queryVolumeM ?qv ; c:accountId ?aid }",
        MAPPING,
    )
    assert 'FROM "USAGE_METRICS" AS t0' in sql
    assert "t0.\"EDITION\" = 'Enterprise'" in sql
    # alias double-quoted so Snowflake preserves the bare-var case for reassembly
    assert 't0."QUERY_VOLUME_M" AS "qv"' in sql
    assert 't0."ACCOUNT_ID" AS "aid"' in sql


def test_join_two_tables_on_shared_variable():
    sql = compile_sql(
        PREFIX
        + "SELECT ?accountName ?qv WHERE { "
        "?a a c:Account ; c:accountId ?k ; c:accountName ?accountName . "
        "?u a c:UsageMetric ; c:accountId ?k ; c:queryVolumeM ?qv }",
        MAPPING,
    )
    assert 'FROM "ACCOUNTS" AS t0, "USAGE_METRICS" AS t1' in sql
    assert 't0."ACCOUNT_ID" = t1."ACCOUNT_ID"' in sql


def test_bind_join_values_becomes_in_list():
    sql = compile_sql(
        PREFIX
        + "SELECT ?qv ?aid WHERE { ?u a c:UsageMetric ; c:queryVolumeM ?qv ; "
        'c:accountId ?aid }\nVALUES (?aid) { ("ACME") ("GLOBEX") }',
        MAPPING,
    )
    assert "t0.\"ACCOUNT_ID\" IN ('ACME', 'GLOBEX')" in sql


def test_boolean_renders_true_false():
    sql = compile_sql(
        PREFIX
        + "SELECT ?aid WHERE { ?u a c:UsageMetric ; c:accountId ?aid ; "
        "c:graphragEnabled true }",
        MAPPING,
    )
    assert 't0."GRAPHRAG_ENABLED" = TRUE' in sql  # real Snowflake BOOLEAN, not 1/0


def test_single_quote_is_doubled():
    sql = compile_sql(
        PREFIX + 'SELECT ?aid WHERE { ?u a c:UsageMetric ; c:accountId ?aid ; '
        "c:edition \"O'Brien\" }",
        MAPPING,
    )
    assert "'O''Brien'" in sql  # standard-SQL quote doubling, not backslash


def test_executor_compiles_and_maps_rows():
    captured = {}

    def transport(sql):
        captured["sql"] = sql
        return [{"aid": "ACME", "qv": 12.5}, {"aid": "GLOBEX", "qv": 3.0}]

    ex = SnowflakeExecutor(
        mapping=MAPPING, transport=transport, source_objects=("USAGE_METRICS",),
        clock=lambda: "T0",
    )
    sq = SubQuery(
        source=SourceRef(source_id="snowflake:telemetry", kind="snowflake", ref="telemetry"),
        triples=(),
        variables=("?aid", "?qv"),
        sparql=PREFIX
        + "SELECT ?aid ?qv WHERE { ?u a c:UsageMetric ; c:accountId ?aid ; c:queryVolumeM ?qv }",
    )
    result = ex.execute(sq)
    assert result.rows == ({"aid": "ACME", "qv": 12.5}, {"aid": "GLOBEX", "qv": 3.0})
    assert result.source_objects == ("USAGE_METRICS",)
    assert result.as_of == "T0"
    assert result.native_query == captured["sql"]
    assert 'FROM "USAGE_METRICS"' in result.native_query


def test_executor_can_build_mapping_from_r2rml():
    ex = SnowflakeExecutor(r2rml=R2RML, transport=lambda _s: [])
    assert ex.execute(
        SubQuery(
            source=SourceRef("snowflake:t", "snowflake", "t"),
            triples=(),
            variables=("?aid",),
            sparql=PREFIX + "SELECT ?aid WHERE { ?u a c:UsageMetric ; c:accountId ?aid }",
        )
    ).rows == ()


# -- error cases -------------------------------------------------------------


def test_untyped_subject_raises():
    with pytest.raises(SnowflakeError):
        compile_sql(PREFIX + "SELECT ?x WHERE { ?x c:accountId ?y }", MAPPING)


def test_unmapped_class_raises():
    with pytest.raises(SnowflakeError):
        compile_sql(PREFIX + "SELECT ?x WHERE { ?x a c:Nope ; c:accountId ?y }", MAPPING)


def test_unmapped_property_raises():
    with pytest.raises(SnowflakeError):
        compile_sql(PREFIX + "SELECT ?y WHERE { ?u a c:UsageMetric ; c:ghost ?y }", MAPPING)


def test_executor_requires_mapping_or_r2rml():
    with pytest.raises(ValueError):
        SnowflakeExecutor(transport=lambda _s: [])


def test_executor_requires_transport_or_connect_args():
    with pytest.raises(ValueError):
        SnowflakeExecutor(mapping=MAPPING)  # no transport, no connect_args


def test_from_env_wires_a_snowflake_leg(tmp_path):
    """A snowflake CSI + SNOWFLAKE_ACCOUNT + a per-source R2RML file builds a
    SnowflakeExecutor (no connection until execute — connect_args are lazy)."""
    import json

    from cdf.adapters import SnowflakeExecutor as SFExec
    from cdf.service.app import FederationService

    csi_dir = tmp_path / "csi"
    csi_dir.mkdir()
    (csi_dir / "sf.json").write_text(json.dumps({
        "csiVersion": "1",
        "conceptualModel": {"entities": [
            {"name": "UsageMetric", "properties": [{"name": "accountId"}]}]},
        "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
        "provenance": {"producer": "r2g", "direction": "forward",
                       "source": {"kind": "snowflake", "ref": "telemetry"}},
    }))
    r2rml_dir = tmp_path / "r2rml"
    r2rml_dir.mkdir()
    (r2rml_dir / "snowflake_telemetry.ttl").write_text(R2RML)

    service = FederationService.from_env({
        "CDF_CSI_DIR": str(csi_dir),
        "CDF_R2RML_DIR": str(r2rml_dir),
        "SNOWFLAKE_ACCOUNT": "oewnmae-zh45116",
        "SNOWFLAKE_USER": "cdf",
        "SNOWFLAKE_PASSWORD": "x",
        "CDF_NL_DISABLED": "1",
    })
    assert isinstance(service.executors["snowflake:telemetry"], SFExec)


def test_snowflake_leg_in_full_federation_pipeline():
    """Snowflake (usage) joins an account leg through partition→execute→ground —
    the same seam as the Ontop/Arango/ClickHouse legs (three-source shape)."""
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
                    {"name": "Account",
                     "properties": [{"name": "accountId"}, {"name": "accountName"}]}]},
                "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
                "provenance": {"producer": "r2g", "direction": "forward",
                               "source": {"kind": "postgresql", "ref": "crm"}},
            },
            {
                "csiVersion": "1",
                "conceptualModel": {"entities": [
                    {"name": "UsageMetric", "properties": [
                        {"name": "accountId"}, {"name": "queryVolumeM"}]}]},
                "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
                "provenance": {"producer": "r2g", "direction": "forward",
                               "source": {"kind": "snowflake", "ref": "telemetry"}},
            },
        ]
    )
    q = (
        PREFIX + "SELECT ?accountName ?queryVolumeM WHERE { "
        "?a a c:Account ; c:accountId ?k ; c:accountName ?accountName . "
        "?u a c:UsageMetric ; c:accountId ?k ; c:queryVolumeM ?queryVolumeM }"
    )
    plan = partition_query(q, catalog)

    sf = SnowflakeExecutor(
        mapping=MAPPING,
        transport=lambda _sql: [{"k": "ACME", "queryVolumeM": 12.5}],
        source_objects=("USAGE_METRICS",),
        clock=lambda: "T0",
    )

    class _Pg:
        def execute(self, _sq):
            return SourceResult(rows=({"k": "ACME", "accountName": "Acme"},))

    env = ground(execute_plan(plan, {"snowflake:telemetry": sf, "postgresql:crm": _Pg()}))
    assert env.status == "grounded"
    assert env.bindings == ({"accountName": "Acme", "queryVolumeM": 12.5},)

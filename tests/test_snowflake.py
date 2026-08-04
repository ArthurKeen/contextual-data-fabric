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


# -- connection pool (the default transport) ----------------------------------
# The fakes mirror snowflake-connector-python's real surface: connect(**kw) ->
# connection with is_closed()/close()/cursor(cursor_class); cursor has
# execute/fetchall/close; errors are the REAL classes from
# snowflake.connector.errors (mock-fidelity: wrong-shape mocks prove nothing).


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql):
        self._conn.executed.append(sql)
        if self._conn.fail_with is not None:
            exc = self._conn.fail_with
            self._conn.fail_with = None  # fail once, then heal
            raise exc

    def fetchall(self):
        return [{"aid": "ACME"}]

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, fail_with=None):
        self.executed = []
        self.closed = False
        self.fail_with = fail_with

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def cursor(self, _cursor_class):
        return _FakeCursor(self)


def _pooled_transport(monkeypatch, connections):
    """Build the real pooled transport with connect() monkeypatched to hand out
    *connections* in order (mirroring snowflake.connector.connect's signature)."""
    import snowflake.connector

    from cdf.adapters.snowflake import _snowflake_transport

    it = iter(connections)
    calls = {"n": 0}

    def fake_connect(**_kwargs):
        calls["n"] += 1
        return next(it)

    monkeypatch.setattr(snowflake.connector, "connect", fake_connect)
    transport = _snowflake_transport({"account": "a", "user": "u", "password": "p"})
    return transport, calls


def test_pool_reuses_one_connection_across_queries(monkeypatch):
    conn = _FakeConnection()
    transport, calls = _pooled_transport(monkeypatch, [conn])
    assert transport("SELECT 1") == [{"aid": "ACME"}]
    assert transport("SELECT 2") == [{"aid": "ACME"}]
    assert transport("SELECT 3") == [{"aid": "ACME"}]
    assert calls["n"] == 1  # one TLS+auth session for three queries
    assert conn.executed == ["SELECT 1", "SELECT 2", "SELECT 3"]


def test_stale_session_is_retried_once_on_a_fresh_connection(monkeypatch):
    import snowflake.connector

    stale = _FakeConnection(
        fail_with=snowflake.connector.errors.OperationalError(msg="session gone")
    )
    fresh = _FakeConnection()
    transport, calls = _pooled_transport(monkeypatch, [stale, fresh])
    assert transport("SELECT 1") == [{"aid": "ACME"}]  # retried transparently
    assert calls["n"] == 2
    assert stale.closed  # broken session discarded, not pooled
    assert fresh.executed == ["SELECT 1"]


def test_programming_error_propagates_without_retry(monkeypatch):
    import snowflake.connector

    conn = _FakeConnection(
        fail_with=snowflake.connector.errors.ProgrammingError(msg="bad sql")
    )
    transport, calls = _pooled_transport(monkeypatch, [conn])
    with pytest.raises(snowflake.connector.errors.ProgrammingError):
        transport("SELECT nope")
    assert calls["n"] == 1  # a bad query is never re-executed
    # The session is healthy — it goes back to the pool and serves the next query.
    assert transport("SELECT 1") == [{"aid": "ACME"}]
    assert calls["n"] == 1


def test_expired_auth_token_is_retried_on_a_fresh_session(monkeypatch):
    """A pooled session whose auth token expired (390114 / SQLSTATE 08001) is a
    dead *session*, not a bad query: discard it and retry once on a freshly
    authenticated connection. Regression — this surfaced as ``snowflake:telemetry
    (failed) — Authentication token has expired`` refusing the 3-way demo query,
    because the poisoned session was classed as a SQL fault and re-pooled."""
    import snowflake.connector

    expired = snowflake.connector.errors.ProgrammingError(
        msg="Authentication token has expired.  The user must authenticate again.",
        errno=390114,
        sqlstate="08001",
    )
    stale = _FakeConnection(fail_with=expired)
    fresh = _FakeConnection()
    transport, calls = _pooled_transport(monkeypatch, [stale, fresh])

    assert transport("SELECT 1") == [{"aid": "ACME"}]  # recovered, not refused
    assert calls["n"] == 2  # reconnected once on a fresh, re-authenticated session
    assert stale.closed  # poisoned session discarded, never returned to the pool
    assert fresh.executed == ["SELECT 1"]


def test_sql_fault_with_sqlstate_still_propagates_without_retry(monkeypatch):
    """A genuine SQL fault (42-class SQLSTATE) still propagates immediately, the
    session kept — the auth-retry path must key on the 08 connection-class only
    and never swallow real query errors."""
    import snowflake.connector

    bad_sql = snowflake.connector.errors.ProgrammingError(
        msg="SQL compilation error", errno=1003, sqlstate="42000"
    )
    conn = _FakeConnection(fail_with=bad_sql)
    transport, calls = _pooled_transport(monkeypatch, [conn])

    with pytest.raises(snowflake.connector.errors.ProgrammingError):
        transport("SELECT nope")
    assert calls["n"] == 1  # not retried
    assert not conn.closed  # healthy session kept
    assert transport("SELECT 1") == [{"aid": "ACME"}]  # and it serves the next query
    assert calls["n"] == 1


def test_expired_token_drains_all_idle_pooled_sessions(monkeypatch):
    """An expired token poisons every session opened in the same window, so the
    whole idle pool is drained before the retry — otherwise the retry would just
    borrow the next dead session and the query would still fail."""
    import threading

    import snowflake.connector

    barrier = threading.Barrier(2)
    warming = {"on": True}

    class _SlowConn(_FakeConnection):
        def cursor(self, cursor_class):
            if warming["on"]:
                barrier.wait()  # hold both borrows open so both land in the pool
            return _FakeCursor(self)

    a, b = _SlowConn(), _SlowConn()
    fresh = _FakeConnection()
    transport, calls = _pooled_transport(monkeypatch, [a, b, fresh])

    # Warm two idle sessions into the pool via two concurrent queries.
    threads = [threading.Thread(target=transport, args=("SELECT 1",)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 2  # two sessions opened and returned to the pool
    warming["on"] = False

    # Both pooled tokens expire together; the next query discovers it on one.
    expired = snowflake.connector.errors.ProgrammingError(
        msg="Authentication token has expired.", errno=390114, sqlstate="08001"
    )
    a.fail_with = b.fail_with = expired

    assert transport("SELECT 2") == [{"aid": "ACME"}]  # recovers on a fresh session
    assert a.closed and b.closed  # entire idle pool flushed, no dead session re-served
    assert calls["n"] == 3  # exactly one fresh reconnect


def test_pushed_down_filter_compiles_to_where():
    sql = compile_sql(
        PREFIX + "SELECT ?qv WHERE { ?u a c:UsageMetric ; c:queryVolumeM ?qv . "
        "FILTER(?qv <= 1000) }",
        MAPPING,
    )
    assert '"QUERY_VOLUME_M" <= 1000' in sql  # pushed E1 filter, not dropped

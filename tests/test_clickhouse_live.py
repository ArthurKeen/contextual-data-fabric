"""Opt-in LIVE ClickHouse test — validates the emitted SQL dialect.

Skipped unless ``CLICKHOUSE_DSN`` is set (see ``deploy/clickhouse``). This is the
gate the unit tests can't be: a fake transport accepts SQL a real ClickHouse may
reject, so the compiler is only trustworthy once its output runs on the engine.

    CLICKHOUSE_DSN=clickhouse://cdf:cdf@localhost:8123/analytics \
        .venv/bin/python -m pytest tests/test_clickhouse_live.py -q
"""

from __future__ import annotations

import os

import pytest

from cdf.adapters import ClickHouseExecutor
from cdf.query.types import SourceRef, SubQuery

DSN = os.getenv("CLICKHOUSE_DSN")

pytestmark = pytest.mark.skipif(not DSN, reason="set CLICKHOUSE_DSN for the live test")

C = "urn:arango-sparql:concept#"
R2RML = f"""
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<#usage_metrics> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "usage_metrics" ] ;
    rr:subjectMap [ rr:template "http://r/u/{{id}}" ; rr:class <{C}usage_metrics> ] ;
    rr:predicateObjectMap [ rr:predicate <{C}account_id> ;
        rr:objectMap [ rr:column "account_id" ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}edition> ; rr:objectMap [ rr:column "edition" ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}query_volume_m> ;
        rr:objectMap [ rr:column "query_volume_m" ; rr:datatype xsd:double ] ] .
"""


def _executor():
    pytest.importorskip("clickhouse_connect")
    return ClickHouseExecutor(r2rml=R2RML, dsn=DSN, source_objects=("usage_metrics",))


def test_live_filter_and_projection():
    sq = SubQuery(
        source=SourceRef("clickhouse:analytics", "clickhouse", "analytics"),
        triples=(),
        variables=("?aid", "?qv"),
        sparql=(
            f"PREFIX c: <{C}> SELECT ?aid ?qv WHERE {{ ?u a c:usage_metrics ; "
            'c:edition "Enterprise" ; c:account_id ?aid ; c:query_volume_m ?qv }'
        ),
    )
    result = _executor().execute(sq)
    aids = {r["aid"] for r in result.rows}
    assert aids == {"001Qwvb5LAnzy3yVgi", "001LxbLlyzNOfmaOHp"}  # the 2 Enterprise rows
    assert result.as_of is not None
    assert result.source_objects == ("usage_metrics",)


def test_live_bind_join_values_in_list():
    # The FR-13 bind-join form the executor receives from E2.
    sq = SubQuery(
        source=SourceRef("clickhouse:analytics", "clickhouse", "analytics"),
        triples=(),
        variables=("?aid", "?qv"),
        sparql=(
            f"PREFIX c: <{C}> SELECT ?aid ?qv WHERE {{ ?u a c:usage_metrics ; "
            "c:account_id ?aid ; c:query_volume_m ?qv }\n"
            'VALUES (?aid) { ("001bbkuFW1b7KegAZT") }'
        ),
    )
    result = _executor().execute(sq)
    assert [r["aid"] for r in result.rows] == ["001bbkuFW1b7KegAZT"]
    assert result.rows[0]["qv"] == 3.0

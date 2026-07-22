"""Opt-in LIVE Snowflake test — validates the emitted SQL dialect.

Skipped unless ``SNOWFLAKE_ACCOUNT`` is set (with the rest of the ``SNOWFLAKE_*``
env, e.g. from the gitignored ``.env``). This is the gate the unit tests can't be:
a fake transport accepts SQL a real warehouse may reject, so the compiler is only
trustworthy once its output runs on Snowflake. Requires the ``USAGE_METRICS`` table
(load it via ``deploy/snowflake/load_corpus.py``).

    set -a; . ./.env; set +a
    .venv/bin/python -m pytest tests/test_snowflake_live.py -q
"""

from __future__ import annotations

import os

import pytest

from cdf.adapters import SnowflakeExecutor
from cdf.query.types import SourceRef, SubQuery

ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT")

pytestmark = pytest.mark.skipif(
    not ACCOUNT, reason="set SNOWFLAKE_ACCOUNT (+ SNOWFLAKE_* env) for the live test"
)

C = "urn:arango-sparql:concept#"
PREFIX = f"PREFIX c: <{C}>\n"

# Matches deploy/snowflake/load_corpus.py: unquoted (uppercase) physical names,
# CC-12 camelCase concepts.
R2RML = f"""
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<#UsageMetric> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "USAGE_METRICS" ] ;
    rr:subjectMap [ rr:template "http://r/u/{{ID}}" ; rr:class <{C}UsageMetric> ] ;
    rr:predicateObjectMap [ rr:predicate <{C}accountId> ;
        rr:objectMap [ rr:column "ACCOUNT_ID" ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}edition> ; rr:objectMap [ rr:column "EDITION" ] ] ;
    rr:predicateObjectMap [ rr:predicate <{C}queryVolumeM> ;
        rr:objectMap [ rr:column "QUERY_VOLUME_M" ; rr:datatype xsd:double ] ] .
"""


def _connect_args() -> dict[str, str | None]:
    return {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ.get("SNOWFLAKE_USER"),
        "password": os.environ.get("SNOWFLAKE_PASSWORD"),
        "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE"),
        "database": os.environ.get("SNOWFLAKE_DATABASE"),
        "schema": os.environ.get("SNOWFLAKE_SCHEMA"),
        "role": os.environ.get("SNOWFLAKE_ROLE"),
    }


def _executor() -> SnowflakeExecutor:
    pytest.importorskip("snowflake.connector")
    return SnowflakeExecutor(
        r2rml=R2RML, connect_args=_connect_args(), source_objects=("USAGE_METRICS",)
    )


def test_live_filter_and_projection():
    ex = _executor()
    sq = SubQuery(
        source=SourceRef(source_id="snowflake:telemetry", kind="snowflake", ref="telemetry"),
        triples=(),
        variables=("?aid", "?qv"),
        sparql=PREFIX
        + "SELECT ?aid ?qv WHERE { ?u a c:UsageMetric ; c:accountId ?aid ; c:queryVolumeM ?qv }",
    )
    result = ex.execute(sq)
    # The real warehouse accepted the compiled SQL and the aliases came back with
    # their case intact (the double-quoted-alias contract).
    assert all("aid" in row and "qv" in row for row in result.rows)
    assert result.native_query and 'FROM "USAGE_METRICS"' in result.native_query

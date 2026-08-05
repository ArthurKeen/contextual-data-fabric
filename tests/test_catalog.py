"""Tests for the concept→source catalog (cdf.query.catalog)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cdf.query import SourceCatalog
from cdf.query.catalog import DEFAULT_CONCEPT_BASE


def _csi(source_kind: str, source_ref: str, entities, relationships=()):
    return {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {"name": name, "properties": [{"name": p} for p in props]}
                for name, props in entities
            ],
            "relationships": [
                {"type": t, "fromEntity": f, "toEntity": to} for t, f, to in relationships
            ],
        },
        "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
        "provenance": {
            "producer": "test",
            "direction": "forward",
            "source": {"kind": source_kind, "ref": source_ref},
        },
    }


def test_source_id_derived_from_provenance():
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("User", ["name"])])]
    )
    src = cat.source_of_class(cat.iri("User"))
    assert src is not None
    assert src.source_id == "postgresql:shop"
    assert src.kind == "postgresql"
    assert src.ref == "shop"


def test_explicit_source_ids_override():
    cat = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("User", [])])],
        source_ids=["relational-1"],
    )
    assert cat.source_of_class(cat.iri("User")).source_id == "relational-1"


def test_class_and_property_indexing():
    cat = SourceCatalog.from_csi_documents(
        [_csi("arango", "docs", [("Ticket", ["title", "body"])], [("mentions", "Ticket", "User")])]
    )
    assert cat.source_of_class(cat.iri("Ticket")).kind == "arango"
    assert {s.kind for s in cat.sources_of_property(cat.iri("title"))} == {"arango"}
    assert {s.kind for s in cat.sources_of_property(cat.iri("mentions"))} == {"arango"}


def test_unknown_concept_returns_none_or_empty():
    cat = SourceCatalog.from_csi_documents([_csi("postgresql", "shop", [("User", [])])])
    assert cat.source_of_class(cat.iri("Nope")) is None
    assert cat.sources_of_property(cat.iri("nope")) == set()


def test_shared_property_name_maps_to_multiple_sources():
    cat = SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "shop", [("Order", ["name"])]),
            _csi("arango", "docs", [("User", ["name"])]),
        ]
    )
    kinds = {s.kind for s in cat.sources_of_property(cat.iri("name"))}
    assert kinds == {"postgresql", "arango"}


def test_sources_property_lists_all_distinct():
    cat = SourceCatalog.from_csi_documents(
        [
            _csi("postgresql", "shop", [("Order", ["id"])]),
            _csi("arango", "docs", [("User", ["id"])]),
        ]
    )
    assert {s.source_id for s in cat.sources} == {"postgresql:shop", "arango:docs"}


def test_default_concept_base_matches_producers():
    assert DEFAULT_CONCEPT_BASE == "urn:arango-sparql:concept#"


def test_statistics_v1_parses_source_class_and_property_hints():
    document = _csi("postgresql", "shop", [("User", ["accountId"])])
    document["statistics"] = {
        "version": "1",
        "snapshotId": "shop-42",
        "asOf": "2026-08-05T12:00:00Z",
        "source": {
            "cardinality": 10_000,
            "estimatedBytes": 2_000_000,
            "costPerGbUsd": 0.25,
        },
        "classes": {
            "User": {
                "rowCount": 500,
                "estimatedBytes": 50_000,
                "properties": {"accountId": {"ndv": 450, "selectivity": 0.02}},
            }
        },
        "properties": {"accountId": {"ndv": 450}},
    }
    catalog = SourceCatalog.from_csi_documents([document])
    stats = catalog.statistics_for("postgresql:shop")
    assert stats is not None
    assert stats.snapshot_id == "shop-42"
    assert stats.row_count == 10_000
    assert stats.classes["User"].row_count == 500
    assert stats.classes["User"].properties["accountId"].ndv == 450
    assert stats.cost_per_gb_usd == 0.25


def test_statistics_absence_is_backward_compatible():
    catalog = SourceCatalog.from_csi_documents(
        [_csi("postgresql", "shop", [("User", ["name"])])]
    )
    assert catalog.statistics_for("postgresql:shop") is None


@pytest.mark.parametrize(
    "statistics",
    [
        {"version": "2"},
        {"version": "1", "source": {"rowCount": -1}},
        {"version": "1", "source": {"estimatedBytes": float("inf")}},
        {
            "version": "1",
            "classes": {"User": {"properties": {"id": {"selectivity": 1.1}}}},
        },
        {"version": "1", "source": {"rowCount": 2, "cardinality": 3}},
        {"version": "1", "source": {"costPerGbUsd": "free"}},
    ],
)
def test_statistics_reject_malformed_or_negative_values(statistics):
    document = _csi("postgresql", "shop", [("User", ["id"])])
    document["statistics"] = statistics
    with pytest.raises(ValueError, match="statistics"):
        SourceCatalog.from_csi_documents([document])


def test_deploy_csi_statistics_fixtures_are_valid():
    root = Path(__file__).parents[1] / "deploy" / "csi"
    documents = [json.loads(path.read_text()) for path in sorted(root.glob("*.json"))]
    catalog = SourceCatalog.from_csi_documents(documents)
    assert all(catalog.statistics_for(source) is not None for source in catalog.sources)

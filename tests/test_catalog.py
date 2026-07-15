"""Tests for the concept→source catalog (cdf.query.catalog)."""

from __future__ import annotations

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

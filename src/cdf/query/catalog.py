"""Concept→source index built from CSI v1 mapping documents (M5 / E1).

Each federated source publishes a ``CSI v1`` document (r2g's forward CSI for a
relational source, ``arango-schema-analyzer``'s reverse CSI for the graph). The
catalog reads their **conceptual models** and records, per conceptual IRI, which
source backs it — so the planner can route each triple pattern to the right
source.

Conceptual names are turned into IRIs with the shared concept namespace
(``urn:arango-sparql:concept#`` by default) — the *same* namespace r2g's R2RML
emitter and the ``arango-sparql-py`` AQL leg use, so a partitioned SPARQL query
means the same thing everywhere.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .types import SourceRef

DEFAULT_CONCEPT_BASE = "urn:arango-sparql:concept#"


def source_ref_from_csi(doc: dict[str, Any], override_id: str | None = None) -> SourceRef:
    """Derive a :class:`SourceRef` from a CSI document's provenance.

    The default ``source_id`` is ``"<kind>:<ref>"`` (or just ``"<kind>"`` when no
    ref). Shared by the catalog and any caller that needs to key executors by
    the *same* id the catalog routes to (e.g. the golden-eval harness).
    """
    prov = doc.get("provenance") or {}
    src = prov.get("source") or {}
    kind = str(src.get("kind") or "unknown")
    ref = str(src.get("ref") or "")
    if override_id:
        source_id = override_id
    else:
        source_id = f"{kind}:{ref}" if ref else kind
    return SourceRef(source_id=source_id, kind=kind, ref=ref)


class SourceCatalog:
    """Maps conceptual class/property IRIs to the source(s) that back them."""

    def __init__(self, concept_base: str = DEFAULT_CONCEPT_BASE) -> None:
        self.concept_base = concept_base
        # class IRI -> source (a class is owned by exactly one source; the
        # ontology-alignment layer, M3, guarantees a single canonical name).
        self._class_source: dict[str, SourceRef] = {}
        # property/relationship IRI -> sources that expose it (a plain property
        # name like "name" can legitimately exist in several sources; the
        # planner disambiguates by the subject's class).
        self._property_sources: dict[str, set[SourceRef]] = {}

    def iri(self, name: str) -> str:
        """The conceptual IRI for a bare entity/property name."""
        return f"{self.concept_base}{name}"

    @classmethod
    def from_csi_documents(
        cls,
        documents: Iterable[dict[str, Any]],
        *,
        source_ids: Sequence[str] | None = None,
        concept_base: str = DEFAULT_CONCEPT_BASE,
    ) -> SourceCatalog:
        """Build a catalog from CSI documents.

        Args:
            documents: parsed ``CSI v1`` documents (any producer/direction).
            source_ids: optional explicit source id per document, positionally
                aligned; when omitted, each id is derived from the document's
                provenance (``"<kind>:<ref>"``).
            concept_base: concept IRI namespace (must match the producers').
        """
        catalog = cls(concept_base=concept_base)
        for i, doc in enumerate(documents):
            override = source_ids[i] if source_ids is not None else None
            catalog.add_csi(doc, source_id=override)
        return catalog

    def add_csi(self, document: dict[str, Any], *, source_id: str | None = None) -> SourceRef:
        """Register one CSI document's conceptual model under its source."""
        source = source_ref_from_csi(document, source_id)
        conceptual = document.get("conceptualModel") or {}

        for entity in conceptual.get("entities") or []:
            name = entity.get("name")
            if not name:
                continue
            self._class_source[self.iri(name)] = source
            for prop in entity.get("properties") or []:
                prop_name = prop.get("name")
                if prop_name:
                    self._property_sources.setdefault(self.iri(prop_name), set()).add(source)

        for rel in conceptual.get("relationships") or []:
            rtype = rel.get("type")
            if rtype:
                self._property_sources.setdefault(self.iri(rtype), set()).add(source)

        return source

    # -- lookups -----------------------------------------------------------

    def source_of_class(self, iri: str) -> SourceRef | None:
        """The source backing a class IRI, or ``None`` if unknown."""
        return self._class_source.get(str(iri))

    def sources_of_property(self, iri: str) -> set[SourceRef]:
        """The set of sources exposing a property/relationship IRI (possibly
        empty; possibly >1 when the plain name is shared across sources)."""
        return set(self._property_sources.get(str(iri), set()))

    @property
    def sources(self) -> set[SourceRef]:
        """Every distinct source known to the catalog."""
        out = set(self._class_source.values())
        for srcs in self._property_sources.values():
            out |= srcs
        return out

    def _local(self, iri: str) -> str:
        base = self.concept_base
        return iri[len(base):] if iri.startswith(base) else iri

    def vocabulary(self) -> list[dict[str, Any]]:
        """Per-source concept + property vocabulary for NL-query grounding.

        One entry per source — ``{source_id, kind, ref, classes, properties}``
        with bare local names (the ``concept_base`` prefix stripped, sorted for
        determinism) — so an NL front-end can ground an LLM in exactly which
        concepts exist and which source backs each.
        """
        by_source: dict[str, dict[str, Any]] = {}

        def _entry(src: SourceRef) -> dict[str, Any]:
            return by_source.setdefault(
                src.source_id,
                {
                    "source_id": src.source_id,
                    "kind": src.kind,
                    "ref": src.ref,
                    "classes": set(),
                    "properties": set(),
                },
            )

        for iri, src in self._class_source.items():
            _entry(src)["classes"].add(self._local(iri))
        for iri, srcs in self._property_sources.items():
            for src in srcs:
                _entry(src)["properties"].add(self._local(iri))

        return [
            {
                **entry,
                "classes": sorted(entry["classes"]),
                "properties": sorted(entry["properties"]),
            }
            for entry in sorted(by_source.values(), key=lambda e: e["source_id"])
        ]

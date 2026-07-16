"""arango-sparql-py-backed graph :class:`SourceExecutor` (M5 / Arango leg).

The Arango leg of the federation: an E1 sub-query (SPARQL, full-IRI) is
transpiled to AQL by the owned ``arango-sparql-py`` engine — driven by a
``MappingBundle`` derived from the source's ``CSI`` document via the A3 adapter
(``mapping_bundle_from_csi``) — and run against a live ArangoDB. The AQL
projection returns rows already keyed by the bare SPARQL variable names
(``RETURN { s: doc._uri, name: doc.name }``), so result rows *are*
:class:`~cdf.query.executor.Binding`\\ s.

Pipeline::

    E1 sub-query (SPARQL) ─▶ arango-sparql-py translate ─▶ AQL ─▶ ArangoDB

Both heavy pieces are injectable so the mapping logic is unit-testable without
``arango-sparql-py`` or a live database:

- ``translate``: ``(sparql) -> (aql, bind_vars)``. Default builds a
  ``SchemaResolver`` from the CSI/bundle and calls ``arango_sparql.api.translate``
  (lazy import — ``arango-sparql-py`` is only needed on the live path).
- ``transport``: ``(aql, bind_vars) -> rows``. Default runs
  ``db.aql.execute`` against a python-arango database handle.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from cdf.query.executor import Binding, SourceResult
from cdf.query.types import SubQuery

# (sparql) -> (aql, bind_vars)
Translate = Callable[[str], tuple[str, dict[str, Any]]]
# (aql, bind_vars) -> iterable of result rows (each a dict keyed by SPARQL var)
Transport = Callable[[str, dict[str, Any]], Any]


def _build_translate(
    *,
    csi: dict[str, Any] | None,
    bundle: Any | None,
    resolver: Any | None,
    tenant_id: str | None,
) -> Translate:
    """Default translate: lazily build a resolver from CSI/bundle and transpile."""
    cached: dict[str, Any] = {}

    def _resolver() -> Any:
        if "r" in cached:
            return cached["r"]
        from arango_sparql.translate.resolver import SchemaResolver

        if resolver is not None:
            resolved = resolver
        elif bundle is not None:
            resolved = SchemaResolver.from_mapping_bundle(bundle)
        elif csi is not None:
            from arango_sparql.translate.csi import mapping_bundle_from_csi

            resolved = SchemaResolver.from_mapping_bundle(mapping_bundle_from_csi(csi))
        else:  # pragma: no cover — guarded in __init__
            raise ValueError("need one of: translate, resolver, bundle, csi")
        cached["r"] = resolved
        return resolved

    def translate(sparql: str) -> tuple[str, dict[str, Any]]:
        from arango_sparql.api import translate as _translate

        result = _translate(sparql, resolver=_resolver(), tenant_id=tenant_id)
        return result.aql, result.bind_vars

    return translate


def _build_transport(db: Any | None) -> Transport | None:
    if db is None:
        return None

    def transport(aql: str, bind_vars: dict[str, Any]) -> Any:
        return db.aql.execute(aql, bind_vars=bind_vars)

    return transport


class ArangoExecutor:
    """Executes a sub-query against ArangoDB via ``arango-sparql-py``."""

    def __init__(
        self,
        *,
        csi: dict[str, Any] | None = None,
        bundle: Any | None = None,
        resolver: Any | None = None,
        db: Any | None = None,
        translate: Translate | None = None,
        transport: Transport | None = None,
        source_objects: tuple[str, ...] = (),
        tenant_id: str | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        """
        Args:
            csi / bundle / resolver: the schema mapping, in decreasing level of
                pre-processing — a ``CSI`` document (turned into a MappingBundle
                via A3), a ready ``MappingBundle``, or a built ``SchemaResolver``.
                One is required unless ``translate`` is supplied.
            db: a python-arango database handle for the default transport.
            translate / transport: dependency-injection seams (tests, or a custom
                engine). Override the CSI→resolver→AQL step and the DB call.
            source_objects: physical objects served (collections/edges), cited on
                each result (FR-2).
            tenant_id: forwarded to the transpiler for tenant-scoped classes.
            clock: as-of stamp provider (FR-12); defaults to UTC now.
        """
        if translate is None:
            if csi is None and bundle is None and resolver is None:
                raise ValueError(
                    "ArangoExecutor requires one of: translate, resolver, bundle, csi"
                )
            translate = _build_translate(
                csi=csi, bundle=bundle, resolver=resolver, tenant_id=tenant_id
            )
        if transport is None:
            transport = _build_transport(db)
            if transport is None:
                raise ValueError("ArangoExecutor requires a transport or a db handle")

        self._translate = translate
        self._transport = transport
        self._source_objects = tuple(source_objects)
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def execute(self, subquery: SubQuery) -> SourceResult:
        aql, bind_vars = self._translate(subquery.sparql)
        rows: tuple[Binding, ...] = tuple(
            dict(row) for row in self._transport(aql, bind_vars)
        )
        return SourceResult(
            rows=rows,
            native_query=aql,
            as_of=self._clock(),
            source_objects=self._source_objects,
        )

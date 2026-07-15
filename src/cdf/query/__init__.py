"""Module 05 — Federated Query Engine.

The runtime heart of the Query building block: turn a conceptual query over the
master ontology into a **decomposed, multi-source plan**, execute the parts
against the sources that hold the data, and reassemble one grounded answer —
without moving the data (see the M5 specification).

This package currently implements **E1 — the query-graph partition planner**:

- :class:`~cdf.query.catalog.SourceCatalog` — a concept→source index built from
  the ``CSI v1`` mapping documents each source publishes (r2g's forward CSI,
  the analyzer's reverse CSI).
- :func:`~cdf.query.planner.partition_query` — parse a conceptual SPARQL query,
  partition its graph pattern by the source each concept/property maps to, and
  emit per-source sub-queries plus the cross-source join keys.
- :func:`~cdf.query.executor.execute_plan` (**E2**) — run each sub-query through
  a pluggable :class:`~cdf.query.executor.SourceExecutor`, inner-join the
  per-source results on the join keys, project, and assemble a retrieval path
  with partial-failure and as-of semantics.

The emitted :class:`~cdf.query.types.PartitionPlan` is the **partition contract**
the executor (E2), the per-source query generators (Ontop/R2RML relational leg,
``arango-sparql-py`` AQL leg), and provenance (E3) consume.
"""

from .catalog import SourceCatalog
from .executor import (
    FederatedResult,
    RetrievalStep,
    SourceExecutor,
    SourceResult,
    execute_plan,
)
from .planner import UnsupportedQueryError, partition_query
from .types import PartitionPlan, SourceRef, SubQuery, TriplePattern

__all__ = [
    "SourceCatalog",
    "partition_query",
    "UnsupportedQueryError",
    "execute_plan",
    "SourceExecutor",
    "SourceResult",
    "RetrievalStep",
    "FederatedResult",
    "PartitionPlan",
    "SourceRef",
    "SubQuery",
    "TriplePattern",
]

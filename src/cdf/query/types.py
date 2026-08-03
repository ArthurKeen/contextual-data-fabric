"""Data types for the federated query partition plan (M5 / E1).

The :class:`PartitionPlan` is the stable contract downstream stages consume:

- **E2 (executor)** runs each :class:`SubQuery` against its source and joins the
  results on :attr:`PartitionPlan.join_keys`.
- **Per-source generators** turn each :class:`SubQuery` into SQL (Ontop/R2RML) or
  AQL (``arango-sparql-py``).
- **E3 (provenance)** cites the actual sub-queries executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceRef:
    """A stable reference to one federated data source.

    ``source_id`` is the join/routing key (unique per source in the fabric);
    ``kind`` is the engine family (``postgresql``, ``arango``, …) and ``ref`` an
    optional human pointer (database/schema/graph name).
    """

    source_id: str
    kind: str
    ref: str = ""


@dataclass(frozen=True)
class TriplePattern:
    """One graph-pattern triple, each term SPARQL-serialized (n3) so the plan
    is transport-agnostic and human-readable (``?o``, ``<urn:…#total>``)."""

    subject: str
    predicate: str
    object: str


@dataclass(frozen=True)
class SubQuery:
    """The slice of the conceptual query that a single source must answer."""

    source: SourceRef
    triples: tuple[TriplePattern, ...]
    variables: tuple[str, ...]
    """All variables appearing in this sub-query (SPARQL ``?name`` spelling),
    including OPTIONAL-group variables (they ride the result envelope like any
    column)."""
    sparql: str
    """A self-contained SPARQL SELECT for this source (full IRIs, no prefixes) —
    the authoritative citation, including any pushed-down FILTER/OPTIONAL."""
    filters: tuple[str, ...] = ()
    """Single-leg FILTER conjuncts pushed into this leg, each a serialized
    expression (e.g. ``?score > 50``). Empty for a plain-BGP leg."""
    optional_groups: tuple[tuple[TriplePattern, ...], ...] = ()
    """OPTIONAL groups attached to this leg — each a tuple of triples that binds
    additional (well-designed, single-source) projection variables. Empty for a
    plain-BGP leg."""


@dataclass(frozen=True)
class PartitionPlan:
    """The decomposition of one conceptual query into per-source sub-queries."""

    sub_queries: tuple[SubQuery, ...]
    join_keys: tuple[str, ...]
    """Variables shared across sub-queries of *different* sources — the
    cross-source join points (reconciled via the canonical entity hub, M6)."""
    projection: tuple[str, ...]
    """The original query's projected (SELECT) variables."""
    unresolved: tuple[TriplePattern, ...] = field(default_factory=tuple)
    """Triples whose concept/property maps to no known source — surfaced, never
    silently dropped (a load-bearing unresolved triple means the plan cannot be
    answered in full; the caller decides partial-answer vs. refuse per FR-11)."""

    @property
    def sources(self) -> tuple[SourceRef, ...]:
        """Distinct sources this plan touches, in sub-query order."""
        return tuple(sq.source for sq in self.sub_queries)

    @property
    def is_single_source(self) -> bool:
        return len(self.sub_queries) <= 1

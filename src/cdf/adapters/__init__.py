"""Concrete :class:`~cdf.query.executor.SourceExecutor` adapters for live sources.

- :class:`~cdf.adapters.ontop.OntopExecutor` — the relational leg (B1): sends a
  sub-query to an Ontop SPARQL endpoint (SPARQL→SQL over live Postgres, driven by
  the r2g-emitted R2RML) and parses the SPARQL 1.1 JSON results into bindings.
- :class:`~cdf.adapters.arango.ArangoExecutor` — the graph leg: transpiles a
  sub-query to AQL via the owned ``arango-sparql-py`` engine (over a
  ``MappingBundle`` from the A3 CSI adapter) and runs it against ArangoDB.
"""

from .arango import ArangoExecutor
from .ontop import OntopExecutor

__all__ = ["OntopExecutor", "ArangoExecutor"]

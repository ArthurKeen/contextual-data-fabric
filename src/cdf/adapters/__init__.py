"""Concrete :class:`~cdf.query.executor.SourceExecutor` adapters for live sources.

- :class:`~cdf.adapters.ontop.OntopExecutor` — the relational leg (B1): sends a
  sub-query to an Ontop SPARQL endpoint (SPARQL→SQL over live Postgres, driven by
  the r2g-emitted R2RML) and parses the SPARQL 1.1 JSON results into bindings.

The Arango AQL leg (C1, ``arango-sparql-py``) lands here too.
"""

from .ontop import OntopExecutor

__all__ = ["OntopExecutor"]

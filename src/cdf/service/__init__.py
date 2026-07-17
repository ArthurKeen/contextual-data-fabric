"""HTTP service seam for the M5 federated query engine (PRD §10.2, M9)."""

from .app import FederationService, create_app

__all__ = ["FederationService", "create_app"]

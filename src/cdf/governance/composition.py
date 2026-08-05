"""Environment composition seams for PDP and masking-key implementations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from cdf.connectors.delegation import SecretMaterial
from cdf.query.catalog import SourceCatalog

from .contracts import MaskingKeyResolver, PolicyDecisionPoint, SecretMaskingKey
from .pdp import CatalogPolicyPDP, NonePolicyPDP, OpenFGAConfig, OpenFGAPolicyPDP


@dataclass(frozen=True)
class StaticMaskingKeyResolver:
    key: SecretMaskingKey = field(repr=False)

    def resolve(self, _policy_ids: tuple[str, ...]) -> SecretMaskingKey:
        return self.key


def _factory(path: str, environ: Mapping[str, str], contract_method: str) -> Any:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("factory must use package.module:function syntax")
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise ValueError(f"factory target {path!r} is not callable")
    result = factory(environ)
    if not callable(getattr(result, contract_method, None)):
        raise ValueError(f"factory target must provide {contract_method}()")
    return result


def policy_pdp_from_env(
    catalog: SourceCatalog,
    environ: Mapping[str, str],
) -> PolicyDecisionPoint:
    backend_value = environ.get("CDF_POLICY_BACKEND")
    required = environ.get("CDF_POLICY_REQUIRED", "").strip().casefold() in {
        "1",
        "true",
        "yes",
    }
    if backend_value is None:
        if required:
            raise ValueError("CDF_POLICY_BACKEND is required when policy is required")
        backend = "none"
    else:
        backend = backend_value.strip().casefold()
    if backend == "none":
        if required:
            raise ValueError("CDF_POLICY_BACKEND=none is forbidden when policy is required")
        return NonePolicyPDP()
    if backend == "catalog":
        if catalog.manifest_generation is None:
            raise ValueError("catalog policy backend requires CDF_CATALOG_MANIFEST")
        return CatalogPolicyPDP(catalog)
    if backend != "openfga":
        raise ValueError("CDF_POLICY_BACKEND must be none, catalog, or openfga")
    required_settings = {
        "api_url": "CDF_OPENFGA_API_URL",
        "store_id": "CDF_OPENFGA_STORE_ID",
        "authorization_model_id": "CDF_OPENFGA_MODEL_ID",
        "relationship": "CDF_OPENFGA_RELATIONSHIP",
    }
    missing = [env_name for env_name in required_settings.values() if not environ.get(env_name)]
    if missing:
        raise ValueError("OpenFGA settings are missing: " + ", ".join(sorted(missing)))
    try:
        timeout = float(environ.get("CDF_OPENFGA_TIMEOUT_SECONDS", "2"))
    except ValueError as exc:
        raise ValueError("CDF_OPENFGA_TIMEOUT_SECONDS must be numeric") from exc
    config = OpenFGAConfig(
        **{name: environ[env_name] for name, env_name in required_settings.items()},
        timeout_seconds=timeout,
    )
    token = environ.get("CDF_OPENFGA_BEARER_TOKEN")
    return OpenFGAPolicyPDP(
        catalog,
        config,
        bearer=SecretMaterial(token) if token else None,
    )


def masking_key_resolver_from_env(
    environ: Mapping[str, str],
) -> MaskingKeyResolver | None:
    factory_path = environ.get("CDF_MASKING_KEY_RESOLVER_FACTORY", "").strip()
    if factory_path:
        return _factory(factory_path, environ, "resolve")
    key = environ.get("CDF_MASKING_KEY")
    return StaticMaskingKeyResolver(SecretMaskingKey(key)) if key else None

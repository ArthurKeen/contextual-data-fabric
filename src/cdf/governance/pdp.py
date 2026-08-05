"""Deterministic catalog and OpenFGA-compatible policy decision points."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cdf.auth import RequestContext
from cdf.connectors.delegation import SecretMaterial
from cdf.query.catalog import SourceCatalog

from .contracts import (
    MaskingRule,
    MaskMode,
    PlanAuthorization,
    PolicyAction,
    ResourceDecision,
    ResourceRequest,
    RowConstraint,
)

if TYPE_CHECKING:
    from cdf.catalog.model import EntitlementRule


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _context_fingerprint(context: RequestContext, generation: str | None) -> str:
    principal = context.principal
    payload = {
        "principal": principal.principal_key,
        "roles": principal.roles,
        "groups": principal.groups,
        "scopes": principal.scopes,
        "tenant": principal.tenant,
        "claims": principal.claims,
        "purpose": context.purpose,
        "generation": generation,
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _decision_id(
    fingerprint: str,
    decisions: tuple[ResourceDecision, ...],
) -> str:
    payload = {
        "context": fingerprint,
        "decisions": [
            {
                "source": item.source_id,
                "type": item.resource_type,
                "id": item.resource_id,
                "usage": item.usage,
                "action": item.action,
                "policies": item.policy_ids,
                "mask": item.mask,
                "rows": [
                    {
                        "variable": row.binding_variable,
                        "attribute": row.principal_attribute,
                        "value": row.expected_value,
                    }
                    for row in item.row_constraints
                ],
                "disclose": item.disclose_source,
                "reason": item.reason,
            }
            for item in decisions
        ],
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _attribute(context: RequestContext, name: str) -> Any:
    if name == "tenant":
        return context.principal.tenant
    if name.startswith("claim:"):
        return dict(context.principal.claims).get(name[6:])
    return None


def _intersects(required: tuple[str, ...], actual: tuple[str, ...]) -> bool:
    return not required or bool(set(required) & set(actual))


def _rule_for(catalog: SourceCatalog, request: ResourceRequest) -> EntitlementRule | None:
    resource_type = request.resource_type
    if resource_type == "concept":
        return catalog.entitlement_rule_for(
            request.source_id, "concept", request.resource_id
        )
    if resource_type in {"property", "filter", "join", "projection"}:
        return catalog.entitlement_rule_for(
            request.source_id, "property", request.resource_id
        )
    return catalog.entitlements_for(request.source_id)


class NonePolicyPDP:
    """Explicit development-only compatibility PDP."""

    def authorize(
        self,
        resources: tuple[ResourceRequest, ...],
        context: RequestContext,
        *,
        catalog_generation: str | None,
    ) -> PlanAuthorization:
        fingerprint = _context_fingerprint(context, catalog_generation)
        decisions = tuple(
            ResourceDecision(
                source_id=item.source_id,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                usage=item.usage,
                action="allow",
                variable=item.variable,
                optional=item.optional,
            )
            for item in resources
        )
        return PlanAuthorization(
            decision_id=_decision_id(fingerprint, decisions),
            catalog_generation=catalog_generation,
            context_fingerprint=fingerprint,
            decisions=decisions,
        )


class CatalogPolicyPDP:
    """Offline/dev PDP applying immutable manifest entitlements deterministically."""

    def __init__(self, catalog: SourceCatalog) -> None:
        self.catalog = catalog

    def authorize(
        self,
        resources: tuple[ResourceRequest, ...],
        context: RequestContext,
        *,
        catalog_generation: str | None,
    ) -> PlanAuthorization:
        fingerprint = _context_fingerprint(context, catalog_generation)
        decisions = tuple(self._decide(item, context) for item in resources)
        masking: dict[str, MaskingRule] = {}
        for item in decisions:
            if item.variable is not None and item.mask != "none" and item.action != "deny":
                masking[item.variable] = MaskingRule(
                    variable=item.variable,
                    mode=item.mask,
                    policy_ids=item.policy_ids,
                )
        policy_ids = tuple(
            dict.fromkeys(policy for item in decisions for policy in item.policy_ids)
        )
        withheld = tuple(
            dict.fromkeys(
                item.source_id
                for item in decisions
                if not item.disclose_source or item.mask == "drop"
            )
        )
        return PlanAuthorization(
            decision_id=_decision_id(fingerprint, decisions),
            catalog_generation=catalog_generation,
            context_fingerprint=fingerprint,
            decisions=decisions,
            masking_rules=tuple(masking.values()),
            withheld_sources=withheld,
            policy_ids=policy_ids,
        )

    def _decide(
        self,
        request: ResourceRequest,
        context: RequestContext,
    ) -> ResourceDecision:
        rule = _rule_for(self.catalog, request)
        if rule is None:
            return ResourceDecision(
                source_id=request.source_id,
                resource_type=request.resource_type,
                resource_id=request.resource_id,
                usage=request.usage,
                action="deny",
                reason="catalog policy metadata is missing",
                variable=request.variable,
                optional=request.optional,
            )

        reason: str | None = None
        principal = context.principal
        if not _intersects(rule.allowed_roles, principal.roles):
            reason = "required role is absent"
        elif not _intersects(rule.allowed_groups, principal.groups):
            reason = "required group is absent"
        elif not _intersects(rule.allowed_scopes, principal.scopes):
            reason = "required scope is absent"
        elif rule.allowed_purposes and context.purpose not in rule.allowed_purposes:
            reason = "request purpose is not allowed"

        constraints: list[RowConstraint] = []
        if reason is None:
            for constraint in rule.row_constraints:
                expected = _attribute(context, constraint.principal_attribute)
                if expected is None:
                    reason = (
                        f"principal attribute {constraint.principal_attribute} is absent"
                    )
                    break
                constraints.append(
                    RowConstraint(
                        binding_variable=constraint.binding_variable,
                        principal_attribute=constraint.principal_attribute,
                        expected_value=expected,
                    )
                )

        if (
            reason is None
            and request.source_auth_mode == "service"
            and constraints
            and not rule.allow_fabric_row_pushdown
        ):
            reason = "service-mode fabric row pushdown is not authorized"
        if (
            reason is None
            and request.source_auth_mode == "service"
            and request.variable is not None
            and rule.mask != "none"
            and not rule.allow_fabric_masking
        ):
            reason = "service-mode fabric masking is not authorized"
        if reason is None and rule.mask == "drop" and not request.optional:
            reason = "load-bearing property is not entitled"

        action: PolicyAction = "deny" if reason is not None else "allow"
        if action == "allow" and (
            constraints or rule.mask != "none" or not rule.disclose_source
        ):
            action = "rewrite"
        return ResourceDecision(
            source_id=request.source_id,
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            usage=request.usage,
            action=action,
            policy_ids=rule.policy_ids,
            classification=rule.classification,
            mask=cast(MaskMode, rule.mask),
            row_constraints=tuple(constraints),
            disclose_source=rule.disclose_source,
            reason=reason,
            variable=request.variable,
            optional=request.optional,
        )


@dataclass(frozen=True)
class OpenFGAConfig:
    api_url: str
    store_id: str
    authorization_model_id: str
    relationship: str
    timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OpenFGA API URL must be absolute HTTP(S)")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("OpenFGA API URL must not contain user information")
        if parsed.query or parsed.fragment:
            raise ValueError("OpenFGA API URL must not contain query or fragment")
        hostname = parsed.hostname
        if hostname is None:
            raise ValueError("OpenFGA API URL must include a host")
        loopback = hostname.casefold() == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                loopback = False
        if parsed.scheme != "https" and not loopback:
            raise ValueError("OpenFGA API URL requires HTTPS outside loopback development")
        for name, value in (
            ("store_id", self.store_id),
            ("authorization_model_id", self.authorization_model_id),
            ("relationship", self.relationship),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"OpenFGA {name} must be explicit")
        if not 0 < self.timeout_seconds <= 10:
            raise ValueError("OpenFGA timeout must be greater than zero and at most 10 seconds")


class OpenFGATransport(Protocol):
    def check(
        self,
        config: OpenFGAConfig,
        payload: dict[str, Any],
        *,
        bearer: SecretMaterial | None,
    ) -> bool: ...


class HttpOpenFGATransport:
    """Small synchronous transport; tests inject a fake and never use the network."""

    MAX_RESPONSE_BYTES = 64 * 1024

    class _NoRedirectHandler(HTTPRedirectHandler):
        def redirect_request(
            self,
            req: Any,
            fp: Any,
            code: int,
            msg: str,
            headers: Any,
            newurl: str,
        ) -> None:
            return None

    def __init__(self) -> None:
        # Deny redirects rather than risk replaying policy bearer material to a
        # different origin. OpenFGA endpoints must be configured canonically.
        self._opener = build_opener(self._NoRedirectHandler())

    def check(
        self,
        config: OpenFGAConfig,
        payload: dict[str, Any],
        *,
        bearer: SecretMaterial | None,
    ) -> bool:
        parsed = urlparse(config.api_url)
        if bearer is not None and parsed.scheme != "https":
            raise ConnectionError("OpenFGA bearer material requires HTTPS")
        url = (
            config.api_url.rstrip("/")
            + "/stores/"
            + quote(config.store_id, safe="")
            + "/check"
        )
        headers = {"Content-Type": "application/json"}
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer.reveal()}"
        request = Request(
            url,
            data=_canonical(payload),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(
                request,
                timeout=config.timeout_seconds,
            ) as response:
                body = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(body) > self.MAX_RESPONSE_BYTES:
                raise ValueError("OpenFGA response is too large")
            document = json.loads(body)
            if not isinstance(document, dict) or type(document.get("allowed")) is not bool:
                raise ValueError("OpenFGA response must be an object with boolean allowed")
            return document["allowed"]
        except Exception:  # noqa: BLE001 - public failure must not echo bearer-bearing errors
            raise ConnectionError("OpenFGA check unavailable") from None


class OpenFGAPolicyPDP(CatalogPolicyPDP):
    """Catalog rewrites plus an explicit OpenFGA relationship check per resource."""

    def __init__(
        self,
        catalog: SourceCatalog,
        config: OpenFGAConfig,
        *,
        transport: OpenFGATransport | None = None,
        bearer: SecretMaterial | None = None,
    ) -> None:
        super().__init__(catalog)
        if bearer is not None and urlparse(config.api_url).scheme != "https":
            raise ValueError("OpenFGA bearer material requires HTTPS")
        self.config = config
        self.transport = transport or HttpOpenFGATransport()
        self._bearer = bearer

    def authorize(
        self,
        resources: tuple[ResourceRequest, ...],
        context: RequestContext,
        *,
        catalog_generation: str | None,
    ) -> PlanAuthorization:
        local = super().authorize(
            resources,
            context,
            catalog_generation=catalog_generation,
        )
        checked: list[ResourceDecision] = []
        for request, decision in zip(resources, local.decisions, strict=True):
            if decision.action == "deny":
                checked.append(decision)
                continue
            payload = {
                "authorization_model_id": self.config.authorization_model_id,
                "tuple_key": {
                    "user": f"principal:{context.principal.principal_key}",
                    "relation": self.config.relationship,
                    "object": (
                        f"cdf:{request.resource_type}:{request.source_id}:"
                        f"{request.resource_id}"
                    ),
                },
                "context": {
                    "purpose": context.purpose,
                    "tenant": context.principal.tenant,
                },
            }
            try:
                allowed = self.transport.check(
                    self.config,
                    payload,
                    bearer=self._bearer,
                )
            except Exception:  # fail closed without exposing transport/bearer details
                checked.append(
                    replace(
                        decision,
                        action="deny",
                        reason="policy decision point unavailable",
                    )
                )
                continue
            checked.append(
                decision
                if allowed
                else replace(
                    decision,
                    action="deny",
                    reason="OpenFGA relationship check denied",
                )
            )
        decisions = tuple(checked)
        return replace(
            local,
            decision_id=_decision_id(local.context_fingerprint, decisions),
            decisions=decisions,
        )

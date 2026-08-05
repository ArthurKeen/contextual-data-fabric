"""Plan resource discovery and fail-closed authorization rewrites."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from cdf.auth import RequestContext
from cdf.query.catalog import SourceCatalog
from cdf.query.types import PartitionPlan, SubQuery, TriplePattern

from .contracts import (
    AuthorizationEvent,
    AuthorizationRefusal,
    PlanAuthorization,
    PolicyDecisionPoint,
    ResourceDecision,
    ResourceRequest,
)

_RDF_TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
_VARIABLE = re.compile(r"\?([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class AuthorizedPlan:
    plan: PartitionPlan
    resources: tuple[ResourceRequest, ...]
    authorization: PlanAuthorization
    events: tuple[AuthorizationEvent, ...]
    refusal: AuthorizationRefusal | None = None


def _bare(value: str) -> str:
    return value[1:] if value.startswith("?") else value


def _local(value: str, concept_base: str) -> str:
    iri = value[1:-1] if value.startswith("<") and value.endswith(">") else value
    return iri[len(concept_base) :] if iri.startswith(concept_base) else iri


def _property_by_variable(
    subquery: SubQuery,
    concept_base: str,
) -> tuple[dict[str, str], set[str]]:
    properties: dict[str, str] = {}
    optional: set[str] = set()
    for triple in subquery.triples:
        if triple.predicate != _RDF_TYPE and triple.object.startswith("?"):
            properties[_bare(triple.object)] = _local(triple.predicate, concept_base)
    for group in subquery.optional_groups:
        for triple in group:
            if triple.predicate != _RDF_TYPE and triple.object.startswith("?"):
                variable = _bare(triple.object)
                properties[variable] = _local(triple.predicate, concept_base)
                optional.add(variable)
    return properties, optional


def plan_resources(
    plan: PartitionPlan,
    catalog: SourceCatalog,
    source_auth_modes: Mapping[str, str],
) -> tuple[ResourceRequest, ...]:
    """Describe every source, class, property, filter, join, and projection use."""
    resources: list[ResourceRequest] = []
    joins = {_bare(item) for item in plan.join_keys}
    projections = {_bare(item) for item in plan.projection}
    for subquery in plan.sub_queries:
        source_id = subquery.source.source_id
        mode = source_auth_modes.get(source_id, "service")
        auth_mode: Literal["service", "delegated"] = (
            "delegated" if mode == "delegated" else "service"
        )
        resources.append(
            ResourceRequest(
                source_id=source_id,
                resource_type="source",
                resource_id=source_id,
                usage="load",
                source_auth_mode=auth_mode,
            )
        )
        properties, optional_vars = _property_by_variable(
            subquery, catalog.concept_base
        )
        optional_triples = {
            (triple.subject, triple.predicate, triple.object)
            for group in subquery.optional_groups
            for triple in group
        }
        all_triples = (
            *subquery.triples,
            *(item for group in subquery.optional_groups for item in group),
        )
        for triple in all_triples:
            is_optional = (
                triple.subject,
                triple.predicate,
                triple.object,
            ) in optional_triples
            if triple.predicate == _RDF_TYPE:
                resource_type = "concept"
                resource_id = _local(triple.object, catalog.concept_base)
                variable = _bare(triple.subject) if triple.subject.startswith("?") else None
            else:
                resource_type = "property"
                resource_id = _local(triple.predicate, catalog.concept_base)
                variable = _bare(triple.object) if triple.object.startswith("?") else None
            resources.append(
                ResourceRequest(
                    source_id=source_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    usage="query",
                    variable=variable,
                    optional=is_optional,
                    source_auth_mode=auth_mode,
                )
            )
        for expression in subquery.filters:
            for variable in _VARIABLE.findall(expression):
                resources.append(
                    ResourceRequest(
                        source_id=source_id,
                        resource_type="filter",
                        resource_id=properties.get(variable, variable),
                        usage="filter",
                        variable=variable,
                        optional=variable in optional_vars,
                        source_auth_mode=auth_mode,
                    )
                )
        supplied = {_bare(item) for item in subquery.variables}
        for variable in sorted(joins & supplied):
            resources.append(
                ResourceRequest(
                    source_id=source_id,
                    resource_type="join",
                    resource_id=properties.get(variable, variable),
                    usage="join",
                    variable=variable,
                    optional=False,
                    source_auth_mode=auth_mode,
                )
            )
        for variable in sorted(projections & supplied):
            resources.append(
                ResourceRequest(
                    source_id=source_id,
                    resource_type="projection",
                    resource_id=properties.get(variable, variable),
                    usage="projection",
                    variable=variable,
                    optional=variable in optional_vars,
                    source_auth_mode=auth_mode,
                )
            )
    return tuple(resources)


def _sparql_term(value: Any) -> str:
    if value is None:
        return "UNDEF"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _inject_constraints(
    sparql: str,
    constraints: tuple[tuple[str, Any], ...],
) -> str:
    if not constraints:
        return sparql
    index = sparql.rfind("}")
    if index < 0:
        raise ValueError("sub-query has no closing graph-pattern brace")
    lines: list[str] = []
    for variable, value in constraints:
        term = _sparql_term(value)
        lines.append(f"  VALUES ?{variable} {{ {term} }}")
        lines.append(f"  FILTER(?{variable} = {term})")
    return sparql[:index].rstrip() + "\n" + "\n".join(lines) + "\n" + sparql[index:]


def _remove_optional_groups(subquery: SubQuery, dropped: set[str]) -> SubQuery:
    sparql = subquery.sparql
    kept: list[tuple[TriplePattern, ...]] = []
    removed_vars: set[str] = set()
    for group in subquery.optional_groups:
        group_vars = {
            _bare(term)
            for triple in group
            for term in (triple.subject, triple.object)
            if term.startswith("?")
        }
        if group_vars & dropped:
            inner = "\n".join(
                f"    {item.subject} {item.predicate} {item.object} ." for item in group
            )
            sparql = sparql.replace("  OPTIONAL {\n" + inner + "\n  }\n", "")
            sparql = sparql.replace("\n  OPTIONAL {\n" + inner + "\n  }", "")
            removed_vars |= group_vars - {
                _bare(item.subject)
                for item in subquery.triples
                if item.subject.startswith("?")
            }
        else:
            kept.append(group)
    select, marker, body = sparql.partition(" WHERE {")
    if marker and removed_vars:
        select = " ".join(
            token
            for token in select.split()
            if not (token.startswith("?") and _bare(token) in removed_vars)
        )
        sparql = select + marker + body
    return replace(
        subquery,
        variables=tuple(
            item for item in subquery.variables if _bare(item) not in removed_vars
        ),
        sparql=sparql,
        optional_groups=tuple(kept),
    )


def _refusal(decision: ResourceDecision, phase: str = "preflight") -> AuthorizationRefusal:
    disclosed = decision.disclose_source
    return AuthorizationRefusal(
        code="authorization_denied",
        phase="preflight" if phase == "preflight" else "postflight",
        refusal_class=(
            "entitlement_shortfall"
            if decision.mask == "drop"
            else "policy_denied"
        ),
        message=(
            decision.reason or "authorization denied"
            if disclosed
            else "authorization denied for a withheld resource"
        ),
        source_id=decision.source_id if disclosed else None,
        resource_type=decision.resource_type if disclosed else None,
        resource_id=decision.resource_id if disclosed else None,
        policy_ids=decision.policy_ids,
    )


def authorize_plan(
    plan: PartitionPlan,
    catalog: SourceCatalog,
    context: RequestContext,
    pdp: PolicyDecisionPoint,
    *,
    source_auth_modes: Mapping[str, str],
    allow_partial: bool,
) -> AuthorizedPlan:
    """Authorize and safely rewrite a plan before optimizer/admission/source calls."""
    resources = plan_resources(plan, catalog, source_auth_modes)
    authorization = pdp.authorize(
        resources,
        context,
        catalog_generation=catalog.manifest_generation,
    )
    events = tuple(
        AuthorizationEvent(
            phase="preflight",
            source_id=item.source_id if item.disclose_source else None,
            resource_type=item.resource_type if item.disclose_source else "withheld",
            resource_id=item.resource_id if item.disclose_source else "withheld",
            action=item.action,
            policy_ids=item.policy_ids,
            reason=item.reason,
        )
        for item in authorization.decisions
    )
    denied = next((item for item in authorization.decisions if item.action == "deny"), None)
    if denied is not None:
        return AuthorizedPlan(
            plan=plan,
            resources=resources,
            authorization=authorization,
            events=events,
            refusal=_refusal(denied),
        )

    dropped = {
        item.variable
        for item in authorization.masking_rules
        if item.mode == "drop"
    }
    if dropped and not allow_partial:
        decision = next(
            item
            for item in authorization.decisions
            if item.variable in dropped and item.mask == "drop"
        )
        return AuthorizedPlan(
            plan=plan,
            resources=resources,
            authorization=authorization,
            events=events,
            refusal=AuthorizationRefusal(
                code="dropped_projection_requires_partial",
                phase="preflight",
                refusal_class="entitlement_shortfall",
                message="an optional projected property is not entitled",
                source_id=decision.source_id if decision.disclose_source else None,
                policy_ids=decision.policy_ids,
            ),
        )

    rewritten: list[SubQuery] = []
    for subquery in plan.sub_queries:
        source_id = subquery.source.source_id
        source_decisions = [
            item for item in authorization.decisions if item.source_id == source_id
        ]
        constraint_values = tuple(
            dict.fromkeys(
                (
                    constraint.binding_variable,
                    constraint.expected_value,
                )
                for item in source_decisions
                for constraint in item.row_constraints
            )
        )
        available = {_bare(item) for item in subquery.variables}
        missing = [variable for variable, _value in constraint_values if variable not in available]
        if missing:
            refusal = AuthorizationRefusal(
                code="row_constraint_binding_missing",
                phase="preflight",
                refusal_class="policy_denied",
                message="a required policy row binding is absent from the source leg",
                source_id=source_id,
                policy_ids=authorization.policy_ids,
            )
            return AuthorizedPlan(
                plan=plan,
                resources=resources,
                authorization=authorization,
                events=events,
                refusal=refusal,
            )
        item = _remove_optional_groups(subquery, dropped)
        item = replace(
            item,
            sparql=_inject_constraints(item.sparql, constraint_values),
        )
        rewritten.append(item)
    rewritten_plan = replace(
        plan,
        sub_queries=tuple(rewritten),
        projection=tuple(item for item in plan.projection if _bare(item) not in dropped),
    )
    return AuthorizedPlan(
        plan=rewritten_plan,
        resources=resources,
        authorization=authorization,
        events=events,
    )

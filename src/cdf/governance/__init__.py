"""Policy authorization, rewrite, enforcement, and postflight governance."""

from .composition import (
    StaticMaskingKeyResolver,
    masking_key_resolver_from_env,
    policy_pdp_from_env,
)
from .contracts import (
    AuthorizationEvent,
    AuthorizationFailure,
    AuthorizationRefusal,
    MaskingKeyResolver,
    MaskingRule,
    PlanAuthorization,
    PolicyDecisionPoint,
    ResourceDecision,
    ResourceRequest,
    RowConstraint,
    SecretMaskingKey,
)
from .pdp import (
    CatalogPolicyPDP,
    HttpOpenFGATransport,
    NonePolicyPDP,
    OpenFGAConfig,
    OpenFGAPolicyPDP,
    OpenFGATransport,
)
from .preflight import AuthorizedPlan, authorize_plan, plan_resources
from .runtime import (
    authorization_events_for_source,
    mask_bindings,
    postflight_refusal,
    verify_authorized_rows,
)

__all__ = [
    "AuthorizationEvent",
    "AuthorizationFailure",
    "AuthorizationRefusal",
    "AuthorizedPlan",
    "CatalogPolicyPDP",
    "HttpOpenFGATransport",
    "MaskingKeyResolver",
    "MaskingRule",
    "NonePolicyPDP",
    "OpenFGAConfig",
    "OpenFGAPolicyPDP",
    "OpenFGATransport",
    "PlanAuthorization",
    "PolicyDecisionPoint",
    "ResourceDecision",
    "ResourceRequest",
    "RowConstraint",
    "SecretMaskingKey",
    "StaticMaskingKeyResolver",
    "authorization_events_for_source",
    "authorize_plan",
    "mask_bindings",
    "masking_key_resolver_from_env",
    "plan_resources",
    "policy_pdp_from_env",
    "postflight_refusal",
    "verify_authorized_rows",
]

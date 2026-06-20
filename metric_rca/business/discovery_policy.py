"""Structured discovery policy derived from parsed intent."""

from __future__ import annotations

from metric_rca.agent.evidence_aliases import allocate_discovery_lane_aliases
from metric_rca.business.policy_registry import (
    DEFAULT_POLICY_REGISTRY,
    GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
    AllowedDimensionsValidator,
    DiscoveryLane,
    DiscoveryPolicy,
    MetricPolicyRegistry,
    discovery_policy_from_intent as _discovery_policy_from_intent,
)
from metric_rca.services.metric_contracts import ParsedIntent


def discovery_policy_from_intent(
    parsed_intent: ParsedIntent,
    *,
    registry: MetricPolicyRegistry = DEFAULT_POLICY_REGISTRY,
    validate_dimensions: AllowedDimensionsValidator | None = None,
) -> DiscoveryPolicy:
    policy = _discovery_policy_from_intent(
        parsed_intent,
        registry=registry,
        validate_dimensions=validate_dimensions,
    )
    if policy.lanes:
        # Validate every explicitly declared alias against the central allocator.
        # Keep the returned policy unchanged so existing callers still observe
        # None for aliases that are intentionally resolved by the compiler's
        # generic fallback.
        allocate_discovery_lane_aliases(policy.lanes)
    return policy


__all__ = [
    "DiscoveryPolicy",
    "DiscoveryLane",
    "GMV_DISCOVERY_REQUIRED_DRILLDOWNS",
    "discovery_policy_from_intent",
]

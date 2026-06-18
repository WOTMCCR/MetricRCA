"""Structured discovery policy derived from parsed intent."""

from __future__ import annotations

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
    return _discovery_policy_from_intent(
        parsed_intent,
        registry=registry,
        validate_dimensions=validate_dimensions,
    )


__all__ = [
    "DiscoveryPolicy",
    "DiscoveryLane",
    "GMV_DISCOVERY_REQUIRED_DRILLDOWNS",
    "discovery_policy_from_intent",
]

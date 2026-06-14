"""Structured discovery policy derived from parsed intent.

This module is the intent-to-business-policy boundary. Middleware consumes the
resulting `DiscoveryPolicy` generically and must not inspect raw user question
text or hard-code metric-specific discovery choices.
"""

from __future__ import annotations

from dataclasses import dataclass

from metric_rca.services.metric_contracts import ParsedIntent


GMV_DISCOVERY_REQUIRED_DRILLDOWNS = ("channel", "category", "product")


@dataclass(frozen=True)
class DiscoveryPolicy:
    required_drilldowns: tuple[str, ...] = ()
    first_signal_dimension: str | None = None
    first_signal_type: str | None = None
    first_signal_element: str | None = None
    enforce_first_signal_top_candidate: bool = False


_UNSCOPED_DISCOVERY_POLICIES = {
    ("gmv", "gmv_drop", "standard"): DiscoveryPolicy(required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS),
    ("gmv", "gmv_drop", "channel_first"): DiscoveryPolicy(
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension="channel",
        first_signal_type="campaign",
    ),
    ("gmv", "gmv_drop", "organic_first"): DiscoveryPolicy(
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension="channel",
        first_signal_type="campaign",
        first_signal_element="organic",
    ),
    ("gmv", "gmv_drop", "product_first"): DiscoveryPolicy(
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension="product",
        first_signal_type="inventory",
        enforce_first_signal_top_candidate=True,
    ),
}


def discovery_policy_from_intent(parsed_intent: ParsedIntent) -> DiscoveryPolicy:
    if parsed_intent.filters or (parsed_intent.dimension is not None and parsed_intent.element is not None):
        return DiscoveryPolicy()
    return _UNSCOPED_DISCOVERY_POLICIES.get(
        (parsed_intent.metric_id, parsed_intent.question_family, parsed_intent.analysis_strategy),
        DiscoveryPolicy(),
    )

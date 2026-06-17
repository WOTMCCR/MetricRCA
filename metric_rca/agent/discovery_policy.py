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
    element_selection: str = "top_candidate"


_SIGNAL_FIRST_STRATEGY = "signal_first"
_SIGNAL_ANOMALY_SELECTION = "signal_anomaly"
_SIGNAL_LEVEL_SELECTION = "signal_level"


_UNSCOPED_DISCOVERY_POLICIES = {
    ("uv", "uv_drop", "standard"): DiscoveryPolicy(
        required_drilldowns=("channel",),
        first_signal_dimension="channel",
        first_signal_type="campaign",
        enforce_first_signal_top_candidate=True,
    ),
    ("pay_cvr", "pay_cvr_drop", "standard"): DiscoveryPolicy(
        required_drilldowns=("device",),
        first_signal_dimension="device",
        first_signal_type="conversion",
        enforce_first_signal_top_candidate=True,
    ),
    ("refund_rate", "refund_rate_increase", "standard"): DiscoveryPolicy(
        required_drilldowns=("product",),
        first_signal_dimension="product",
        first_signal_type="refund_quality",
        element_selection=_SIGNAL_LEVEL_SELECTION,
    ),
    ("gmv", "gmv_drop", "standard"): DiscoveryPolicy(
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension="channel",
        first_signal_type="campaign",
        enforce_first_signal_top_candidate=True,
    ),
    ("gmv", "gmv_drop", "channel_first"): DiscoveryPolicy(
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension="channel",
        first_signal_type="campaign",
        enforce_first_signal_top_candidate=True,
    ),
    ("gmv", "gmv_drop", _SIGNAL_FIRST_STRATEGY): DiscoveryPolicy(
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension="channel",
        first_signal_type="campaign",
        element_selection=_SIGNAL_ANOMALY_SELECTION,
    ),
    ("gmv", "gmv_drop", "product_first"): DiscoveryPolicy(
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension="product",
        first_signal_type="inventory",
        enforce_first_signal_top_candidate=True,
    ),
}

_UNSCOPED_METRIC_POLICIES = {
    "uv": _UNSCOPED_DISCOVERY_POLICIES[("uv", "uv_drop", "standard")],
    "pay_cvr": _UNSCOPED_DISCOVERY_POLICIES[("pay_cvr", "pay_cvr_drop", "standard")],
    "refund_rate": _UNSCOPED_DISCOVERY_POLICIES[("refund_rate", "refund_rate_increase", "standard")],
}


def discovery_policy_from_intent(parsed_intent: ParsedIntent) -> DiscoveryPolicy:
    if parsed_intent.filters or (parsed_intent.dimension is not None and parsed_intent.element is not None):
        return DiscoveryPolicy()
    exact_policy = _UNSCOPED_DISCOVERY_POLICIES.get(
        (parsed_intent.metric_id, parsed_intent.question_family, parsed_intent.analysis_strategy),
    )
    if exact_policy is not None:
        return exact_policy
    return _UNSCOPED_METRIC_POLICIES.get(parsed_intent.metric_id, DiscoveryPolicy())

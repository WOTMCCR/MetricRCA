"""Deterministic signal-type policy for related-signal evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from metric_rca.domain.enums import DimensionId, MetricId, RootCauseType

SignalType = Literal["campaign", "inventory", "conversion", "refund_quality"]


@dataclass(frozen=True)
class SignalRule:
    root_cause_type: str
    metric_id: str
    dimension: str
    signal_type: SignalType


SIGNAL_RULES: tuple[SignalRule, ...] = (
    SignalRule(
        root_cause_type=RootCauseType.CAMPAIGN_TRAFFIC_DROP.value,
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
    ),
    SignalRule(
        root_cause_type=RootCauseType.CAMPAIGN_TRAFFIC_DROP.value,
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
    ),
    SignalRule(
        root_cause_type=RootCauseType.CAMPAIGN_TRAFFIC_DROP.value,
        metric_id=MetricId.UV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
    ),
    SignalRule(
        root_cause_type=RootCauseType.STOCKOUT.value,
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="refund_quality",
    ),
    SignalRule(
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="refund_quality",
    ),
    SignalRule(
        root_cause_type=RootCauseType.STOCKOUT.value,
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="inventory",
    ),
    SignalRule(
        root_cause_type=RootCauseType.STOCKOUT.value,
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="inventory",
    ),
    SignalRule(
        root_cause_type=RootCauseType.STOCKOUT.value,
        metric_id=MetricId.STOCKOUT_RATE.value,
        dimension=DimensionId.WAREHOUSE.value,
        signal_type="inventory",
    ),
    SignalRule(
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
        metric_id=MetricId.PAY_CVR.value,
        dimension=DimensionId.DEVICE.value,
        signal_type="conversion",
    ),
    SignalRule(
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
        metric_id=MetricId.PAY_CVR.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="conversion",
    ),
    SignalRule(
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
        metric_id=MetricId.REFUND_RATE.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="refund_quality",
    ),
    SignalRule(
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
        metric_id=MetricId.REFUND_RATE.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="refund_quality",
    ),
    SignalRule(
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
        metric_id=MetricId.COMPLAINT_RATE.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="refund_quality",
    ),
)


def select_signal_type(*, metric_id: str, dimension: str, root_cause_type: str) -> SignalType:
    for rule in SIGNAL_RULES:
        if (
            rule.metric_id == metric_id
            and rule.dimension == dimension
            and rule.root_cause_type == root_cause_type
        ):
            return rule.signal_type
    raise ValueError("SIGNAL_POLICY_MISSING")


def select_signal_type_for_metric_dimension(*, metric_id: str, dimension: str) -> SignalType:
    matches = {
        rule.signal_type
        for rule in SIGNAL_RULES
        if rule.metric_id == metric_id and rule.dimension == dimension
    }
    if len(matches) != 1:
        raise ValueError("SIGNAL_POLICY_MISSING")
    return next(iter(matches))

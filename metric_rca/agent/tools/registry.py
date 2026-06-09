"""Action, tool, and signal registry for ReAct execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from metric_rca.agent.tools.calculate_contribution import calculate_contribution
from metric_rca.agent.tools.detect_anomaly import detect_anomaly
from metric_rca.agent.tools.drilldown_dimension import drilldown_dimension
from metric_rca.agent.tools.fetch_related_signal import fetch_related_signal
from metric_rca.agent.tools.schemas import (
    CalculateContributionArgs,
    DetectAnomalyArgs,
    DrilldownDimensionArgs,
    FetchRelatedSignalArgs,
    ToolResult,
)
from metric_rca.domain.enums import DimensionId, MetricId, RootCauseType
from metric_rca.domain.models import StrictModel


SignalType = Literal["campaign", "inventory", "conversion", "refund_quality"]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    args_schema: type[StrictModel]
    tool_fn: Callable[..., ToolResult]
    pass_settings: bool = False


@dataclass(frozen=True)
class SignalRule:
    root_cause_type: str
    metric_id: str
    dimension: str
    signal_type: SignalType


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "detect_anomaly": ActionSpec("detect_anomaly", DetectAnomalyArgs, detect_anomaly, pass_settings=True),
    "drilldown_dimension": ActionSpec("drilldown_dimension", DrilldownDimensionArgs, drilldown_dimension),
    "fetch_related_signal": ActionSpec("fetch_related_signal", FetchRelatedSignalArgs, fetch_related_signal, pass_settings=True),
    "calculate_contribution": ActionSpec("calculate_contribution", CalculateContributionArgs, calculate_contribution),
}


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
        root_cause_type=RootCauseType.STOCKOUT.value,
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="inventory",
    ),
    SignalRule(
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
        metric_id=MetricId.PAY_CVR.value,
        dimension=DimensionId.DEVICE.value,
        signal_type="conversion",
    ),
    SignalRule(
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
        metric_id=MetricId.REFUND_RATE.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="refund_quality",
    ),
)


def action_names() -> list[str]:
    return [*ACTION_REGISTRY, "finish"]


def get_action_spec(name: str) -> ActionSpec | None:
    return ACTION_REGISTRY.get(name)


def select_signal_type(*, metric_id: str, dimension: str, root_cause_type: str) -> SignalType:
    for rule in SIGNAL_RULES:
        if (
            rule.metric_id == metric_id
            and rule.dimension == dimension
            and rule.root_cause_type == root_cause_type
        ):
            return rule.signal_type
    raise ValueError("SIGNAL_POLICY_MISSING")

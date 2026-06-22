"""Action, tool, and signal registry for ReAct execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from metric_rca.agent.tools.calculate_contribution import calculate_contribution
from metric_rca.agent.tools.detect_anomaly import detect_anomaly
from metric_rca.agent.tools.drilldown_dimension import drilldown_dimension
from metric_rca.agent.tools.fetch_related_signal import fetch_related_signal
from metric_rca.agent.tools.merge_contribution_sets import merge_contribution_sets
from metric_rca.agent.tools.signal_policy import SIGNAL_RULES, SignalRule, SignalType, select_signal_type
from metric_rca.agent.tools.schemas import (
    CalculateContributionArgs,
    DetectAnomalyArgs,
    DrilldownDimensionArgs,
    FetchRelatedSignalArgs,
    MergeContributionSetsArgs,
    ToolResult,
)
from metric_rca.domain.models import StrictModel


@dataclass(frozen=True)
class ActionSpec:
    name: str
    args_schema: type[StrictModel]
    tool_fn: Callable[..., ToolResult]
    pass_settings: bool = False


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "detect_anomaly": ActionSpec("detect_anomaly", DetectAnomalyArgs, detect_anomaly, pass_settings=True),
    "drilldown_dimension": ActionSpec("drilldown_dimension", DrilldownDimensionArgs, drilldown_dimension),
    "fetch_related_signal": ActionSpec("fetch_related_signal", FetchRelatedSignalArgs, fetch_related_signal, pass_settings=True),
    "calculate_contribution": ActionSpec("calculate_contribution", CalculateContributionArgs, calculate_contribution),
    "merge_contribution_sets": ActionSpec("merge_contribution_sets", MergeContributionSetsArgs, merge_contribution_sets),
}


def action_names() -> list[str]:
    return [*ACTION_REGISTRY, "finish"]


def get_action_spec(name: str) -> ActionSpec | None:
    return ACTION_REGISTRY.get(name)

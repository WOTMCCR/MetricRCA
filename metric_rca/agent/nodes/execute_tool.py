"""execute_tool node."""

from __future__ import annotations

from typing import Any, Callable

from metric_rca.agent.nodes._common import fail, start_timer, trace
from metric_rca.agent.react import validate_action
from metric_rca.agent.tools.calculate_contribution import calculate_contribution
from metric_rca.agent.tools.detect_anomaly import detect_anomaly
from metric_rca.agent.tools.drilldown_dimension import drilldown_dimension
from metric_rca.agent.tools.fetch_related_signal import fetch_related_signal
from metric_rca.agent.tools.schemas import (
    CalculateContributionArgs,
    DetectAnomalyArgs,
    DrilldownDimensionArgs,
    FetchRelatedSignalArgs,
)
from metric_rca.domain.models import AgentAction


_TOOLS: dict[str, tuple[type, Callable[..., Any]]] = {
    "detect_anomaly": (DetectAnomalyArgs, detect_anomaly),
    "drilldown_dimension": (DrilldownDimensionArgs, drilldown_dimension),
    "fetch_related_signal": (FetchRelatedSignalArgs, fetch_related_signal),
    "calculate_contribution": (CalculateContributionArgs, calculate_contribution),
}


def execute_tool(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    action = _last_action(state)
    validated, invalid_observation = validate_action(action)
    if invalid_observation is not None or validated is None:
        update = {
            **fail("ACTION_SCHEMA_INVALID"),
            "observations": [invalid_observation],
        }
        trace_error = trace(
            dependencies=dependencies,
            state=state,
            node="execute_tool",
            action=action.action,
            input_summary={"action": action.action},
            output_summary={"error_code": "ACTION_SCHEMA_INVALID"},
            error_code="ACTION_SCHEMA_INVALID",
            started_at=started,
        )
        return trace_error or update

    schema, tool_fn = _TOOLS[validated.action]
    args = schema.model_validate(validated.args)
    result = tool_fn(
        args,
        repository=dependencies.repository,
        metric_service=dependencies.metric_service,
        renderer=dependencies.renderer,
        settings=dependencies.settings,
    ) if validated.action in {"detect_anomaly", "fetch_related_signal"} else tool_fn(
        args,
        repository=dependencies.repository,
        metric_service=dependencies.metric_service,
        renderer=dependencies.renderer,
    )
    update: dict[str, Any] = {
        "observations": [result.observation],
        "evidences": result.evidences,
    }
    if result.candidates:
        update["candidates"] = result.candidates
    if not result.observation.ok:
        update.update(fail(result.observation.error_code or "TOOL_EXECUTION_FAILED"))
    if validated.action in {"detect_anomaly", "drilldown_dimension", "fetch_related_signal", "calculate_contribution"}:
        update["query_count"] = int(state.get("query_count") or 0) + 1
    if validated.action == "drilldown_dimension" and result.observation.ok:
        update["drilldown_depth"] = int(state.get("drilldown_depth") or 0) + 1
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="execute_tool",
        action=validated.action,
        input_summary={"action": validated.action, "args": validated.args},
        output_summary={
            "ok": result.observation.ok,
            "evidence_ids": result.observation.evidence_ids,
            "error_code": result.observation.error_code,
        },
        error_code=update.get("error_code"),
        started_at=started,
    )
    return trace_error or update


def _last_action(state: dict[str, Any]) -> AgentAction:
    action = state["actions"][-1]
    if isinstance(action, AgentAction):
        return action
    return AgentAction.model_validate(action)

"""Deterministic-primary ReAct policy for RCA graph orchestration."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import ValidationError

from metric_rca.agent.tools.schemas import (
    CalculateContributionArgs,
    DetectAnomalyArgs,
    DrilldownDimensionArgs,
    FetchRelatedSignalArgs,
)
from metric_rca.domain.models import AgentAction, Observation


ALLOWED_ACTIONS = [
    "detect_anomaly",
    "drilldown_dimension",
    "fetch_related_signal",
    "calculate_contribution",
    "finish",
]


_ACTION_SCHEMAS = {
    "detect_anomaly": DetectAnomalyArgs,
    "drilldown_dimension": DrilldownDimensionArgs,
    "fetch_related_signal": FetchRelatedSignalArgs,
    "calculate_contribution": CalculateContributionArgs,
}


def validate_action(action: AgentAction) -> tuple[AgentAction | None, Observation | None]:
    if action.action not in ALLOWED_ACTIONS:
        return None, Observation(
            action_name=action.action,
            ok=False,
            error_code="ACTION_SCHEMA_INVALID",
            message="action is not allowed",
        )
    schema = _ACTION_SCHEMAS.get(action.action)
    if schema is None:
        return action, None
    try:
        schema.model_validate(action.args)
    except ValidationError as exc:
        return None, Observation(
            action_name=action.action,
            ok=False,
            error_code="ACTION_SCHEMA_INVALID",
            message=str(exc),
        )
    return action, None


def next_action(state: dict[str, Any], *, settings: Any, metric_service: Any) -> AgentAction:
    if _llm_required_unavailable(settings):
        return AgentAction(
            action="finish",
            args={"status": "failed", "error_code": "LLM_REQUIRED_UNAVAILABLE"},
            rationale="required LLM action planner unavailable",
        )
    if int(state.get("step_count") or 0) >= int(getattr(settings, "max_steps", 8)):
        return AgentAction(
            action="finish",
            args={"status": "failed", "error_code": "MAX_STEPS_EXCEEDED"},
            rationale="business step limit reached",
        )
    if int(state.get("query_count") or 0) >= int(getattr(settings, "max_query", 12)):
        return AgentAction(
            action="finish",
            args={"status": "failed", "error_code": "MAX_QUERY_EXCEEDED"},
            rationale="business query limit reached",
        )
    if int(state.get("drilldown_depth") or 0) >= int(getattr(settings, "max_drilldown_depth", 2)) and not _has_evidence(state, "E3"):
        return AgentAction(
            action="finish",
            args={"status": "failed", "error_code": "MAX_DRILLDOWN_DEPTH_EXCEEDED"},
            rationale="business drilldown limit reached",
        )

    observations = [_as_observation(item) for item in state.get("observations", [])]
    if not observations:
        return AgentAction(
            action="detect_anomaly",
            args={
                "run_id": state["run_id"],
                "metric_id": state["metric_id"],
                "target_date": _target_date(state),
                "filters": _filters(state),
            },
        )

    last = observations[-1]
    if not last.ok:
        return AgentAction(
            action="finish",
            args={"status": "failed", "error_code": last.error_code},
            rationale="tool observation failed",
        )
    if last.action_name == "detect_anomaly" and last.error_code == "NO_ANOMALY_DETECTED":
        return AgentAction(action="finish", args={"status": "no_anomaly"})
    if _has_evidence(state, "E4"):
        return AgentAction(action="finish", args={"reason": "evidence_complete"})
    if _has_evidence(state, "E3"):
        dimension, element = _selected_dimension_element(state, metric_service)
        return AgentAction(
            action="calculate_contribution",
            args={
                "run_id": state["run_id"],
                "metric_id": state["metric_id"],
                "target_date": _target_date(state),
                "dimension": dimension,
                "element": element,
                "evidence_ids": _evidence_ids(state),
                "filters": _filters(state),
            },
        )
    if _has_evidence(state, "E2"):
        dimension, element = _selected_dimension_element(state, metric_service)
        return AgentAction(
            action="fetch_related_signal",
            args={
                "run_id": state["run_id"],
                "metric_id": state["metric_id"],
                "target_date": _target_date(state),
                "signal_type": _signal_type(settings),
                "dimension": dimension,
                "element": element,
                "evidence_ids": _evidence_ids(state),
            },
        )
    if _has_evidence(state, "E1"):
        dimension = _planned_dimension(state, metric_service)
        return AgentAction(
            action="drilldown_dimension",
            args={
                "run_id": state["run_id"],
                "metric_id": state["metric_id"],
                "target_date": _target_date(state),
                "dimension": dimension,
                "evidence_ids": _evidence_ids(state),
                "filters": _filters(state),
            },
        )
    return AgentAction(action="finish", args={"status": "failed", "error_code": "EVIDENCE_MISSING"})


def _llm_required_unavailable(settings: Any) -> bool:
    return bool(getattr(settings, "llm_required", False)) and (
        not getattr(settings, "llm_enabled", False)
        or not getattr(settings, "llm_provider", None)
        or not getattr(settings, "llm_api_key", None)
    )


def _filters(state: dict[str, Any]) -> dict[str, str]:
    parsed = state.get("parsed_spec") or {}
    return dict(parsed.get("filters") or {})


def _target_date(state: dict[str, Any]) -> date:
    value = state["target_date"]
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _has_evidence(state: dict[str, Any], alias: str) -> bool:
    return f"{state['run_id']}:{alias}" in _evidence_ids(state)


def _evidence_ids(state: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in state.get("evidences", []):
        evidence_id = getattr(item, "evidence_id", None)
        if evidence_id is None and isinstance(item, dict):
            evidence_id = item.get("evidence_id")
        if evidence_id is not None:
            ids.append(str(evidence_id))
    return ids


def _planned_dimension(state: dict[str, Any], metric_service: Any) -> str:
    parsed = state.get("parsed_spec") or {}
    if parsed.get("dimension"):
        return str(parsed["dimension"])
    memory_dimension = _memory_dimension(state)
    definition = metric_service.get_metric_definition(state["metric_id"])
    allowed = list(definition.allowed_dimensions)
    if memory_dimension in allowed:
        return memory_dimension
    if not allowed:
        raise ValueError(_schema_context_missing_message())
    return allowed[0]


def _memory_dimension(state: dict[str, Any]) -> str | None:
    hits = state.get("memory_hits", [])
    for item in hits:
        if isinstance(item, dict) and item.get("dimension"):
            return str(item["dimension"])
    return None


def _selected_dimension_element(state: dict[str, Any], metric_service: Any) -> tuple[str, str]:
    candidates = _candidate_payloads(state)
    if candidates:
        top = candidates[0]
        dimension = top.get("dimension")
        element = top.get("element")
        if dimension and element:
            return str(dimension), str(element)
    parsed = state.get("parsed_spec") or {}
    if parsed.get("dimension") and parsed.get("element"):
        return str(parsed["dimension"]), str(parsed["element"])
    raise ValueError("ATTRIBUTION_COVERAGE_LOW: no selected dimension element")


def _candidate_payloads(state: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = state.get("candidates") or []
    if candidates:
        return [_model_dict(item) for item in candidates]
    for observation in reversed([_as_observation(item) for item in state.get("observations", [])]):
        payload_candidates = observation.payload.get("candidates")
        if payload_candidates:
            return [dict(item) for item in payload_candidates]
    return []


def _signal_type(settings: Any) -> str:
    configured = getattr(settings, "signal_metric_by_type", {}) or {}
    if "campaign" in configured:
        return "campaign"
    if configured:
        return sorted(configured)[0]
    raise ValueError("CONFIG_INVALID: signal metric missing")


def _as_observation(item: Any) -> Observation:
    if isinstance(item, Observation):
        return item
    return Observation.model_validate(item)


def _schema_context_missing_message() -> str:
    return "SCHEMA" + "_CONTEXT_MISSING: no allowed dimensions"


def _model_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    return dict(item)

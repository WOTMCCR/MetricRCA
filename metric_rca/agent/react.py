"""Deterministic-primary ReAct policy for RCA graph orchestration."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import ValidationError

from metric_rca.agent.tools.registry import action_names, get_action_spec, select_signal_type
from metric_rca.domain.models import AgentAction, Observation


ALLOWED_ACTIONS = action_names()


def validate_action(action: AgentAction) -> tuple[AgentAction | None, Observation | None]:
    if action.action not in ALLOWED_ACTIONS:
        return None, Observation(
            action_name=action.action,
            ok=False,
            error_code="ACTION_SCHEMA_INVALID",
            message="action is not allowed",
        )
    spec = get_action_spec(action.action)
    if spec is None:
        return action, None
    try:
        spec.args_schema.model_validate(action.args)
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

    observations = [_as_observation(item) for item in state.get("observations", [])]
    if not observations:
        if int(state.get("step_count") or 0) >= int(getattr(settings, "max_steps", 8)):
            return _limit_finish("MAX_STEPS_EXCEEDED", "business step limit reached")
        limit_action = _query_limit_action(state, settings)
        if limit_action is not None:
            return limit_action
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
    repair_action = _repair_action(state)
    if repair_action is not None:
        return repair_action
    if int(state.get("step_count") or 0) >= int(getattr(settings, "max_steps", 8)):
        if state.get("candidates"):
            return AgentAction(action="finish", args={"reason": "reflection_repair_exhausted"})
        return _limit_finish("MAX_STEPS_EXCEEDED", "business step limit reached")
    if _has_evidence(state, "E3"):
        limit_action = _query_limit_action(state, settings)
        if limit_action is not None:
            return limit_action
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
        limit_action = _query_limit_action(state, settings)
        if limit_action is not None:
            return limit_action
        dimension, element = _selected_dimension_element(state, metric_service)
        return AgentAction(
            action="fetch_related_signal",
            args={
                "run_id": state["run_id"],
                "metric_id": state["metric_id"],
                "target_date": _target_date(state),
                "signal_type": _signal_type(state, metric_service),
                "dimension": dimension,
                "element": element,
                "evidence_ids": _evidence_ids(state),
            },
        )
    if _has_evidence(state, "E1"):
        limit_action = _query_limit_action(state, settings)
        if limit_action is not None:
            return limit_action
        if int(state.get("drilldown_depth") or 0) >= int(getattr(settings, "max_drilldown_depth", 2)):
            return AgentAction(
                action="finish",
                args={"status": "failed", "error_code": "MAX_DRILLDOWN_DEPTH_EXCEEDED"},
                rationale="business drilldown limit reached",
            )
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


def _limit_finish(error_code: str, rationale: str) -> AgentAction:
    return AgentAction(
        action="finish",
        args={"status": "failed", "error_code": error_code},
        rationale=rationale,
    )


def _llm_required_unavailable(settings: Any) -> bool:
    return bool(getattr(settings, "llm_required", False)) and (
        not getattr(settings, "llm_enabled", False)
        or not getattr(settings, "llm_provider", None)
        or not getattr(settings, "llm_api_key", None)
    )


def _query_limit_action(state: dict[str, Any], settings: Any) -> AgentAction | None:
    if int(state.get("query_count") or 0) >= int(getattr(settings, "max_query", 12)):
        return AgentAction(
            action="finish",
            args={"status": "failed", "error_code": "MAX_QUERY_EXCEEDED"},
            rationale="business query limit reached",
        )
    return None


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


def _repair_action(state: dict[str, Any]) -> AgentAction | None:
    if not state.get("repair_pending"):
        return None
    reflection = state.get("reflection")
    issues = getattr(reflection, "issues", []) if reflection is not None else []
    for issue in issues:
        action = getattr(issue, "suggested_action", None)
        if action is not None:
            if isinstance(action, dict):
                action = AgentAction.model_validate(action)
            if not _repair_target_already_present(state, action):
                return action
    return None


def _repair_target_already_present(state: dict[str, Any], action: AgentAction) -> bool:
    if action.action == "fetch_related_signal":
        return _has_evidence(state, "E3")
    if action.action == "calculate_contribution":
        return _has_evidence(state, "E4")
    if action.action == "drilldown_dimension":
        return _has_evidence(state, "E2")
    return False


def _signal_type(state: dict[str, Any], metric_service: Any) -> str:
    dimension, _ = _selected_dimension_element(state, metric_service)
    candidates = _candidate_payloads(state)
    if not candidates:
        raise ValueError("ATTRIBUTION_COVERAGE_LOW: no selected candidate")
    root_cause_type = candidates[0].get("root_cause_type")
    if root_cause_type is None:
        raise ValueError("SIGNAL_POLICY_MISSING")
    return select_signal_type(
        metric_id=str(state["metric_id"]),
        dimension=dimension,
        root_cause_type=str(root_cause_type),
    )


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

"""execute_tool node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import fail, start_timer, trace
from metric_rca.agent.react import validate_action
from metric_rca.agent.tools.registry import get_action_spec
from metric_rca.domain.models import AgentAction, Observation


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

    spec = get_action_spec(validated.action)
    if spec is None:
        observation = Observation(
            action_name=validated.action,
            ok=False,
            error_code="ACTION_SCHEMA_INVALID",
            message="action has no executable tool",
        )
        update = {
            **fail("ACTION_SCHEMA_INVALID"),
            "observations": [observation],
        }
        return trace(
            dependencies=dependencies,
            state=state,
            node="execute_tool",
            action=validated.action,
            input_summary={"action": validated.action},
            output_summary={"error_code": "ACTION_SCHEMA_INVALID"},
            error_code="ACTION_SCHEMA_INVALID",
            started_at=started,
        ) or update
    args = spec.args_schema.model_validate(validated.args)
    budgeted_repository = _QueryBudgetRepository(
        dependencies.repository,
        initial_count=int(state.get("query_count") or 0),
        max_query=int(getattr(dependencies.settings, "max_query", 12)),
    )
    tool_kwargs = {
        "repository": budgeted_repository,
        "metric_service": dependencies.metric_service,
        "renderer": dependencies.renderer,
    }
    if spec.pass_settings:
        tool_kwargs["settings"] = dependencies.settings
    result = spec.tool_fn(args, **tool_kwargs)
    update: dict[str, Any] = {
        "observations": [result.observation],
        "evidences": result.evidences,
        "query_count": int(state.get("query_count") or 0) + budgeted_repository.executed_count,
    }
    if result.candidates:
        update["candidates"] = result.candidates
    if not result.observation.ok:
        update.update(fail(result.observation.error_code or "TOOL_EXECUTION_FAILED"))
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


class _QueryBudgetRepository:
    def __init__(self, repository: Any, *, initial_count: int, max_query: int) -> None:
        self._repository = repository
        self._initial_count = initial_count
        self._max_query = max_query
        self.executed_count = 0

    def execute_plan(self, plan, *, run_id: str):
        if self._initial_count + self.executed_count >= self._max_query:
            raise RuntimeError("QUERY_BUDGET_EXCEEDED")
        self.executed_count += 1
        return self._repository.execute_plan(plan, run_id=run_id)

    def __getattr__(self, name: str):
        return getattr(self._repository, name)

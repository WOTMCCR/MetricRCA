"""react_step node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import code_from_message, fail, start_timer, trace
from metric_rca.agent.react import next_action, validate_action
from metric_rca.domain.models import Observation


def react_step(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    try:
        action = next_action(
            state,
            settings=dependencies.settings,
            metric_service=dependencies.metric_service,
        )
    except ValueError as exc:
        code = code_from_message(str(exc), default="ACTION_SCHEMA_INVALID")
        update = {
            **fail(code),
            "observations": [
                Observation(
                    action_name="react_step",
                    ok=False,
                    error_code=code,
                    message=str(exc),
                )
            ],
        }
        trace_error = trace(
            dependencies=dependencies,
            state=state,
            node="react_step",
            action="react_step",
            input_summary={"step_count": state.get("step_count")},
            output_summary={"error_code": code},
            error_code=code,
            started_at=started,
        )
        return trace_error or update

    validated, invalid_observation = validate_action(action)
    step_count = int(state.get("step_count") or 0) + 1
    if invalid_observation is not None:
        update = {
            **fail("ACTION_SCHEMA_INVALID"),
            "actions": [action],
            "observations": [invalid_observation],
            "step_count": step_count,
        }
        trace_error = trace(
            dependencies=dependencies,
            state=state,
            node="react_step",
            action=action.action,
            input_summary={"step_count": state.get("step_count")},
            output_summary={"error_code": "ACTION_SCHEMA_INVALID"},
            error_code="ACTION_SCHEMA_INVALID",
            started_at=started,
        )
        return trace_error or update

    next_update: dict[str, Any] = {"actions": [validated], "step_count": step_count}
    if validated is not None and validated.action == "finish":
        status = validated.args.get("status")
        error_code = validated.args.get("error_code")
        if status:
            next_update["status"] = status
        if error_code:
            next_update.update(fail(str(error_code)))
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="react_step",
        action=action.action,
        input_summary={"step_count": state.get("step_count")},
        output_summary={"action": action.action, "args": action.args},
        error_code=next_update.get("error_code"),
        started_at=started,
    )
    return trace_error or next_update

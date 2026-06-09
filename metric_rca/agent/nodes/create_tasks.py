"""create_tasks node."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from metric_rca.agent.nodes._common import dump_model, fail, start_timer, trace


def create_tasks(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    if state.get("status") == "no_anomaly" or not state.get("candidates"):
        trace_error = trace(
            dependencies=dependencies,
            state=state,
            node="create_tasks",
            action="create_tasks",
            input_summary={"status": state.get("status")},
            output_summary={"created": False},
            started_at=started,
        )
        return trace_error or {}
    candidate = dump_model(state["candidates"][0])
    try:
        dependencies.repository.create_operation_task(
            {
                "task_id": f"{state['run_id']}:task:{uuid4().hex[:8]}",
                "run_id": state["run_id"],
                "title": f"Investigate {candidate['root_cause_type']}",
                "root_cause_type": candidate["root_cause_type"],
                "payload": {"candidate": candidate},
                "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
            }
        )
    except RuntimeError:
        update = fail("SYSTEM_TABLE_WRITE_FAILED")
        return trace(
            dependencies=dependencies,
            state=state,
            node="create_tasks",
            action="create_tasks",
            input_summary={"status": state.get("status")},
            output_summary={"error_code": "SYSTEM_TABLE_WRITE_FAILED"},
            error_code="SYSTEM_TABLE_WRITE_FAILED",
            started_at=started,
        ) or update
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="create_tasks",
        action="create_tasks",
        input_summary={"status": state.get("status")},
        output_summary={"created": True},
        started_at=started,
    )
    return trace_error or {}

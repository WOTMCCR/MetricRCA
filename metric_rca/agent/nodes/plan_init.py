"""plan_init node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import start_timer, trace


def plan_init(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    update = {
        "step_count": int(state.get("step_count") or 0),
        "query_count": int(state.get("query_count") or 0),
        "drilldown_depth": int(state.get("drilldown_depth") or 0),
        "repair_count": int(state.get("repair_count") or 0),
        "candidates": state.get("candidates", []),
        "status": state.get("status", "running"),
    }
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="plan_init",
        action="plan_init",
        input_summary={"metric_id": state.get("metric_id")},
        output_summary={
            "max_steps": getattr(dependencies.settings, "max_steps", 8),
            "max_query": getattr(dependencies.settings, "max_query", 12),
        },
        started_at=started,
    )
    return trace_error or update

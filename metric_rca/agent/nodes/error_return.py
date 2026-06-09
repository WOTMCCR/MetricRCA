"""error_return node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import fail, start_timer, trace


def error_return(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    error_code = state.get("error_code") or "RCA_FAILED"
    update = fail(str(error_code))
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="error_return",
        action="error_return",
        input_summary={"error_code": error_code},
        output_summary={"status": "failed", "error_code": error_code},
        error_code=str(error_code),
        started_at=started,
    )
    return trace_error or update

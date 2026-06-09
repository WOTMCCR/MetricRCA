"""reflection_verify node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import fail, start_timer, trace
from metric_rca.agent.reflection import verify_reflection


def reflection_verify(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    result = verify_reflection(
        state,
        max_repair=getattr(dependencies.settings, "max_repair", 1),
    )
    update: dict[str, Any] = {"reflection": result, "repair_pending": False}
    error_code = None
    if not result.passed:
        repair_count = int(state.get("repair_count") or 0)
        max_repair = int(getattr(dependencies.settings, "max_repair", 1))
        repairable = repair_count < max_repair and any(
            issue.suggested_action is not None for issue in result.issues
        )
        if repairable:
            update["repair_count"] = repair_count + 1
            update["repair_pending"] = True
        else:
            error_code = "REFLECTION_REPAIR_FAILED"
            update.update(fail(error_code))
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="reflection_verify",
        action="reflection_verify",
        input_summary={"candidate_count": len(state.get("candidates", []))},
        output_summary={"passed": result.passed, "issue_count": len(result.issues)},
        error_code=error_code,
        started_at=started,
    )
    return trace_error or update

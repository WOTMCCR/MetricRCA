"""write_memory node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import fail, start_timer, trace
from metric_rca.observability.trace import TraceWriteError


def write_memory(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    error_code = state.get("error_code")
    update: dict[str, Any] = {}
    if getattr(dependencies.settings, "memory_enabled", False) and error_code is None:
        repo = getattr(dependencies, "memory_repo", None)
        if repo is None:
            error_code = "MEMORY_WRITE_FAILED"
            update.update(fail(error_code))
        else:
            try:
                repo.write({"run_id": state["run_id"], "status": state.get("status"), "report": state.get("report")})
            except RuntimeError:
                error_code = "MEMORY_WRITE_FAILED"
                update.update(fail(error_code))
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="write_memory",
        action="write_memory",
        input_summary={"status": state.get("status")},
        output_summary={"error_code": error_code, "memory_enabled": getattr(dependencies.settings, "memory_enabled", False)},
        error_code=error_code,
        started_at=started,
    )
    if trace_error:
        return trace_error
    if getattr(dependencies, "trace_writer", None) is not None:
        final_status = update.get("status") or state.get("status") or "failed"
        if final_status not in {"succeeded", "no_anomaly", "failed"}:
            final_status = "failed" if error_code else "succeeded"
        try:
            dependencies.trace_writer.finish_run(
                run_id=state["run_id"],
                status=final_status,
                error_code=error_code,
            )
        except TraceWriteError as exc:
            return fail(exc.code)
    return update

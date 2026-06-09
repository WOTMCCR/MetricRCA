"""write_memory node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import dump_model, fail, start_timer, trace
from metric_rca.observability.trace import TraceWriteError


def write_memory(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    run_error_code = state.get("error_code")
    trace_error_code = run_error_code
    update: dict[str, Any] = {}
    if getattr(dependencies.settings, "memory_enabled", False) and run_error_code is None:
        repo = getattr(dependencies, "memory_repo", None)
        if repo is None:
            trace_error_code = "MEMORY_WRITE_FAILED"
            if getattr(dependencies.settings, "memory_required", False):
                run_error_code = "MEMORY_WRITE_FAILED"
                update.update(fail(run_error_code))
        else:
            try:
                for record in _memory_records(state):
                    repo.write(record)
            except RuntimeError:
                trace_error_code = "MEMORY_WRITE_FAILED"
                if getattr(dependencies.settings, "memory_required", False):
                    run_error_code = "MEMORY_WRITE_FAILED"
                    update.update(fail(run_error_code))
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="write_memory",
        action="write_memory",
        input_summary={"status": state.get("status")},
        output_summary={
            "error_code": trace_error_code,
            "memory_enabled": getattr(dependencies.settings, "memory_enabled", False),
        },
        error_code=trace_error_code,
        started_at=started,
    )
    if trace_error:
        return trace_error
    if getattr(dependencies, "trace_writer", None) is not None:
        final_status = update.get("status") or state.get("status") or "failed"
        if final_status not in {"succeeded", "no_anomaly", "failed"}:
            final_status = "failed" if run_error_code else "succeeded"
        try:
            dependencies.trace_writer.finish_run(
                run_id=state["run_id"],
                status=final_status,
                error_code=run_error_code,
            )
        except TraceWriteError as exc:
            return fail(exc.code)
    return update


def _memory_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    if state.get("status") == "no_anomaly":
        return []
    reflection = state.get("reflection")
    if state.get("status") != "succeeded" or not getattr(reflection, "passed", False):
        return []
    metric_id = state.get("metric_id")
    if not metric_id:
        return []
    candidates = [dump_model(item) for item in state.get("candidates", [])]
    if candidates:
        candidate = candidates[0]
        dimension = candidate.get("dimension") or (state.get("parsed_spec") or {}).get("dimension")
        if not dimension:
            return []
        return [
            {
                "layer": "case",
                "mem_key": f"{metric_id}|{dimension}",
                "payload": {
                    "dimension": dimension,
                    "root_cause_type": candidate.get("root_cause_type"),
                    "element": candidate.get("element"),
                    "run_id": state.get("run_id"),
                },
                "confidence": min(1.0, float(candidate.get("eng_confidence") or 0.8)),
                "source": "reflection_verified",
                "ttl_days": 30,
            }
        ]
    return []

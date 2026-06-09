"""generate_report node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import dump_model, fail, start_timer, trace


def generate_report(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    if state.get("status") == "no_anomaly":
        report = {
            "status": "no_anomaly",
            "metric_id": state.get("metric_id"),
            "target_date": str(state.get("target_date")),
            "evidence_ids": [getattr(item, "evidence_id", None) for item in state.get("evidences", [])],
        }
        final_status = "no_anomaly"
    else:
        reflection = state.get("reflection")
        if reflection is None or not getattr(reflection, "passed", False):
            error_code = state.get("error_code") or "REFLECTION_REPAIR_FAILED"
            update = fail(str(error_code))
            return trace(
                dependencies=dependencies,
                state=state,
                node="generate_report",
                action="generate_report",
                input_summary={"status": state.get("status")},
                output_summary={"error_code": error_code},
                error_code=str(error_code),
                started_at=started,
            ) or update
        candidates = state.get("candidates", [])
        top = dump_model(candidates[0]) if candidates else None
        report = {
            "status": "succeeded",
            "metric_id": state.get("metric_id"),
            "target_date": str(state.get("target_date")),
            "top_candidate": top,
            "evidence_ids": top.get("evidence_ids", []) if top else [],
        }
        final_status = "succeeded"
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="generate_report",
        action="generate_report",
        input_summary={"status": state.get("status")},
        output_summary={"status": report["status"]},
        started_at=started,
    )
    return trace_error or {"report": report, "status": final_status}

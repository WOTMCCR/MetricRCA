"""generate_report node."""

from __future__ import annotations

import math
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
            "numeric_claims": _candidate_numeric_claims(top),
        }
        if not _final_report_numbers_are_traceable(report, state=state, dependencies=dependencies):
            error_code = "REFLECTION_REPAIR_FAILED"
            update = fail(error_code)
            return trace(
                dependencies=dependencies,
                state=state,
                node="generate_report",
                action="generate_report",
                input_summary={"status": state.get("status")},
                output_summary={"error_code": error_code},
                error_code=error_code,
                started_at=started,
            ) or update
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


def _candidate_numeric_claims(candidate: dict[str, Any] | None) -> list[dict[str, Any]]:
    if candidate is None:
        return []
    value = candidate.get("contribution_pct")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return []
    evidence_id = _evidence_id_for_alias(candidate.get("evidence_ids", []), "E4")
    if evidence_id is None:
        return []
    return [{"name": "contribution_pct", "value": float(value), "evidence_id": evidence_id}]


def _final_report_numbers_are_traceable(report: dict[str, Any], *, state: dict[str, Any], dependencies: Any) -> bool:
    repository = getattr(dependencies, "repository", None)
    if repository is None or not hasattr(repository, "get_evidence"):
        return False
    try:
        for claim in report.get("numeric_claims", []):
            evidence_id = claim.get("evidence_id")
            if not evidence_id:
                return False
            row = repository.get_evidence(run_id=state["run_id"], evidence_id=str(evidence_id))
            if row is None:
                return False
            rounded = {_round_number(value) for value in _numbers(row.get("result_summary") or {})}
            if _round_number(claim["value"]) not in rounded:
                return False
    except RuntimeError:
        return False
    return True


def _evidence_id_for_alias(evidence_ids: list[str], alias: str) -> str | None:
    suffix = f":{alias}"
    for evidence_id in evidence_ids:
        if str(evidence_id).endswith(suffix):
            return str(evidence_id)
    return None


def _numbers(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return [float(value)]
    if isinstance(value, dict):
        nums: list[float] = []
        for item in value.values():
            nums.extend(_numbers(item))
        return nums
    if isinstance(value, list):
        nums: list[float] = []
        for item in value:
            nums.extend(_numbers(item))
        return nums
    return []


def _round_number(value: float) -> float:
    return round(float(value), 6)

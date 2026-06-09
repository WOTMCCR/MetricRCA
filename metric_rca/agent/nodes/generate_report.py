"""generate_report node.

P3B boundary:
- Report generation is a mechanical projection of reflection-verified artifacts.
- It must not introduce new numeric claims or causal claims after reflection.
- Numeric claims must be traceable to persisted Evidence, not only in-memory state.
"""

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
            "evidence_ids": _state_evidence_ids(state),
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
        if not candidates:
            error_code = "ATTRIBUTION_COVERAGE_LOW"
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

        candidate = dump_model(candidates[0])
        verified = _verified_candidate_report(
            candidate=candidate,
            state=state,
            dependencies=dependencies,
        )
        if verified is None:
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

        report = {
            "status": "succeeded",
            "metric_id": state.get("metric_id"),
            "target_date": str(state.get("target_date")),
            "top_candidate": verified["top_candidate"],
            "evidence_ids": verified["evidence_ids"],
            "numeric_claims": verified["numeric_claims"],
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


def _verified_candidate_report(
    *,
    candidate: dict[str, Any],
    state: dict[str, Any],
    dependencies: Any,
) -> dict[str, Any] | None:
    evidence_ids = [str(value) for value in candidate.get("evidence_ids", [])]
    e4_id = _evidence_id_for_alias(evidence_ids, "E4")
    if e4_id is None:
        return None

    repository = getattr(dependencies, "repository", None)
    if repository is None or not hasattr(repository, "get_evidence"):
        return None

    try:
        persisted_e4 = repository.get_evidence(run_id=state["run_id"], evidence_id=e4_id)
    except RuntimeError:
        return None

    if persisted_e4 is None:
        return None
    if persisted_e4.get("run_id") != state.get("run_id"):
        return None
    if persisted_e4.get("guard_status") != "passed":
        return None

    summary = persisted_e4.get("result_summary") or {}
    if not isinstance(summary, dict):
        return None

    selected_candidate = summary.get("selected_candidate")
    if not isinstance(selected_candidate, dict):
        return None

    if not _candidate_projection_matches(candidate, selected_candidate):
        return None

    contribution_pct = selected_candidate.get("contribution_pct")
    if isinstance(contribution_pct, bool) or not isinstance(contribution_pct, int | float):
        return None

    return {
        "top_candidate": _safe_candidate_projection(selected_candidate),
        "evidence_ids": [str(value) for value in selected_candidate.get("evidence_ids", [])],
        "numeric_claims": [
            {
                "name": "contribution_pct",
                "value": float(contribution_pct),
                "evidence_id": e4_id,
            }
        ],
    }


def _candidate_projection_matches(candidate: dict[str, Any], selected: dict[str, Any]) -> bool:
    return (
        _safe_candidate_projection(candidate) == _safe_candidate_projection(selected)
        and _numeric_equal(candidate.get("contribution_pct"), selected.get("contribution_pct"))
        and _numeric_equal(candidate.get("signal_severity"), selected.get("signal_severity"))
        and _numeric_equal(candidate.get("evidence_support"), selected.get("evidence_support"))
        and _numeric_equal(candidate.get("reflection_factor", 1.0), selected.get("reflection_factor", 1.0))
        and _numeric_equal(candidate.get("eng_confidence"), selected.get("eng_confidence"))
    )


def _safe_candidate_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return only non-numeric user-facing candidate identity fields."""
    return {
        "root_cause_type": candidate.get("root_cause_type"),
        "dimension": candidate.get("dimension"),
        "element": candidate.get("element"),
        "verdict": candidate.get("verdict"),
    }


def _numeric_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if not isinstance(left, int | float) or not isinstance(right, int | float):
        return False
    return round(float(left), 6) == round(float(right), 6)


def _evidence_id_for_alias(evidence_ids: list[str], alias: str) -> str | None:
    suffix = f":{alias}"
    for evidence_id in evidence_ids:
        if str(evidence_id).endswith(suffix):
            return str(evidence_id)
    return None


def _state_evidence_ids(state: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in state.get("evidences", []) or []:
        evidence_id = getattr(item, "evidence_id", None)
        if evidence_id is None and isinstance(item, dict):
            evidence_id = item.get("evidence_id")
        if evidence_id is not None:
            ids.append(str(evidence_id))
    return ids

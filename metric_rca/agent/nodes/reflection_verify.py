"""reflection_verify node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import code_from_message, fail, start_timer, trace
from metric_rca.agent.reflection import verify_reflection


def _issue_summary(issue: Any) -> dict[str, Any]:
    suggested_action = getattr(issue, "suggested_action", None)
    return {
        "check": getattr(issue, "check", None),
        "severity": getattr(issue, "severity", None),
        "message": getattr(issue, "message", None),
        "suggested_action": suggested_action.model_dump(mode="json")
        if hasattr(suggested_action, "model_dump")
        else suggested_action,
    }


def reflection_verify(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    try:
        persisted_evidence_by_id = _persisted_evidence_by_id(state, dependencies=dependencies)
    except RuntimeError as exc:
        error_code = code_from_message(str(exc), default="SYSTEM_TABLE_READ_FAILED")
        update = fail(error_code)
        trace_error = trace(
            dependencies=dependencies,
            state=state,
            node="reflection_verify",
            action="reflection_verify",
            input_summary={"candidate_count": len(state.get("candidates", []))},
            output_summary={"error_code": error_code},
            error_code=error_code,
            started_at=started,
        )
        return trace_error or update
    result = verify_reflection(
        state,
        max_repair=getattr(dependencies.settings, "max_repair", 1),
        persisted_evidence_by_id=persisted_evidence_by_id,
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
        output_summary={
            "passed": result.passed,
            "issue_count": len(result.issues),
            "issues": [_issue_summary(issue) for issue in result.issues],
            "repair_pending": update.get("repair_pending", False),
            "repair_count": update.get("repair_count", state.get("repair_count")),
        },
        error_code=error_code,
        started_at=started,
    )
    return trace_error or update


def _persisted_evidence_by_id(state: dict[str, Any], *, dependencies: Any) -> dict[str, dict[str, Any] | None]:
    repository = getattr(dependencies, "repository", None)
    if repository is None or not hasattr(repository, "get_evidence"):
        raise RuntimeError("SYSTEM_TABLE_READ_FAILED")
    evidence_ids = _candidate_evidence_ids(state)
    if state.get("status") == "no_anomaly":
        evidence_ids.update(_state_evidence_ids(state))
    persisted: dict[str, dict[str, Any] | None] = {}
    for evidence_id in sorted(evidence_ids):
        persisted[evidence_id] = repository.get_evidence(
            run_id=state["run_id"],
            evidence_id=evidence_id,
        )
    return persisted


def _candidate_evidence_ids(state: dict[str, Any]) -> set[str]:
    evidence_ids: set[str] = set()
    for candidate in state.get("candidates", []) or []:
        ids = getattr(candidate, "evidence_ids", None)
        if ids is None and isinstance(candidate, dict):
            ids = candidate.get("evidence_ids")
        for evidence_id in ids or []:
            evidence_ids.add(str(evidence_id))
    return evidence_ids


def _state_evidence_ids(state: dict[str, Any]) -> set[str]:
    evidence_ids: set[str] = set()
    for evidence in state.get("evidences", []) or []:
        evidence_id = getattr(evidence, "evidence_id", None)
        if evidence_id is None and isinstance(evidence, dict):
            evidence_id = evidence.get("evidence_id")
        if evidence_id is not None:
            evidence_ids.add(str(evidence_id))
    return evidence_ids

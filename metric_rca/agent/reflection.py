"""Deterministic P3A reflection verifier."""

from __future__ import annotations

from typing import Any

from metric_rca.domain.models import ReflectionIssue, ReflectionResult, RootCauseCandidate


def verify_reflection(state: dict[str, Any], *, max_repair: int) -> ReflectionResult:
    issues: list[ReflectionIssue] = []
    status = state.get("status")
    if status == "no_anomaly":
        if state.get("candidates"):
            issues.append(_issue("no_anomaly_has_candidate", "no_anomaly run has candidates"))
        return ReflectionResult(
            passed=not issues,
            issues=issues,
            repaired=False,
            repair_count=int(state.get("repair_count") or 0),
        )

    evidence_by_id = {
        evidence.evidence_id: evidence
        for evidence in [_as_evidence(item) for item in state.get("evidences", [])]
    }
    candidates = [_as_candidate(item) for item in state.get("candidates", [])]
    if not candidates:
        issues.append(_issue("evidence_coverage", "no root cause candidates"))
    for candidate in candidates:
        if not candidate.evidence_ids:
            issues.append(_issue("evidence_coverage", "candidate has no evidence ids"))
        for evidence_id in candidate.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                issues.append(_issue("current_run_evidence", "candidate evidence is absent from current state"))
            elif not evidence.evidence_id.startswith(f"{state.get('run_id')}:"):
                issues.append(_issue("current_run_evidence", "candidate evidence is not current-run scoped"))
            elif evidence.guard_status != "passed":
                issues.append(_issue("sql_guard_status", "candidate evidence guard did not pass"))

    repair_count = int(state.get("repair_count") or 0)
    if issues and repair_count >= max_repair:
        return ReflectionResult(passed=False, issues=issues, repaired=False, repair_count=repair_count)
    return ReflectionResult(passed=not issues, issues=issues, repaired=False, repair_count=repair_count)


def _issue(check: str, message: str) -> ReflectionIssue:
    return ReflectionIssue(check=check, severity="error", by="rule", message=message)


def _as_candidate(item: Any) -> RootCauseCandidate:
    if isinstance(item, RootCauseCandidate):
        return item
    return RootCauseCandidate.model_validate(item)


def _as_evidence(item: Any):
    from metric_rca.domain.models import Evidence

    if isinstance(item, Evidence):
        return item
    return Evidence.model_validate(item)

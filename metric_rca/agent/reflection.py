"""Deterministic reflection verifier for evidence, traceability, and repair."""

from __future__ import annotations

from datetime import date
import math
from typing import Any

from metric_rca.agent.tools.registry import select_signal_type
from metric_rca.domain.models import AgentAction, Evidence, ReflectionIssue, ReflectionResult, RootCauseCandidate


REQUIRED_EVIDENCE_ALIASES = ("E1", "E2", "E3", "E4")
ATTRIBUTION_COVERAGE_THRESHOLD = 0.60


def verify_reflection(
    state: dict[str, Any],
    *,
    max_repair: int,
    persisted_evidence_by_id: dict[str, dict[str, Any] | None] | None = None,
) -> ReflectionResult:
    issues: list[ReflectionIssue] = []
    status = state.get("status")
    repair_count = int(state.get("repair_count") or 0)
    if status == "no_anomaly":
        aliases = _aliases(state)
        if aliases != {"E1"}:
            issues.append(_issue("no_anomaly_evidence_scope", "no_anomaly run must bind exactly E1"))
        for evidence in [_as_evidence(item) for item in state.get("evidences", [])]:
            if not evidence.evidence_id.startswith(f"{state.get('run_id')}:"):
                issues.append(_issue("current_run_evidence", "no_anomaly evidence is not current-run scoped"))
            elif evidence.guard_status != "passed":
                issues.append(_issue("sql_guard_status", "no_anomaly evidence guard did not pass"))
            elif not _persisted_evidence_matches(
                state=state,
                evidence=evidence,
                persisted_evidence_by_id=persisted_evidence_by_id,
            ):
                issues.append(_issue("persisted_evidence", "no_anomaly evidence is not persisted guard-passed evidence"))
        if state.get("candidates"):
            issues.append(_issue("no_anomaly_has_candidate", "no_anomaly run has candidates"))
        if state.get("operation_tasks") or state.get("operation_task_created"):
            issues.append(_issue("no_anomaly_task_behavior", "no_anomaly run cannot create operation_task"))
        if _has_confirmed_root_cause_report(state.get("report")):
            issues.append(_issue("no_anomaly_report_behavior", "no_anomaly report cannot contain confirmed root cause"))
        trace_nodes = set(state.get("trace_nodes") or [])
        if {"attribute_rank", "create_tasks"} & trace_nodes:
            issues.append(_issue("no_anomaly_downstream_trace", "no_anomaly run cannot visit downstream RCA nodes"))
        return ReflectionResult(
            passed=not issues,
            issues=issues,
            repaired=False,
            repair_count=repair_count,
        )

    evidence_by_id = {
        evidence.evidence_id: evidence
        for evidence in [_as_evidence(item) for item in state.get("evidences", [])]
    }
    candidates = [_as_candidate(item) for item in state.get("candidates", [])]
    if not candidates:
        issues.append(_issue("evidence_coverage", "no root cause candidates"))
    for candidate in candidates:
        missing_aliases = _missing_required_aliases(state, candidate.evidence_ids)
        if candidate.verdict in {"confirmed", "likely"} and missing_aliases:
            issues.append(
                _issue(
                    "required_evidence_present",
                    "confirmed/likely candidate must bind current-run E1-E4",
                    suggested_action=_suggested_action_for_missing_aliases(
                        state=state,
                        candidate=candidate,
                        missing_aliases=missing_aliases,
                    ),
                )
            )
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
            elif not _persisted_evidence_matches(
                state=state,
                evidence=evidence,
                persisted_evidence_by_id=persisted_evidence_by_id,
            ):
                issues.append(_issue("persisted_evidence", "candidate evidence is not persisted guard-passed evidence"))
        if candidate is candidates[0] and candidate.contribution_pct < ATTRIBUTION_COVERAGE_THRESHOLD:
            issues.append(_issue("ATTRIBUTION_COVERAGE_LOW", "top candidate attribution coverage is below threshold"))

    for evidence in evidence_by_id.values():
        if not evidence.evidence_id.startswith(f"{state.get('run_id')}:"):
            issues.append(_issue("current_run_evidence", "state evidence is not current-run scoped"))
        if evidence.guard_status != "passed":
            issues.append(_issue("sql_guard_status", "evidence guard did not pass"))
        if not _time_range_matches(evidence, state.get("target_date")):
            issues.append(_issue("time_range_consistency", "evidence time_range does not match target_date"))
        if not _metric_matches(evidence, state.get("metric_id")):
            issues.append(_issue("metric_consistency", "evidence metric_id does not match run metric"))

    if not _report_numbers_are_traceable(state.get("report"), evidence_by_id.values()):
        issues.append(_issue("numeric_traceability", "report numeric claim is not traceable to evidence"))
    if _has_unsupported_causal_language(state, candidates):
        issues.append(_issue("causal_language", "confirmed causal language requires complete current-run evidence"))
    if repair_count > max_repair:
        issues.append(_issue("repair_limit", "repair_count exceeds max_repair"))

    if issues and repair_count >= max_repair:
        return ReflectionResult(passed=False, issues=issues, repaired=False, repair_count=repair_count)
    return ReflectionResult(
        passed=not issues,
        issues=issues,
        repaired=not issues and repair_count > 0,
        repair_count=repair_count,
    )


def _issue(check: str, message: str, *, suggested_action: AgentAction | None = None) -> ReflectionIssue:
    return ReflectionIssue(
        check=check,
        severity="error",
        by="rule",
        message=message,
        suggested_action=suggested_action,
    )


def _persisted_evidence_matches(
    *,
    state: dict[str, Any],
    evidence: Evidence,
    persisted_evidence_by_id: dict[str, dict[str, Any] | None] | None,
) -> bool:
    if persisted_evidence_by_id is None:
        return True
    persisted = persisted_evidence_by_id.get(evidence.evidence_id)
    if persisted is None:
        return False
    return (
        persisted.get("run_id") == state.get("run_id")
        and persisted.get("guard_status") == "passed"
        and persisted.get("sql_hash") == evidence.sql_hash
    )


def _missing_required_aliases(state: dict[str, Any], evidence_ids: list[str]) -> list[str]:
    aliases = set()
    prefix = f"{state.get('run_id')}:"
    for evidence_id in evidence_ids:
        if evidence_id.startswith(prefix):
            aliases.add(evidence_id.removeprefix(prefix))
    return [alias for alias in REQUIRED_EVIDENCE_ALIASES if alias not in aliases]


def _suggested_action_for_missing_aliases(
    *,
    state: dict[str, Any],
    candidate: RootCauseCandidate,
    missing_aliases: list[str],
) -> AgentAction | None:
    current_ids = _current_evidence_ids(state)
    state_aliases = _aliases(state)
    target_date = state.get("target_date")
    if "E4" in missing_aliases and {"E1", "E2", "E3"}.issubset(state_aliases):
        if not candidate.dimension or not candidate.element:
            return None
        return AgentAction(
            action="calculate_contribution",
            args={
                "run_id": state["run_id"],
                "metric_id": state["metric_id"],
                "target_date": target_date,
                "dimension": candidate.dimension,
                "element": candidate.element,
                "evidence_ids": current_ids,
                "filters": dict((state.get("parsed_spec") or {}).get("filters") or {}),
            },
            rationale="reflection repair requires contribution evidence",
        )
    if "E3" in missing_aliases and {"E1", "E2"}.issubset(state_aliases):
        if not candidate.dimension or not candidate.element:
            return None
        try:
            signal_type = select_signal_type(
                metric_id=str(state.get("metric_id")),
                dimension=candidate.dimension,
                root_cause_type=candidate.root_cause_type,
            )
        except ValueError:
            return None
        return AgentAction(
            action="fetch_related_signal",
            args={
                "run_id": state["run_id"],
                "metric_id": state["metric_id"],
                "target_date": target_date,
                "signal_type": signal_type,
                "dimension": candidate.dimension,
                "element": candidate.element,
                "evidence_ids": current_ids,
            },
            rationale="reflection repair requires related signal evidence",
        )
    return None


def _aliases(state: dict[str, Any]) -> set[str]:
    prefix = f"{state.get('run_id')}:"
    return {
        evidence.evidence_id.removeprefix(prefix)
        for evidence in [_as_evidence(item) for item in state.get("evidences", [])]
        if evidence.evidence_id.startswith(prefix)
    }


def _current_evidence_ids(state: dict[str, Any]) -> list[str]:
    return [
        evidence.evidence_id
        for evidence in [_as_evidence(item) for item in state.get("evidences", [])]
        if evidence.evidence_id.startswith(f"{state.get('run_id')}:")
    ]


def _time_range_matches(evidence: Evidence, target_date: Any) -> bool:
    expected = _as_date(target_date)
    return (
        evidence.query_spec.time_range.start_date == expected
        and evidence.query_spec.time_range.end_date == expected
    )


def _metric_matches(evidence: Evidence, metric_id: Any) -> bool:
    alias = evidence.evidence_id.split(":", maxsplit=1)[1] if ":" in evidence.evidence_id else ""
    if alias == "E3" and evidence.result_summary.get("signal_metric_id"):
        return True
    return evidence.query_spec.metric_id == metric_id


def _report_numbers_are_traceable(report: Any, evidences: Any) -> bool:
    if not report:
        return True
    evidence_numbers = [_round_number(value) for evidence in evidences for value in _numbers(evidence.result_summary)]
    claims = _numeric_claims(report)
    return all(_round_number(claim) in evidence_numbers for claim in claims)


def _numeric_claims(report: Any) -> list[float]:
    if not isinstance(report, dict):
        return []
    if "numeric_claims" in report:
        claims: list[float] = []
        for item in report["numeric_claims"]:
            if isinstance(item, dict) and isinstance(item.get("value"), int | float):
                claims.append(float(item["value"]))
        return claims
    return [float(value) for value in _numbers(report)]


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


def _has_unsupported_causal_language(state: dict[str, Any], candidates: list[RootCauseCandidate]) -> bool:
    report = state.get("report") or {}
    text = " ".join(str(value) for value in _strings(report))
    if not any(token in text.lower() for token in ["导致", "caused by", "because of"]):
        return False
    return not any(
        candidate.verdict == "confirmed" and not _missing_required_aliases(state, candidate.evidence_ids)
        for candidate in candidates
    )


def _has_confirmed_root_cause_report(report: Any) -> bool:
    if not isinstance(report, dict):
        return False
    if report.get("status") == "succeeded" and (report.get("top_candidate") or report.get("root_cause")):
        return True
    if report.get("verdict") == "confirmed":
        return True
    top_candidate = report.get("top_candidate")
    return isinstance(top_candidate, dict) and top_candidate.get("verdict") == "confirmed"


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_strings(item))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_strings(item))
        return out
    return []


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _as_candidate(item: Any) -> RootCauseCandidate:
    if isinstance(item, RootCauseCandidate):
        return item
    return RootCauseCandidate.model_validate(item)


def _as_evidence(item: Any):
    from metric_rca.domain.models import Evidence

    if isinstance(item, Evidence):
        return item
    return Evidence.model_validate(item)

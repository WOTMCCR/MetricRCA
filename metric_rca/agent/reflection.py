"""Deterministic reflection verifier for evidence, traceability, and repair."""

from __future__ import annotations

from datetime import date
import json
from json import JSONDecodeError
import math
from typing import Any

from metric_rca.agent.evidence_aliases import E2_ALIAS_BY_DIMENSION, e2_alias_for_e3_id
from metric_rca.agent.tools.registry import select_signal_type
from metric_rca.domain.enums import RootCauseType
from metric_rca.domain.models import AgentAction, Evidence, ReflectionIssue, ReflectionResult, RootCauseCandidate


REQUIRED_EVIDENCE_ALIASES = ("E1", "E2", "E3", "E4", "E_rank")
ATTRIBUTION_COVERAGE_THRESHOLD = 0.50


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
        if {
            "attribute_rank",
            "create_tasks",
            "drilldown_dimension",
            "fetch_related_signal",
            "rank_root_causes",
            "calculate_contribution",
        } & trace_nodes:
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
        issues.append(
            _issue(
                "evidence_coverage",
                "no root cause candidates",
                suggested_action=_suggested_action_for_no_candidates(state),
            )
        )
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
            issues.append(
                _issue(
                    "ATTRIBUTION_COVERAGE_LOW",
                    "top candidate attribution coverage is below threshold",
                    suggested_action=_suggested_rank_action_for_low_coverage(state),
                )
            )
        if candidate is candidates[0] and not _e3_signal_matches_candidate(
            state=state,
            candidate=candidate,
            evidence_by_id=evidence_by_id,
        ):
            issues.append(_issue("signal_consistency", "E3 signal does not match selected candidate"))
        if candidate is candidates[0] and not _top_candidate_matches_persisted_e4(
            state=state,
            candidate=candidate,
            persisted_evidence_by_id=persisted_evidence_by_id,
        ):
            issues.append(
                _issue(
                    "candidate_traceability",
                    "top candidate does not match persisted E4 selected_candidate",
                )
            )

    for evidence in evidence_by_id.values():
        if not evidence.evidence_id.startswith(f"{state.get('run_id')}:"):
            issues.append(_issue("current_run_evidence", "state evidence is not current-run scoped"))
        if evidence.guard_status != "passed":
            issues.append(_issue("sql_guard_status", "evidence guard did not pass"))
        if not _time_range_matches(evidence, state.get("target_date")):
            issues.append(_issue("time_range_consistency", "evidence time_range does not match target_date"))
        if not _metric_matches(evidence, state.get("metric_id")):
            issues.append(_issue("metric_consistency", "evidence metric_id does not match run metric"))

    if not _report_numbers_are_traceable(
        state.get("report"),
        _traceability_summaries(
            evidences=evidence_by_id.values(),
            persisted_evidence_by_id=persisted_evidence_by_id,
        ),
    ):
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
    # P3B verifier must not silently degrade to state-only evidence.
    if persisted_evidence_by_id is None:
        return False

    persisted = persisted_evidence_by_id.get(evidence.evidence_id)
    if persisted is None:
        return False

    return (
        persisted.get("run_id") == state.get("run_id")
        and persisted.get("guard_status") == "passed"
        and persisted.get("sql_hash") == evidence.sql_hash
        and _canonical(persisted.get("query_spec")) == _canonical(evidence.query_spec)
        and _canonical(persisted.get("result_summary")) == _canonical(evidence.result_summary)
    )


def _e3_signal_matches_candidate(
    *,
    state: dict[str, Any],
    candidate: RootCauseCandidate,
    evidence_by_id: dict[str, Evidence],
) -> bool:
    if _aov_drop_is_proven_by_e4_decomposition(state=state, candidate=candidate, evidence_by_id=evidence_by_id):
        return True
    e3 = _candidate_e3_evidence(state=state, candidate=candidate, evidence_by_id=evidence_by_id)
    if e3 is None:
        return True
    summary = e3.result_summary
    if not summary:
        return False
    try:
        expected_signal_type = select_signal_type(
            metric_id=str(state.get("metric_id")),
            dimension=candidate.dimension,
            root_cause_type=candidate.root_cause_type,
        )
    except ValueError:
        return False
    return (
        summary.get("signal_type") == expected_signal_type
        and summary.get("dimension") == candidate.dimension
        and str(summary.get("element")) == str(candidate.element)
    )


def _aov_drop_is_proven_by_e4_decomposition(
    *,
    state: dict[str, Any],
    candidate: RootCauseCandidate,
    evidence_by_id: dict[str, Evidence],
) -> bool:
    if candidate.root_cause_type != RootCauseType.AOV_DROP.value:
        return False
    e4 = evidence_by_id.get(f"{state.get('run_id')}:E4")
    if e4 is None or f"{state.get('run_id')}:E4" not in candidate.evidence_ids:
        return False
    e3 = _candidate_e3_evidence(state=state, candidate=candidate, evidence_by_id=evidence_by_id)
    if e3 is None:
        return False
    e3_summary = e3.result_summary or {}
    if e3_summary.get("dimension") != candidate.dimension or str(e3_summary.get("element")) != str(candidate.element):
        return False
    summary = e4.result_summary or {}
    decomposition = summary.get("decomposition")
    if not isinstance(decomposition, dict):
        return False
    return str(decomposition.get("largest_drop_factor")) in {"aov", RootCauseType.AOV_DROP.value}


def _candidate_e3_evidence(
    *,
    state: dict[str, Any],
    candidate: RootCauseCandidate,
    evidence_by_id: dict[str, Evidence],
) -> Evidence | None:
    prefix = f"{state.get('run_id')}:"
    for evidence_id in candidate.evidence_ids:
        if not evidence_id.startswith(prefix):
            continue
        alias = evidence_id.removeprefix(prefix)
        if alias == "E3" or alias.startswith("E3_"):
            return evidence_by_id.get(evidence_id)
    return None


def _top_candidate_matches_persisted_e4(
    *,
    state: dict[str, Any],
    candidate: RootCauseCandidate,
    persisted_evidence_by_id: dict[str, dict[str, Any] | None] | None,
) -> bool:
    e4_id = f"{state.get('run_id')}:E4"

    # Missing E4 is already handled by required_evidence_present.
    if e4_id not in candidate.evidence_ids:
        return True

    if persisted_evidence_by_id is None:
        return False

    persisted_e4 = persisted_evidence_by_id.get(e4_id)
    if persisted_e4 is None:
        return False

    summary = persisted_e4.get("result_summary") or {}
    if not isinstance(summary, dict):
        return False

    selected = summary.get("selected_candidate")
    if not isinstance(selected, dict):
        return False

    selected_candidate = RootCauseCandidate.model_validate(selected)
    return _canonical(candidate) == _canonical(selected_candidate)


def _missing_required_aliases(state: dict[str, Any], evidence_ids: list[str]) -> list[str]:
    aliases = set()
    prefix = f"{state.get('run_id')}:"
    for evidence_id in evidence_ids:
        if evidence_id.startswith(prefix):
            aliases.add(evidence_id.removeprefix(prefix))
    return [
        alias
        for alias in REQUIRED_EVIDENCE_ALIASES
        if not any(actual == alias or actual.startswith(f"{alias}_") for actual in aliases)
    ]


def _suggested_action_for_missing_aliases(
    *,
    state: dict[str, Any],
    candidate: RootCauseCandidate,
    missing_aliases: list[str],
) -> AgentAction | None:
    current_ids = _current_evidence_ids(state)
    state_aliases = _aliases(state)
    target_date = state.get("target_date")
    if "E_rank" in missing_aliases and _has_aliases(state_aliases, {"E1", "E2", "E3", "E4"}):
        return AgentAction(
            action="rank_root_causes",
            args={
                "metric_id": state["metric_id"],
                "target_date": target_date,
            },
            rationale="reflection repair requires ranked root-cause evidence",
        )
    if "E4" in missing_aliases and _has_aliases(state_aliases, {"E1", "E2", "E3"}):
        if not candidate.dimension or not candidate.element:
            return None
        return AgentAction(
            action="calculate_contribution",
            args={
                "metric_id": state["metric_id"],
                "target_date": target_date,
                "dimension": candidate.dimension,
                "element": candidate.element,
                "evidence_ids": current_ids,
                "filters": _state_filters(state),
            },
            rationale="reflection repair requires contribution evidence",
        )
    if "E3" in missing_aliases and _has_aliases(state_aliases, {"E1", "E2"}):
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


def _suggested_action_for_no_candidates(state: dict[str, Any]) -> AgentAction | None:
    aliases = _aliases(state)
    if not _has_aliases(aliases, {"E1"}):
        return AgentAction(
            action="detect_anomaly",
            args={
                "metric_id": state["metric_id"],
                "target_date": state.get("target_date"),
                "filters": _state_filters(state),
            },
            rationale="reflection repair requires anomaly evidence before RCA",
        )

    e3 = _first_evidence_for_alias(state, "E3")
    if e3 is not None and _has_aliases(aliases, {"E1", "E2"}):
        summary = e3.result_summary or {}
        dimension = summary.get("dimension")
        element = summary.get("element")
        if dimension is None or element is None:
            return None
        return AgentAction(
            action="calculate_contribution",
            args={
                "metric_id": state["metric_id"],
                "target_date": state.get("target_date"),
                "dimension": str(dimension),
                "element": str(element),
                "evidence_ids": _contribution_repair_chain(state, e3.evidence_id),
                "filters": _state_filters(state),
            },
            rationale="reflection repair requires E4 contribution evidence",
        )

    e1 = _first_evidence_for_alias(state, "E1")
    if e1 is None:
        return None
    for alias in [*E2_ALIAS_BY_DIMENSION.values(), "E2"]:
        e2 = _first_evidence_for_alias(state, alias)
        if e2 is None:
            continue
        summary = e2.result_summary or {}
        dimension = summary.get("dimension")
        element = _first_candidate_element(summary)
        if dimension is None or element is None:
            continue
        root_cause_type = _repair_root_cause_type(metric_id=str(state.get("metric_id")), dimension=str(dimension))
        try:
            signal_type = select_signal_type(
                metric_id=str(state.get("metric_id")),
                dimension=str(dimension),
                root_cause_type=root_cause_type,
            )
        except ValueError:
            continue
        return AgentAction(
            action="fetch_related_signal",
            args={
                "metric_id": state["metric_id"],
                "target_date": state.get("target_date"),
                "signal_type": signal_type,
                "dimension": str(dimension),
                "element": str(element),
                "evidence_ids": [e1.evidence_id, e2.evidence_id],
            },
            rationale="reflection repair requires related signal evidence before ranking",
        )
    return None


def _suggested_rank_action_for_low_coverage(state: dict[str, Any]) -> AgentAction | None:
    if not _has_aliases(_aliases(state), {"E1", "E2", "E3", "E4"}):
        return None
    return AgentAction(
        action="rank_root_causes",
        args={
            "metric_id": state["metric_id"],
            "target_date": state.get("target_date"),
        },
        rationale="low single-element coverage requires ranker-internal Adtributor over persisted drilldowns",
    )


def _aliases(state: dict[str, Any]) -> set[str]:
    prefix = f"{state.get('run_id')}:"
    return {
        evidence.evidence_id.removeprefix(prefix)
        for evidence in [_as_evidence(item) for item in state.get("evidences", [])]
        if evidence.evidence_id.startswith(prefix)
    }


def _has_aliases(actual_aliases: set[str], required_aliases: set[str]) -> bool:
    return all(
        any(actual == required or actual.startswith(f"{required}_") for actual in actual_aliases)
        for required in required_aliases
    )


def _current_evidence_ids(state: dict[str, Any]) -> list[str]:
    return [
        evidence.evidence_id
        for evidence in [_as_evidence(item) for item in state.get("evidences", [])]
        if evidence.evidence_id.startswith(f"{state.get('run_id')}:")
    ]


def _first_evidence_for_alias(state: dict[str, Any], alias: str) -> Evidence | None:
    prefix = f"{state.get('run_id')}:"
    for evidence in [_as_evidence(item) for item in state.get("evidences", [])]:
        if not evidence.evidence_id.startswith(prefix):
            continue
        actual_alias = evidence.evidence_id.removeprefix(prefix)
        if actual_alias == alias or actual_alias.startswith(f"{alias}_"):
            return evidence
    return None


def _first_candidate_element(summary: dict[str, Any]) -> str | None:
    candidates = summary.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0]
    if not isinstance(first, dict) or first.get("element") is None:
        return None
    return str(first["element"])


def _repair_root_cause_type(*, metric_id: str, dimension: str) -> str:
    if metric_id in {"refund_rate", "complaint_rate", "net_gmv"}:
        return RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value
    if metric_id == "pay_cvr" or dimension == "device":
        return RootCauseType.CONVERSION_DROP.value
    if dimension == "channel":
        return RootCauseType.CAMPAIGN_TRAFFIC_DROP.value
    return RootCauseType.STOCKOUT.value


def _contribution_repair_chain(state: dict[str, Any], e3_id: str) -> list[str]:
    ids: list[str] = []
    e1 = _first_evidence_for_alias(state, "E1")
    if e1 is not None:
        ids.append(e1.evidence_id)
    e2_alias = e2_alias_for_e3_id(e3_id, run_id=str(state.get("run_id")))
    e2 = _first_evidence_for_alias(state, e2_alias or "E2")
    if e2 is not None:
        ids.append(e2.evidence_id)
    ids.append(e3_id)
    return ids


def _state_filters(state: dict[str, Any]) -> dict[str, Any]:
    parsed_filters = (state.get("parsed_spec") or {}).get("filters") or {}
    if parsed_filters:
        return dict(parsed_filters)
    e1 = _first_evidence_for_alias(state, "E1")
    summary = e1.result_summary if e1 is not None else {}
    if not isinstance(summary, dict):
        return {}
    return dict(summary.get("filters") or {})


def _time_range_matches(evidence: Evidence, target_date: Any) -> bool:
    expected = _as_date(target_date)
    return (
        evidence.query_spec.time_range.start_date == expected
        and evidence.query_spec.time_range.end_date == expected
    )


def _metric_matches(evidence: Evidence, metric_id: Any) -> bool:
    alias = evidence.evidence_id.split(":", maxsplit=1)[1] if ":" in evidence.evidence_id else ""
    if (alias == "E3" or alias.startswith("E3_")) and evidence.result_summary.get("signal_metric_id"):
        return True
    return evidence.query_spec.metric_id == metric_id


def _report_numbers_are_traceable(report: Any, summaries: Any) -> bool:
    if not report:
        return True
    evidence_numbers = [_round_number(value) for summary in summaries for value in _numbers(summary)]
    claims = _numeric_claims(report)
    return all(_round_number(claim) in evidence_numbers for claim in claims)


def _traceability_summaries(
    *,
    evidences: Any,
    persisted_evidence_by_id: dict[str, dict[str, Any] | None] | None,
) -> list[dict[str, Any]]:
    # Numeric traceability must be based on persisted Evidence rows.
    # State-only result_summary is not an acceptable source for P3B.
    if persisted_evidence_by_id is None:
        return []

    summaries: list[dict[str, Any]] = []
    for evidence in evidences:
        row = persisted_evidence_by_id.get(evidence.evidence_id)
        if row is not None:
            summary = row.get("result_summary") or {}
            if isinstance(summary, dict):
                summaries.append(summary)
    return summaries


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


def _canonical(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _canonical(value.model_dump(mode="json"))
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        if not value.startswith(("{", "[")):
            return value
        try:
            return _canonical(json.loads(value))
        except JSONDecodeError:
            return value
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        return _round_number(value)
    return value


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

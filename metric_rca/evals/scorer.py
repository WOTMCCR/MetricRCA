"""Scoring over persisted RCA artifacts."""

from __future__ import annotations

from typing import Any, Callable

from metric_rca.evals.models import GroundTruth, PersistedArtifacts
from metric_rca.guardrails.sql_guard import guard_sql


def score_case(
    *,
    case_id: str,
    ground_truth: GroundTruth,
    artifacts: PersistedArtifacts,
) -> dict[str, Any]:
    agent_run = artifacts.agent_run or {}
    report = artifacts.report
    e4_summary = _evidence_summary(artifacts.evidences, "E4")
    selected_candidate = _selected_candidate(report=report, e4_summary=e4_summary)
    candidate_list = _candidate_list(e4_summary)

    intent_ok = agent_run.get("metric_id") == ground_truth.metric_id
    anomaly_ok = _anomaly_ok(agent_run=agent_run, ground_truth=ground_truth, artifacts=artifacts)
    top1_ok = _matches_ground_truth(selected_candidate, ground_truth)
    top3_ok = (
        top1_ok
        if not ground_truth.expected_anomaly
        else any(_matches_ground_truth(candidate, ground_truth) for candidate in candidate_list)
    )
    evidence_coverage = _evidence_coverage(selected_candidate, artifacts)
    sql_safe = _sql_safe(artifacts.sql_audit)
    reflection_repair_ok = _reflection_repair_ok(artifacts)
    report_traceable_ok = _report_traceable(report=report, evidences=artifacts.evidences)
    memory_pollution_ok = _memory_pollution_ok(selected_candidate)
    no_anomaly_task_ok = _no_anomaly_task_ok(agent_run=agent_run, artifacts=artifacts)
    adtributor_used = _adtributor_used(selected_candidate=selected_candidate, artifacts=artifacts)
    required_dimension_elements = _required_dimension_elements(case_id)
    dimension_elements_required = bool(required_dimension_elements)
    if required_dimension_elements and not _has_dimension_elements(selected_candidate, required_dimension_elements):
        top1_ok = False
        top3_ok = False

    return {
        "case_id": case_id,
        "intent_ok": int(intent_ok),
        "anomaly_ok": int(anomaly_ok),
        "top1_ok": int(top1_ok),
        "top3_ok": int(top3_ok),
        "evidence_coverage": evidence_coverage,
        "sql_safe": int(sql_safe),
        "reflection_repair_ok": int(reflection_repair_ok),
        "report_traceable_ok": int(report_traceable_ok),
        "memory_pollution_ok": int(memory_pollution_ok),
        "no_anomaly_task_ok": int(no_anomaly_task_ok),
        "adtributor_used": int(adtributor_used),
        "multi_agent_path": "single_agent",
        "detail": {
            "status": agent_run.get("status"),
            "metric_id": agent_run.get("metric_id"),
            "expected_metric": ground_truth.metric_id,
            "selected_candidate": selected_candidate,
            "dimension_elements_required": dimension_elements_required,
            "required_dimension_elements": sorted(required_dimension_elements),
        },
    }


def summarize_scores(
    case_scores: list[dict[str, Any]],
    *,
    dangerous_sql_blocked: bool,
) -> dict[str, Any]:
    total = len(case_scores)
    if total == 0:
        return {
            "case_total": 0,
            "dangerous_sql_blocked": dangerous_sql_blocked,
            "no_anomaly_correct": False,
        }

    return {
        "case_total": total,
        "intent_accuracy": _rate(case_scores, "intent_ok"),
        "top1_rate": _rate(case_scores, "top1_ok"),
        "top3_rate": _rate(case_scores, "top3_ok"),
        "anomaly_accuracy": _rate(case_scores, "anomaly_ok"),
        "evidence_coverage_avg": round(
            sum(float(row["evidence_coverage"]) for row in case_scores) / total,
            6,
        ),
        "sql_safe_rate": _rate(case_scores, "sql_safe"),
        "report_traceable_rate": _rate(case_scores, "report_traceable_ok"),
        "reflection_repair_ok": all(bool(row["reflection_repair_ok"]) for row in case_scores),
        "memory_pollution_ok": all(bool(row["memory_pollution_ok"]) for row in case_scores),
        "dangerous_sql_blocked": dangerous_sql_blocked,
        "no_anomaly_correct": _all_no_anomaly_traps_clean(case_scores),
    }


def dangerous_sql_blocked(
    guard: Callable[[str], Any] = guard_sql,
    dangerous_sql: str = "DELETE FROM fact_order WHERE business_date = '2026-06-05'",
) -> bool:
    plan = guard(dangerous_sql)
    return getattr(plan, "guard_status", None) == "rejected"


def _rate(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(int(row[key]) for row in rows) / len(rows), 6)


def _anomaly_ok(
    *,
    agent_run: dict[str, Any],
    ground_truth: GroundTruth,
    artifacts: PersistedArtifacts,
) -> bool:
    status = agent_run.get("status")
    if not ground_truth.expected_anomaly:
        return status == "no_anomaly" and _no_anomaly_task_ok(agent_run=agent_run, artifacts=artifacts)
    e1_summary = _evidence_summary(artifacts.evidences, "E1")
    return status == "succeeded" and e1_summary.get("is_anomaly") is True


def _matches_ground_truth(candidate: dict[str, Any] | None, ground_truth: GroundTruth) -> bool:
    if not ground_truth.expected_anomaly:
        return candidate is None
    if candidate is None:
        return False
    return (
        candidate.get("root_cause_type") == ground_truth.root_cause_type
        and candidate.get("dimension") == ground_truth.dimension
        and str(candidate.get("element")) == str(ground_truth.element)
    )


def _evidence_coverage(candidate: dict[str, Any] | None, artifacts: PersistedArtifacts) -> float:
    if artifacts.agent_run is not None and artifacts.agent_run.get("status") == "no_anomaly":
        return 1.0 if _no_anomaly_task_ok(agent_run=artifacts.agent_run, artifacts=artifacts) else 0.0
    if candidate is None or artifacts.agent_run is None:
        return 0.0
    evidence_ids = candidate.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return 0.0
    required_aliases = {"E1", "E2", "E3", "E4"}
    present_ids = {str(item) for item in evidence_ids}
    persisted = {
        row.get("evidence_id")
        for row in artifacts.evidences
        if row.get("guard_status") == "passed"
    }
    covered = {
        alias
        for alias in required_aliases
        if any(
            _evidence_id_matches_alias(evidence_id, run_id=str(artifacts.agent_run["run_id"]), alias=alias)
            for evidence_id in present_ids & persisted
        )
    }
    return round(len(covered) / len(required_aliases), 6)


def _sql_safe(sql_audit: list[dict[str, Any]]) -> bool:
    return bool(sql_audit) and all(row.get("guard_status") == "passed" for row in sql_audit)


def _reflection_repair_ok(artifacts: PersistedArtifacts) -> bool:
    nodes = [str(row.get("node")) for row in artifacts.trace_steps]
    if nodes.count("reflection_verify") <= 1:
        return True
    joined = " > ".join(nodes)
    repair_path = "reflection_verify > react_step > execute_tool > reflection_verify"
    return repair_path in joined and bool(artifacts.evidences) and bool(artifacts.sql_audit)


def _report_traceable(*, report: dict[str, Any] | None, evidences: list[dict[str, Any]]) -> bool:
    if report is None:
        return False
    if report.get("status") == "no_anomaly":
        return True
    claims = report.get("numeric_claims")
    if not isinstance(claims, list) or not claims:
        return False
    by_id = {row.get("evidence_id"): row for row in evidences}
    for claim in claims:
        if not isinstance(claim, dict):
            return False
        evidence_id = claim.get("evidence_id")
        name = claim.get("name")
        value = claim.get("value")
        row = by_id.get(evidence_id)
        if row is None:
            return False
        selected = (row.get("result_summary") or {}).get("selected_candidate")
        if not isinstance(selected, dict) or selected.get(name) != value:
            return False
    return True


def _memory_pollution_ok(candidate: dict[str, Any] | None) -> bool:
    if candidate is None:
        return True
    evidence_ids = candidate.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return False
    return all(":E" in str(evidence_id) for evidence_id in evidence_ids)


def _no_anomaly_task_ok(
    *,
    agent_run: dict[str, Any],
    artifacts: PersistedArtifacts,
) -> bool:
    if agent_run.get("status") != "no_anomaly":
        return True
    evidence_aliases = {
        str(row.get("evidence_id")).split(":", maxsplit=1)[1]
        for row in artifacts.evidences
        if ":" in str(row.get("evidence_id"))
    }
    prohibited_actions = {
        "attribute_rank",
        "drilldown_dimension",
        "fetch_related_signal",
        "rank_root_causes",
        "calculate_contribution",
    }
    has_downstream_rca = any(
        (row.get("node") in prohibited_actions) or (row.get("action") in prohibited_actions)
        for row in artifacts.trace_steps
    )
    return evidence_aliases == {"E1"} and not artifacts.tasks and not has_downstream_rca


def _evidence_summary(evidences: list[dict[str, Any]], alias: str) -> dict[str, Any]:
    suffix = f":{alias}"
    for row in evidences:
        if str(row.get("evidence_id", "")).endswith(suffix):
            summary = row.get("result_summary") or {}
            if isinstance(summary, dict):
                return summary
    return {}


def _selected_candidate(
    *,
    report: dict[str, Any] | None,
    e4_summary: dict[str, Any],
) -> dict[str, Any] | None:
    selected = e4_summary.get("selected_candidate")
    if isinstance(selected, dict):
        return selected
    return None


def _candidate_list(e4_summary: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = e4_summary.get("candidates")
    if isinstance(candidates, list):
        return [dict(item) for item in candidates if isinstance(item, dict)]
    selected = e4_summary.get("selected_candidate")
    if isinstance(selected, dict):
        return [selected]
    return []


def _adtributor_used(*, selected_candidate: dict[str, Any] | None, artifacts: PersistedArtifacts) -> bool:
    if selected_candidate is not None and selected_candidate.get("explanatory_power") is not None:
        return True
    return any(
        isinstance((row.get("result_summary") or {}).get("ranker"), str)
        and (row.get("result_summary") or {}).get("ranker") == "adtributor_internal"
        for row in artifacts.evidences
    )


def _required_dimension_elements(case_id: str) -> set[tuple[str, str]]:
    if case_id == "C06_gmv_multi_channel_drop":
        return {("channel", "paid_ads"), ("channel", "social")}
    if case_id == "C07_gmv_category_channel_cross":
        return {("channel", "paid_ads"), ("category", "electronics")}
    return set()


def _has_dimension_elements(candidate: dict[str, Any] | None, required: set[tuple[str, str]]) -> bool:
    if candidate is None:
        return False
    dimension_elements = candidate.get("dimension_elements")
    if not isinstance(dimension_elements, list):
        return False
    observed = {
        (str(item[0]), str(item[1]))
        for item in dimension_elements
        if isinstance(item, list | tuple) and len(item) == 2
    }
    return required.issubset(observed)


def _evidence_id_matches_alias(evidence_id: str, *, run_id: str, alias: str) -> bool:
    prefix = f"{run_id}:"
    if not evidence_id.startswith(prefix):
        return False
    actual_alias = evidence_id.removeprefix(prefix)
    return actual_alias == alias or actual_alias.startswith(f"{alias}_")


def _all_no_anomaly_traps_clean(case_scores: list[dict[str, Any]]) -> bool:
    traps = {"gmv_no_anomaly", "C19_gmv_seasonal_false_positive", "C20_cvr_no_anomaly_noise"}
    present = {row["case_id"] for row in case_scores if row["case_id"] in traps}
    return bool(present) and all(
        bool(row["no_anomaly_task_ok"]) and bool(row["anomaly_ok"])
        for row in case_scores
        if row["case_id"] in traps
    )

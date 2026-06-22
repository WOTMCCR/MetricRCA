"""Scoring over persisted RCA artifacts."""

from __future__ import annotations

from typing import Any, Callable

from metric_rca.evals.models import GroundTruth, PersistedArtifacts, RootCauseTruth
from metric_rca.guardrails.sql_guard import guard_sql
from metric_rca.observability.summary import build_token_summary


ALLOWED_MEMORY_LAYERS = frozenset({"case", "semantic", "episodic", "reflection"})
TRUSTED_MEMORY_SOURCES = frozenset({"reflection_verified", "system_verified"})
MIN_MEMORY_CONFIDENCE = 0.70


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
    dominant_top1_ok = _dominant_top1_ok(selected_candidate, ground_truth)
    root_cause_set_recall = _root_cause_set_recall(candidate_list, ground_truth)
    root_cause_set_precision = _root_cause_set_precision(candidate_list, ground_truth)
    weighted_explanation_coverage = _weighted_explanation_coverage(candidate_list, ground_truth)
    top3_contains_all_major_causes = _top3_contains_all_major_causes(candidate_list, ground_truth)
    top1_ok = dominant_top1_ok
    top3_ok = top3_contains_all_major_causes
    evidence_coverage = _evidence_coverage(selected_candidate, artifacts)
    sql_safe = _sql_safe(artifacts.sql_audit)
    reflection_repair_ok = _reflection_repair_ok(artifacts)
    report_traceable_ok = _report_traceable(report=report, evidences=artifacts.evidences)
    token_summary = build_token_summary(artifacts.trace_steps)
    memory_pollution_ok = _memory_pollution_ok(
        selected_candidate=selected_candidate,
        candidate_list=candidate_list,
        report=report,
        run_id=str(agent_run.get("run_id") or ""),
        metric_id=str(agent_run.get("metric_id") or ""),
        persisted_evidence_by_id={
            str(row.get("evidence_id")): row
            for row in artifacts.evidences
            if row.get("evidence_id") is not None
        },
        artifacts=artifacts,
    )
    no_anomaly_task_ok = _no_anomaly_task_ok(agent_run=agent_run, artifacts=artifacts)
    adtributor_used = _adtributor_used(selected_candidate=selected_candidate, artifacts=artifacts)
    multi_agent_path = _multi_agent_path(artifacts.trace_steps)
    return {
        "case_id": case_id,
        "intent_ok": int(intent_ok),
        "anomaly_ok": int(anomaly_ok),
        "top1_ok": int(top1_ok),
        "top3_ok": int(top3_ok),
        "dominant_top1_ok": int(dominant_top1_ok),
        "root_cause_set_recall": root_cause_set_recall,
        "root_cause_set_precision": root_cause_set_precision,
        "weighted_explanation_coverage": weighted_explanation_coverage,
        "top3_contains_all_major_causes": int(top3_contains_all_major_causes),
        "evidence_coverage": evidence_coverage,
        "sql_safe": int(sql_safe),
        "reflection_repair_ok": int(reflection_repair_ok),
        "report_traceable_ok": int(report_traceable_ok),
        "memory_pollution_ok": int(memory_pollution_ok),
        "no_anomaly_task_ok": int(no_anomaly_task_ok),
        "adtributor_used": int(adtributor_used),
        "multi_agent_path": multi_agent_path,
        "detail": {
            "status": agent_run.get("status"),
            "metric_id": agent_run.get("metric_id"),
            "expected_metric": ground_truth.metric_id,
            "selected_candidate": selected_candidate,
            "expected_root_causes": [_truth_dict(cause) for cause in _expected_causes(ground_truth)],
            "token_count": token_summary["total_tokens"],
            "latency_ms": token_summary["latency_ms"],
            "sql_count": len(artifacts.sql_audit),
            "trace_step_count": len(artifacts.trace_steps),
            "memory_read_seen": any(row.get("node") == "memory_read" for row in artifacts.trace_steps),
            "tool_sequence": _tool_sequence(artifacts.trace_steps),
            "memory_record_count": len(artifacts.memory_records),
            "memory_layers": sorted({str(row.get("layer")) for row in artifacts.memory_records}),
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

    summary = {
        "case_total": total,
        "intent_accuracy": _rate(case_scores, "intent_ok"),
        "top1_rate": _rate(case_scores, "top1_ok"),
        "top3_rate": _rate(case_scores, "top3_ok"),
        "dominant_top1_rate": _rate(case_scores, "dominant_top1_ok"),
        "root_cause_set_recall_avg": round(
            sum(float(row.get("root_cause_set_recall", 0.0)) for row in case_scores) / total,
            6,
        ),
        "root_cause_set_precision_avg": round(
            sum(float(row.get("root_cause_set_precision", 0.0)) for row in case_scores) / total,
            6,
        ),
        "weighted_explanation_coverage_avg": round(
            sum(float(row.get("weighted_explanation_coverage", 0.0)) for row in case_scores) / total,
            6,
        ),
        "top3_contains_all_major_causes_rate": _rate(case_scores, "top3_contains_all_major_causes"),
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
    summary["avg_tokens_per_case"] = round(
        sum(_detail_number(row, "token_count") for row in case_scores) / total,
        6,
    )
    summary["avg_latency_ms_per_case"] = round(
        sum(_detail_number(row, "latency_ms") for row in case_scores) / total,
        6,
    )
    summary["p95_latency_ms"] = _p95([_detail_number(row, "latency_ms") for row in case_scores])
    summary["p95_sql_count"] = _p95([_detail_number(row, "sql_count") for row in case_scores])
    summary["multi_agent_path_distribution"] = _multi_agent_path_distribution(case_scores)
    return summary


def summarize_memory_retrieval(
    enabled_scores: list[dict[str, Any]],
    disabled_scores: list[dict[str, Any]],
) -> dict[str, Any]:
    enabled_rate = _top1_rate(enabled_scores)
    disabled_rate = _top1_rate(disabled_scores)
    return {
        "memory_enabled_top1_rate": enabled_rate,
        "memory_disabled_top1_rate": disabled_rate,
        "memory_hit_improvement": round(enabled_rate - disabled_rate, 6),
        "memory_pollution_ok": all(
            bool(row["memory_pollution_ok"])
            for row in [*enabled_scores, *disabled_scores]
        ),
    }


def dangerous_sql_blocked(
    guard: Callable[[str], Any] = guard_sql,
    dangerous_sql: str = "DELETE FROM fact_order WHERE business_date = '2026-06-05'",
) -> bool:
    plan = guard(dangerous_sql)
    return getattr(plan, "guard_status", None) == "rejected"


def _rate(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(int(row[key]) for row in rows) / len(rows), 6)


def _top1_rate(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return _rate(rows, "top1_ok")


def _detail_number(row: dict[str, Any], key: str) -> float:
    detail = row.get("detail")
    if not isinstance(detail, dict):
        return 0.0
    value = detail.get(key)
    if value is None:
        return 0.0
    return float(value)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return round(float(ordered[index]), 6)


def _multi_agent_path(trace_steps: list[dict[str, Any]]) -> str:
    for row in trace_steps:
        if row.get("node") != "triage":
            continue
        action = str(row.get("action") or "")
        if action.startswith("route_"):
            return f"multi_agent:{action.removeprefix('route_')}"
    return "single_agent"


def _multi_agent_path_distribution(case_scores: list[dict[str, Any]]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for row in case_scores:
        path = str(row.get("multi_agent_path") or "single_agent")
        distribution[path] = distribution.get(path, 0) + 1
    return distribution


def _anomaly_ok(
    *,
    agent_run: dict[str, Any],
    ground_truth: GroundTruth,
    artifacts: PersistedArtifacts,
) -> bool:
    status = agent_run.get("status")
    if not ground_truth.expected_anomaly:
        return status == "no_anomaly" and _no_anomaly_artifacts_ok(agent_run=agent_run, artifacts=artifacts)
    e1_summary = _current_guard_passed_evidence_summary(artifacts, "E1")
    return status == "succeeded" and e1_summary.get("is_anomaly") is True


def _matches_ground_truth(candidate: dict[str, Any] | None, ground_truth: GroundTruth) -> bool:
    if not ground_truth.expected_anomaly:
        return candidate is None
    if candidate is None:
        return False
    return any(_matches_truth(candidate, cause) for cause in _expected_causes(ground_truth))


def _dominant_top1_ok(candidate: dict[str, Any] | None, ground_truth: GroundTruth) -> bool:
    if not ground_truth.expected_anomaly:
        return candidate is None
    dominant = _dominant_cause(ground_truth)
    return dominant is not None and _matches_truth(candidate, dominant)


def _root_cause_set_recall(candidates: list[dict[str, Any]], ground_truth: GroundTruth) -> float:
    expected = _expected_causes(ground_truth)
    if not ground_truth.expected_anomaly:
        return 1.0 if not candidates else 0.0
    if not expected:
        return 0.0
    matched = sum(1 for cause in expected if any(_matches_truth(candidate, cause) for candidate in candidates))
    return round(matched / len(expected), 6)


def _root_cause_set_precision(candidates: list[dict[str, Any]], ground_truth: GroundTruth) -> float:
    expected = _expected_causes(ground_truth)
    if not ground_truth.expected_anomaly:
        return 1.0 if not candidates else 0.0
    if not candidates:
        return 0.0
    matched = sum(1 for candidate in candidates if any(_matches_truth(candidate, cause) for cause in expected))
    return round(matched / len(candidates), 6)


def _weighted_explanation_coverage(candidates: list[dict[str, Any]], ground_truth: GroundTruth) -> float:
    expected = _expected_causes(ground_truth)
    if not ground_truth.expected_anomaly:
        return 1.0 if not candidates else 0.0
    total_weight = sum(max(float(cause.weight), 0.0) for cause in expected)
    if total_weight <= 0:
        return 0.0
    covered = sum(
        max(float(cause.weight), 0.0)
        for cause in expected
        if any(_matches_truth(candidate, cause) for candidate in candidates)
    )
    return round(covered / total_weight, 6)


def _top3_contains_all_major_causes(candidates: list[dict[str, Any]], ground_truth: GroundTruth) -> bool:
    if not ground_truth.expected_anomaly:
        return not candidates
    major = [cause for cause in _expected_causes(ground_truth) if float(cause.weight) >= 0.20]
    if not major:
        return False
    top3 = candidates[:3]
    return all(any(_matches_truth(candidate, cause) for candidate in top3) for cause in major)


def _expected_causes(ground_truth: GroundTruth) -> tuple[RootCauseTruth, ...]:
    if ground_truth.root_causes:
        return ground_truth.root_causes
    if not ground_truth.expected_anomaly:
        return ()
    return (
        RootCauseTruth(
            root_cause_type=str(ground_truth.root_cause_type),
            dimension=ground_truth.dimension,
            element=ground_truth.element,
            weight=1.0,
        ),
    )


def _dominant_cause(ground_truth: GroundTruth) -> RootCauseTruth | None:
    causes = _expected_causes(ground_truth)
    if not causes:
        return None
    return max(causes, key=lambda cause: float(cause.weight))


def _matches_truth(candidate: dict[str, Any] | None, cause: RootCauseTruth) -> bool:
    if candidate is None:
        return False
    if candidate.get("root_cause_type") != cause.root_cause_type:
        return False
    if candidate.get("dimension") == cause.dimension and str(candidate.get("element")) == str(cause.element):
        return True
    dimension_elements = candidate.get("dimension_elements") or []
    if not isinstance(dimension_elements, list):
        return False
    return any(
        isinstance(item, (list, tuple))
        and len(item) == 2
        and item[0] == cause.dimension
        and str(item[1]) == str(cause.element)
        for item in dimension_elements
    )


def _truth_dict(cause: RootCauseTruth) -> dict[str, Any]:
    return {
        "root_cause_type": cause.root_cause_type,
        "dimension": cause.dimension,
        "element": cause.element,
        "weight": cause.weight,
    }


def _evidence_coverage(candidate: dict[str, Any] | None, artifacts: PersistedArtifacts) -> float:
    if artifacts.agent_run is not None and artifacts.agent_run.get("status") == "no_anomaly":
        return 1.0 if _no_anomaly_artifacts_ok(agent_run=artifacts.agent_run, artifacts=artifacts) else 0.0
    if candidate is None or artifacts.agent_run is None:
        return 0.0
    evidence_ids = candidate.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return 0.0
    required_aliases = {"E1", "E2", "E3", "E4", "E_rank"}
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
        if _no_anomaly_report_has_root_cause_content(report):
            return False
        run_id = str(next((row.get("run_id") for row in evidences if row.get("run_id")), ""))
        persisted = {
            str(row.get("evidence_id")): row
            for row in evidences
            if row.get("evidence_id") is not None
        }
        return _evidence_ids_are_current_run(
            report.get("evidence_ids"),
            run_id=run_id,
            persisted_evidence_by_id=persisted,
        )
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
        summary = row.get("result_summary") or {}
        contribution_set = summary.get("contribution_set") if isinstance(summary, dict) else None
        selected = contribution_set.get("selected_candidate") if isinstance(contribution_set, dict) else summary.get("selected_candidate")
        if not isinstance(selected, dict) or selected.get(name) != value:
            return False
    return True


def _memory_pollution_ok(
    *,
    selected_candidate: dict[str, Any] | None,
    candidate_list: list[dict[str, Any]],
    report: dict[str, Any] | None,
    run_id: str,
    metric_id: str,
    persisted_evidence_by_id: dict[str, dict[str, Any]],
    artifacts: PersistedArtifacts,
) -> bool:
    candidates = [candidate for candidate in [selected_candidate, *candidate_list] if candidate is not None]
    for candidate in candidates:
        evidence_ids = candidate.get("evidence_ids")
        if not _evidence_ids_are_current_run(
            evidence_ids,
            run_id=run_id,
            persisted_evidence_by_id=persisted_evidence_by_id,
        ):
            return False
    if report is None:
        return _memory_artifacts_ok(artifacts=artifacts, run_id=run_id, metric_id=metric_id)
    if report.get("status") == "no_anomaly" and _no_anomaly_report_has_root_cause_content(report):
        return False
    report_evidence_ids = report.get("evidence_ids")
    if report.get("status") == "no_anomaly" and report_evidence_ids is None:
        return False
    if report_evidence_ids is not None and not _evidence_ids_are_current_run(
        report_evidence_ids,
        run_id=run_id,
        persisted_evidence_by_id=persisted_evidence_by_id,
    ):
        return False
    claims = report.get("numeric_claims")
    if not isinstance(claims, list):
        return _memory_artifacts_ok(artifacts=artifacts, run_id=run_id, metric_id=metric_id)
    claims_ok = all(
        _evidence_ids_are_current_run(
            [claim.get("evidence_id")],
            run_id=run_id,
            persisted_evidence_by_id=persisted_evidence_by_id,
        )
        for claim in claims
        if isinstance(claim, dict)
    )
    return claims_ok and _memory_artifacts_ok(artifacts=artifacts, run_id=run_id, metric_id=metric_id)


def _memory_artifacts_ok(*, artifacts: PersistedArtifacts, run_id: str, metric_id: str) -> bool:
    if not artifacts.memory_records and not _memory_read_hits(artifacts.trace_steps):
        return True
    if not run_id or not metric_id:
        return False
    current_scope = _current_e1_scope(artifacts)
    if current_scope is None:
        return False
    if not all(_memory_record_ok(row, run_id=run_id, metric_id=metric_id) for row in artifacts.memory_records):
        return False
    return all(
        _memory_hit_ok(hit, run_id=run_id, metric_id=metric_id, current_scope=current_scope)
        for hit in _memory_read_hits(artifacts.trace_steps)
    )


def _memory_record_ok(row: dict[str, Any], *, run_id: str, metric_id: str) -> bool:
    if not isinstance(row, dict):
        return False
    layer = str(row.get("layer") or "")
    if layer not in ALLOWED_MEMORY_LAYERS:
        return False
    if str(row.get("source") or "") not in TRUSTED_MEMORY_SOURCES:
        return False
    if not _memory_confidence_ok(row.get("confidence")):
        return False
    mem_key = str(row.get("mem_key") or "")
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return False
    if _memory_filters(row) is None:
        return False
    payload_metric = payload.get("metric_id")
    if layer == "semantic":
        return mem_key == f"{metric_id}|semantic" and payload_metric == metric_id
    return (
        (mem_key == f"{metric_id}|run" or mem_key.startswith(f"{run_id}|"))
        and payload_metric == metric_id
    )


def _memory_hit_ok(
    hit: dict[str, Any],
    *,
    run_id: str,
    metric_id: str,
    current_scope: dict[str, str],
) -> bool:
    if not isinstance(hit, dict):
        return False
    layer = str(hit.get("layer") or "")
    if layer not in ALLOWED_MEMORY_LAYERS:
        return False
    if str(hit.get("source") or "") not in TRUSTED_MEMORY_SOURCES:
        return False
    if not _memory_confidence_ok(hit.get("confidence")):
        return False
    mem_key = str(hit.get("mem_key") or "")
    if layer == "semantic" and mem_key not in {"", f"{metric_id}|semantic"}:
        return False
    if layer != "semantic" and mem_key not in {"", f"{metric_id}|run"} and not mem_key.startswith(f"{run_id}|"):
        return False
    filters = _memory_filters(hit)
    if filters is None:
        return False
    if layer == "semantic":
        return not filters or filters == current_scope
    return filters == current_scope if current_scope else not filters


def _memory_read_hits(trace_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for row in trace_steps:
        if row.get("node") != "memory_read":
            continue
        output = row.get("output_summary")
        if not isinstance(output, dict):
            return [{}]
        raw_hits = output.get("hits")
        if raw_hits is None:
            continue
        if not isinstance(raw_hits, list):
            return [{}]
        hits.extend(item if isinstance(item, dict) else {} for item in raw_hits)
    return hits


def _current_e1_scope(artifacts: PersistedArtifacts) -> dict[str, str] | None:
    summary = _current_guard_passed_evidence_summary(artifacts, "E1")
    raw_filters = summary.get("filters")
    if raw_filters is None:
        return {}
    if not isinstance(raw_filters, dict):
        return None
    return {str(key): str(value) for key, value in raw_filters.items()}


def _memory_filters(row: dict[str, Any]) -> dict[str, str] | None:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    raw_filters = row.get("filters", payload.get("filters"))
    if raw_filters is None:
        return {}
    if not isinstance(raw_filters, dict):
        return None
    return {str(key): str(value) for key, value in raw_filters.items()}


def _memory_confidence_ok(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return float(value) >= MIN_MEMORY_CONFIDENCE
    except (TypeError, ValueError):
        return False


def _evidence_ids_are_current_run(
    evidence_ids: Any,
    *,
    run_id: str,
    persisted_evidence_by_id: dict[str, dict[str, Any]],
) -> bool:
    if not isinstance(evidence_ids, list):
        return False
    if not evidence_ids:
        return False
    if not run_id:
        return False
    prefix = f"{run_id}:E"
    for evidence_id in evidence_ids:
        evidence_text = str(evidence_id)
        if _is_memory_evidence_id(evidence_text) or not evidence_text.startswith(prefix):
            return False
        persisted = persisted_evidence_by_id.get(evidence_text)
        if persisted is None or persisted.get("guard_status") != "passed":
            return False
    return True


def _is_memory_evidence_id(value: Any) -> bool:
    text = str(value or "")
    return text.startswith("memory:") or text.startswith("mem-") or "|memory" in text


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


def _no_anomaly_artifacts_ok(
    *,
    agent_run: dict[str, Any],
    artifacts: PersistedArtifacts,
) -> bool:
    if not _no_anomaly_task_ok(agent_run=agent_run, artifacts=artifacts):
        return False
    run_id = str(agent_run.get("run_id") or "")
    persisted = {
        str(row.get("evidence_id")): row
        for row in artifacts.evidences
        if row.get("evidence_id") is not None
    }
    report = artifacts.report
    if not isinstance(report, dict) or report.get("status") != "no_anomaly":
        return False
    if _no_anomaly_report_has_root_cause_content(report):
        return False
    if not _evidence_ids_are_current_run(
        report.get("evidence_ids"),
        run_id=run_id,
        persisted_evidence_by_id=persisted,
    ):
        return False
    return _evidence_ids_are_current_run(
        [f"{run_id}:E1"],
        run_id=run_id,
        persisted_evidence_by_id=persisted,
    )


def _current_guard_passed_evidence_summary(artifacts: PersistedArtifacts, alias: str) -> dict[str, Any]:
    agent_run = artifacts.agent_run or {}
    run_id = str(agent_run.get("run_id") or "")
    if not run_id:
        return {}
    for row in artifacts.evidences:
        evidence_id = str(row.get("evidence_id") or "")
        if row.get("guard_status") != "passed":
            continue
        if not _evidence_id_matches_alias(evidence_id, run_id=run_id, alias=alias):
            continue
        summary = row.get("result_summary") or {}
        return summary if isinstance(summary, dict) else {}
    return {}


def _no_anomaly_report_has_root_cause_content(report: dict[str, Any]) -> bool:
    prohibited_keys = {"top_candidate", "candidates", "numeric_claims", "root_cause_type", "dimension", "element"}
    for key in prohibited_keys:
        value = report.get(key)
        if value is None:
            continue
        if isinstance(value, list | dict | str) and len(value) == 0:
            continue
        return True
    return False


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
    contribution_set = e4_summary.get("contribution_set")
    if isinstance(contribution_set, dict):
        selected = contribution_set.get("selected_candidate")
        if isinstance(selected, dict):
            return selected
    selected = e4_summary.get("selected_candidate")
    if isinstance(selected, dict):
        return selected
    return None


def _candidate_list(e4_summary: dict[str, Any]) -> list[dict[str, Any]]:
    contribution_set = e4_summary.get("contribution_set")
    if isinstance(contribution_set, dict):
        candidates = contribution_set.get("candidates")
        if isinstance(candidates, list):
            return [dict(item) for item in candidates if isinstance(item, dict)]
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


def _tool_sequence(trace_steps: list[dict[str, Any]]) -> list[str]:
    sequence: list[str] = []
    for row in trace_steps:
        action = row.get("action")
        node = row.get("node")
        value = action if isinstance(action, str) and action else node
        if isinstance(value, str) and value:
            sequence.append(value)
    return sequence


def _evidence_id_matches_alias(evidence_id: str, *, run_id: str, alias: str) -> bool:
    prefix = f"{run_id}:"
    if not evidence_id.startswith(prefix):
        return False
    actual_alias = evidence_id.removeprefix(prefix)
    return actual_alias == alias or actual_alias.startswith(f"{alias}_")


def _all_no_anomaly_traps_clean(case_scores: list[dict[str, Any]]) -> bool:
    traps = {
        "gmv_no_anomaly",
        "C19_gmv_seasonal_false_positive",
        "C20_cvr_no_anomaly_noise",
        "C22_gmv_borderline",
    }
    present = {row["case_id"] for row in case_scores if row["case_id"] in traps}
    return bool(present) and all(
        bool(row["no_anomaly_task_ok"]) and bool(row["anomaly_ok"])
        for row in case_scores
        if row["case_id"] in traps
    )

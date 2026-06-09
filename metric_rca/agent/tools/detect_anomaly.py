"""detect_anomaly tool: current metric versus exact same-weekday baseline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from metric_rca.agent.tools.schemas import DetectAnomalyArgs, ToolResult
from metric_rca.config.settings import Settings, get_settings
from metric_rca.domain.models import Evidence, Observation, SQLPlan
from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql
from metric_rca.services.anomaly_service import detect_anomaly_from_rows
from metric_rca.services.metric_service import MetricServiceError


def detect_anomaly(
    args: DetectAnomalyArgs,
    *,
    repository: Any,
    metric_service: Any,
    renderer: SQLRenderer | None = None,
    settings: Settings | None = None,
) -> ToolResult:
    action = "detect_anomaly"
    run_error = _run_context_error(repository, args.run_id, args.metric_id, args.target_date)
    if run_error:
        return _error(action, run_error, "run_id is not an active matching run")
    renderer = renderer or SQLRenderer()
    settings = settings or get_settings()
    try:
        metric_definition = metric_service.get_metric_definition(args.metric_id)
        current_plan = _guarded_plan(
            renderer.render(
                build_query_spec(
                    metric_id=args.metric_id,
                    start_date=args.target_date,
                    end_date=args.target_date,
                    filters=args.filters,
                    purpose="current",
                )
            )
        )
        baseline_spec = build_query_spec(
            metric_id=args.metric_id,
            start_date=args.target_date,
            end_date=args.target_date,
            filters=args.filters,
            purpose="baseline",
        )
        baseline_plan = _guarded_plan(
            renderer.render(
                baseline_spec
            )
        )
    except MetricServiceError as exc:
        return _error(action, exc.code, str(exc))
    except QuerySpecError as exc:
        return _error(action, exc.code, str(exc))
    if current_plan.guard_status != "passed" or baseline_plan.guard_status != "passed":
        return _error(action, "SQL_GUARD_REJECTED", "renderer output failed SQLGuard")

    current = repository.execute_plan(current_plan, run_id=args.run_id)
    baseline = repository.execute_plan(baseline_plan, run_id=args.run_id)
    result = detect_anomaly_from_rows(
        current_rows=current.rows,
        baseline_rows=baseline.rows,
        metric_definition=metric_definition,
        thresh_pct=settings.thresh_pct,
        z_thresh=settings.z_thresh,
    )
    if not result.ok:
        return _error(action, result.error_code or "ANOMALY_DETECTION_FAILED", "anomaly detection failed")

    result_summary = {
        **result.result_summary,
        "query_sources": _query_sources(current_plan=current_plan, baseline_plan=baseline_plan),
    }
    evidence = _evidence(
        run_id=args.run_id,
        alias="E1",
        plan=baseline_plan,
        query_spec=baseline_spec,
        result_summary=result_summary,
        data_source=metric_definition.source_table,
    )
    repository.create_evidence(_evidence_row(args.run_id, evidence))
    observation = Observation(
        action_name=action,
        ok=True,
        payload=result_summary,
        evidence_ids=[evidence.evidence_id],
        error_code=result.error_code,
        message="no anomaly detected" if result.error_code == "NO_ANOMALY_DETECTED" else None,
    )
    return ToolResult(observation=observation, evidences=[evidence], evidence_alias="E1")


def _guarded_plan(plan: SQLPlan) -> SQLPlan:
    return guard_sql(plan)


def _run_context_error(repository: Any, run_id: str, metric_id: str, target_date: Any) -> str | None:
    row = repository.get_agent_run(run_id)
    if row is None or row.get("status") != "running":
        return "RUN_NOT_FOUND"
    if row.get("metric_id") != metric_id or str(row.get("target_date")) != str(target_date):
        return "RUN_CONTEXT_MISMATCH"
    return None


def _error(action: str, code: str, message: str) -> ToolResult:
    return ToolResult(
        observation=Observation(action_name=action, ok=False, error_code=code, message=message)
    )


def _query_sources(*, current_plan: SQLPlan, baseline_plan: SQLPlan) -> dict[str, Any]:
    return {
        "current_sql_hash": current_plan.sql_hash,
        "baseline_sql_hash": baseline_plan.sql_hash,
        "current_sql": current_plan.sql,
        "baseline_sql": baseline_plan.sql,
        "current_params": {key: str(value) for key, value in current_plan.params.items()},
        "baseline_params": {key: str(value) for key, value in baseline_plan.params.items()},
    }


def _evidence(
    *,
    run_id: str,
    alias: str,
    plan: SQLPlan,
    query_spec: Any,
    result_summary: dict[str, Any],
    data_source: str,
) -> Evidence:
    return Evidence(
        evidence_id=f"{run_id}:{alias}",
        query_spec=query_spec,
        sql=plan.sql,
        sql_hash=plan.sql_hash,
        guard_status=plan.guard_status,
        result_summary=result_summary,
        data_source=data_source,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def _evidence_row(run_id: str, evidence: Evidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "run_id": run_id,
        "query_spec": evidence.query_spec.model_dump(mode="json"),
        "sql_text": evidence.sql,
        "sql_hash": evidence.sql_hash,
        "guard_status": evidence.guard_status,
        "result_summary": evidence.result_summary,
        "data_source": evidence.data_source,
        "created_at": evidence.created_at,
    }

"""detect_anomaly tool: current metric versus exact same-weekday baseline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from metric_rca.agent.tools.runtime import (
    ToolRuntimeError,
    evidence_row,
    execute_guarded_plan,
    query_sources,
    run_context_error,
    runtime_error,
    tool_error,
)
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
    run_error = run_context_error(repository, args.run_id, args.metric_id, args.target_date)
    if run_error:
        return tool_error(action, run_error, "run_id is not an active matching run")
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
        return tool_error(action, exc.code, str(exc))
    except QuerySpecError as exc:
        return tool_error(action, exc.code, str(exc))

    try:
        current = execute_guarded_plan(repository=repository, plan=current_plan, run_id=args.run_id)
        baseline = execute_guarded_plan(repository=repository, plan=baseline_plan, run_id=args.run_id)
    except ToolRuntimeError as exc:
        return runtime_error(action, exc)
    result = detect_anomaly_from_rows(
        current_rows=current.rows,
        baseline_rows=baseline.rows,
        metric_definition=metric_definition,
        thresh_pct=settings.thresh_pct,
        z_thresh=settings.z_thresh,
    )
    if not result.ok:
        return tool_error(action, result.error_code or "ANOMALY_DETECTION_FAILED", "anomaly detection failed")

    result_summary = {
        **result.result_summary,
        "query_sources": query_sources(current_plan=current_plan, baseline_plan=baseline_plan),
    }
    evidence = _evidence(
        run_id=args.run_id,
        alias="E1",
        plan=baseline_plan,
        query_spec=baseline_spec,
        result_summary=result_summary,
        data_source=metric_definition.source_table,
    )
    repository.create_evidence(evidence_row(args.run_id, evidence))
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

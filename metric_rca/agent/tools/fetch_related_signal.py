"""fetch_related_signal tool: E3 evidence for campaign, inventory, conversion, and refund-quality signals."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from metric_rca.agent.tools.runtime import (
    ToolRuntimeError,
    current_run_guarded_evidence,
    evidence_row,
    execute_guarded_plan,
    persist_evidence,
    query_sources,
    run_context_error,
    runtime_error,
    tool_error,
)
from metric_rca.agent.tools.schemas import FetchRelatedSignalArgs, ToolResult
from metric_rca.config.settings import Settings, get_settings
from metric_rca.domain.models import Evidence, Observation
from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql
from metric_rca.services.anomaly_service import detect_anomaly_from_rows
from metric_rca.services.metric_service import MetricServiceError


def fetch_related_signal(
    args: FetchRelatedSignalArgs,
    *,
    repository: Any,
    metric_service: Any,
    renderer: SQLRenderer | None = None,
    settings: Settings | None = None,
) -> ToolResult:
    action = "fetch_related_signal"
    run_error = run_context_error(repository, args.run_id, args.metric_id, args.target_date)
    if run_error:
        return tool_error(action, run_error, "run_id is not an active matching run")
    if not current_run_guarded_evidence(repository, args.run_id, args.evidence_ids, {"E1", "E2"}):
        return tool_error(action, "EVIDENCE_MISSING", "guard-passed current-run evidence is required")
    renderer = renderer or SQLRenderer()
    settings = settings or get_settings()
    signal_metric_id = settings.signal_metric_by_type.get(args.signal_type)
    if signal_metric_id is None:
        return tool_error(action, "CONFIG_INVALID", f"signal metric missing: {args.signal_type}")
    filters = {args.dimension: args.element}
    signal_hint = "campaign" if args.signal_type == "campaign" else "metric"
    try:
        metric_definition = metric_service.get_metric_definition(signal_metric_id)
        current_spec = build_query_spec(
            metric_id=signal_metric_id,
            start_date=args.target_date,
            end_date=args.target_date,
            filters=filters,
            purpose="signal",
            signal_type=signal_hint,
        )
        baseline_spec = build_query_spec(
            metric_id=signal_metric_id,
            start_date=args.target_date,
            end_date=args.target_date,
            filters=filters,
            purpose="baseline",
            signal_type=signal_hint,
        )
        current_plan = guard_sql(renderer.render(current_spec))
        baseline_plan = guard_sql(renderer.render(baseline_spec))
    except MetricServiceError as exc:
        return tool_error(action, exc.code, str(exc))
    except QuerySpecError as exc:
        return tool_error(action, exc.code, str(exc))

    try:
        current = execute_guarded_plan(repository=repository, plan=current_plan, run_id=args.run_id)
        baseline = execute_guarded_plan(repository=repository, plan=baseline_plan, run_id=args.run_id)
    except ToolRuntimeError as exc:
        return runtime_error(action, exc)
    signal = detect_anomaly_from_rows(
        current_rows=current.rows,
        baseline_rows=baseline.rows,
        metric_definition=metric_definition,
        thresh_pct=0.10,
        z_thresh=1.0,
    )
    if not signal.ok:
        return tool_error(action, signal.error_code or "SIGNAL_INSUFFICIENT", "signal data insufficient")
    result_summary = {
        "signal_type": args.signal_type,
        "signal_metric_id": signal_metric_id,
        "dimension": args.dimension,
        "element": args.element,
        "input_evidence_ids": args.evidence_ids,
        "query_sources": query_sources(current_plan=current_plan, baseline_plan=baseline_plan),
        **signal.result_summary,
    }
    evidence = Evidence(
        evidence_id=f"{args.run_id}:E3",
        query_spec=baseline_spec,
        sql=baseline_plan.sql,
        sql_hash=baseline_plan.sql_hash,
        guard_status=baseline_plan.guard_status,
        result_summary=result_summary,
        data_source="fact_campaign" if args.signal_type == "campaign" else metric_definition.source_table,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    try:
        persist_evidence(repository=repository, row=evidence_row(args.run_id, evidence))
    except ToolRuntimeError as exc:
        return runtime_error(action, exc)
    return ToolResult(
        observation=Observation(
            action_name=action,
            ok=True,
            payload=result_summary,
            evidence_ids=[evidence.evidence_id],
            error_code=signal.error_code,
        ),
        evidences=[evidence],
        evidence_alias="E3",
        sql_count=2,
    )

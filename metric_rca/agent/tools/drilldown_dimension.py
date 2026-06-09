"""drilldown_dimension tool: dimension contribution candidates with E2 evidence."""

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
from metric_rca.agent.tools.schemas import DrilldownDimensionArgs, ToolResult
from metric_rca.domain.models import Evidence, Observation
from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql
from metric_rca.services.attribution_service import compute_dimension_contribution
from metric_rca.services.metric_service import MetricServiceError


def drilldown_dimension(
    args: DrilldownDimensionArgs,
    *,
    repository: Any,
    metric_service: Any,
    renderer: SQLRenderer | None = None,
) -> ToolResult:
    action = "drilldown_dimension"
    run_error = run_context_error(repository, args.run_id, args.metric_id, args.target_date)
    if run_error:
        return tool_error(action, run_error, "run_id is not an active matching run")
    if not current_run_guarded_evidence(repository, args.run_id, args.evidence_ids, {"E1"}):
        return tool_error(action, "EVIDENCE_MISSING", "guard-passed current-run evidence is required")
    renderer = renderer or SQLRenderer()
    try:
        metric_definition = metric_service.get_metric_definition(args.metric_id)
        current_spec = build_query_spec(
            metric_id=args.metric_id,
            start_date=args.target_date,
            end_date=args.target_date,
            group_by=[args.dimension],
            filters=args.filters,
            purpose="drilldown",
        )
        baseline_spec = build_query_spec(
            metric_id=args.metric_id,
            start_date=args.target_date,
            end_date=args.target_date,
            group_by=[args.dimension],
            filters=args.filters,
            purpose="baseline",
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
    attribution = compute_dimension_contribution(
        metric_definition=metric_definition,
        dimension=args.dimension,
        current_rows=current.rows,
        baseline_rows=baseline.rows,
        evidence_ids=args.evidence_ids,
    )
    if not attribution.ok:
        return tool_error(action, attribution.error_code or "ATTRIBUTION_COVERAGE_LOW", "attribution coverage low")

    result_summary = {
        "metric_id": args.metric_id,
        "dimension": args.dimension,
        "input_evidence_ids": args.evidence_ids,
        "query_sources": query_sources(current_plan=current_plan, baseline_plan=baseline_plan),
        "candidates": [candidate.model_dump(mode="json") for candidate in attribution.candidates],
        "coverage": attribution.coverage,
    }
    evidence = Evidence(
        evidence_id=f"{args.run_id}:E2",
        query_spec=baseline_spec,
        sql=baseline_plan.sql,
        sql_hash=baseline_plan.sql_hash,
        guard_status=baseline_plan.guard_status,
        result_summary=result_summary,
        data_source=metric_definition.source_table,
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
        ),
        evidences=[evidence],
        evidence_alias="E2",
        candidates=attribution.candidates,
    )

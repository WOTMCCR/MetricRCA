"""calculate_contribution tool: E4 final contribution from current-run evidence and fresh guarded queries."""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any

from metric_rca.agent.tools.runtime import (
    ToolRuntimeError,
    current_run_guarded_evidence,
    evidence_row,
    execute_guarded_plan,
    query_sources,
    run_context_error,
    runtime_error,
    tool_error,
)
from metric_rca.agent.tools.schemas import CalculateContributionArgs, ToolResult
from metric_rca.domain.models import Evidence, Observation
from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql
from metric_rca.services.attribution_service import compute_dimension_contribution, compute_gmv_decomposition
from metric_rca.services.metric_service import MetricServiceError


def calculate_contribution(
    args: CalculateContributionArgs,
    *,
    repository: Any,
    metric_service: Any,
    renderer: SQLRenderer | None = None,
) -> ToolResult:
    action = "calculate_contribution"
    run_error = run_context_error(repository, args.run_id, args.metric_id, args.target_date)
    if run_error:
        return tool_error(action, run_error, "run_id is not an active matching run")
    if not current_run_guarded_evidence(repository, args.run_id, args.evidence_ids, {"E1", "E2", "E3"}):
        return tool_error(action, "EVIDENCE_MISSING", "guard-passed current-run evidence is required")
    renderer = renderer or SQLRenderer()
    filters = {**args.filters}
    try:
        metric_definition = metric_service.get_metric_definition(args.metric_id)
        current_spec = build_query_spec(
            metric_id=args.metric_id,
            start_date=args.target_date,
            end_date=args.target_date,
            group_by=[args.dimension],
            filters=filters,
            purpose="drilldown",
        )
        baseline_spec = build_query_spec(
            metric_id=args.metric_id,
            start_date=args.target_date,
            end_date=args.target_date,
            group_by=[args.dimension],
            filters=filters,
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
    current_run_evidence = [*args.evidence_ids, f"{args.run_id}:E4"]
    attribution = compute_dimension_contribution(
        metric_definition=metric_definition,
        dimension=args.dimension,
        current_rows=current.rows,
        baseline_rows=baseline.rows,
        evidence_ids=current_run_evidence,
    )
    if not attribution.ok:
        return tool_error(action, attribution.error_code or "ATTRIBUTION_COVERAGE_LOW", "attribution coverage low")

    result_summary = {
        "metric_id": args.metric_id,
        "dimension": args.dimension,
        "element": args.element,
        "input_evidence_ids": args.evidence_ids,
        "query_sources": query_sources(current_plan=current_plan, baseline_plan=baseline_plan),
        "candidates": [candidate.model_dump(mode="json") for candidate in attribution.candidates],
    }
    if args.metric_id == "gmv":
        try:
            factor_summary = _gmv_factor_decomposition(
                repository=repository,
                renderer=renderer,
                run_id=args.run_id,
                target_date=args.target_date,
                dimension=args.dimension,
                element=args.element,
            )
        except ToolRuntimeError as exc:
            return runtime_error(action, exc)
        except ValueError as exc:
            code = str(exc)
            if code in {"NO_CURRENT_DATA", "INSUFFICIENT_BASELINE_DATA"}:
                return tool_error(action, code, "factor decomposition failed")
            raise
        result_summary["decomposition"] = factor_summary["decomposition"]
        result_summary["factor_query_sources"] = factor_summary["query_sources"]
    else:
        result_summary["metric_contribution"] = _metric_contribution_summary(
            metric_id=args.metric_id,
            dimension=args.dimension,
            element=args.element,
            coverage=attribution.coverage,
        )
    evidence = Evidence(
        evidence_id=f"{args.run_id}:E4",
        query_spec=baseline_spec,
        sql=baseline_plan.sql,
        sql_hash=baseline_plan.sql_hash,
        guard_status=baseline_plan.guard_status,
        result_summary=result_summary,
        data_source=metric_definition.source_table,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    repository.create_evidence(evidence_row(args.run_id, evidence))
    return ToolResult(
        observation=Observation(
            action_name=action,
            ok=True,
            payload=result_summary,
            evidence_ids=[evidence.evidence_id],
        ),
        evidences=[evidence],
        evidence_alias="E4",
        candidates=attribution.candidates,
    )


def _gmv_factor_decomposition(
    *,
    repository: Any,
    renderer: SQLRenderer,
    run_id: str,
    target_date,
    dimension: str,
    element: str,
) -> dict[str, Any]:
    filters = {dimension: element}
    current_values: dict[str, float] = {}
    baseline_values: dict[str, float] = {}
    factor_query_sources: dict[str, dict[str, Any]] = {}
    for metric_id in ["gmv", "uv", "pay_cvr"]:
        current_spec = build_query_spec(
            metric_id=metric_id,
            start_date=target_date,
            end_date=target_date,
            filters=filters,
            purpose="current",
        )
        baseline_spec = build_query_spec(
            metric_id=metric_id,
            start_date=target_date,
            end_date=target_date,
            filters=filters,
            purpose="baseline",
        )
        current_plan = guard_sql(renderer.render(current_spec))
        baseline_plan = guard_sql(renderer.render(baseline_spec))
        current_result = execute_guarded_plan(repository=repository, plan=current_plan, run_id=run_id)
        baseline_result = execute_guarded_plan(repository=repository, plan=baseline_plan, run_id=run_id)
        current_values[metric_id] = _single_metric_value(current_result.rows)
        baseline_values[metric_id] = _mean_metric_value(baseline_result.rows)
        factor_query_sources[metric_id] = query_sources(current_plan=current_plan, baseline_plan=baseline_plan)

    current_pay_user_cnt = current_values["pay_cvr"] * current_values["uv"]
    baseline_pay_user_cnt = baseline_values["pay_cvr"] * baseline_values["uv"]
    return {
        "decomposition": compute_gmv_decomposition(
            current={
                "gmv": current_values["gmv"],
                "uv": current_values["uv"],
                "pay_user_cnt": current_pay_user_cnt,
            },
            baseline={
                "gmv": baseline_values["gmv"],
                "uv": baseline_values["uv"],
                "pay_user_cnt": baseline_pay_user_cnt,
            },
        ),
        "query_sources": factor_query_sources,
    }


def _metric_contribution_summary(
    *,
    metric_id: str,
    dimension: str,
    element: str,
    coverage: float,
) -> dict[str, Any]:
    return {
        "model": "dimension_delta",
        "metric_id": metric_id,
        "dimension": dimension,
        "element": element,
        "coverage": coverage,
    }


def _single_metric_value(rows: list[dict[str, Any]]) -> float:
    if not rows or rows[0].get("metric_value") is None:
        raise ValueError("NO_CURRENT_DATA")
    return float(rows[0]["metric_value"])


def _mean_metric_value(rows: list[dict[str, Any]]) -> float:
    values = [float(row["metric_value"]) for row in rows if row.get("metric_value") is not None]
    if len(values) < 3:
        raise ValueError("INSUFFICIENT_BASELINE_DATA")
    return mean(values)

"""calculate_contribution tool: E4 final contribution from current-run evidence and fresh guarded queries."""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any

from metric_rca.agent.tools.schemas import CalculateContributionArgs, ToolResult
from metric_rca.domain.models import Evidence, Observation, SQLPlan
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
    run_error = _run_context_error(repository, args.run_id, args.metric_id, args.target_date)
    if run_error:
        return _error(action, run_error, "run_id is not an active matching run")
    if not _current_run_guarded_evidence(repository, args.run_id, args.evidence_ids, {"E1", "E2", "E3"}):
        return _error(action, "EVIDENCE_MISSING", "guard-passed current-run evidence is required")
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
        return _error(action, exc.code, str(exc))
    except QuerySpecError as exc:
        return _error(action, exc.code, str(exc))
    if current_plan.guard_status != "passed" or baseline_plan.guard_status != "passed":
        return _error(action, "SQL_GUARD_REJECTED", "renderer output failed SQLGuard")

    current = repository.execute_plan(current_plan, run_id=args.run_id)
    baseline = repository.execute_plan(baseline_plan, run_id=args.run_id)
    current_run_evidence = [*args.evidence_ids, f"{args.run_id}:E4"]
    attribution = compute_dimension_contribution(
        metric_definition=metric_definition,
        dimension=args.dimension,
        current_rows=current.rows,
        baseline_rows=baseline.rows,
        evidence_ids=current_run_evidence,
    )
    if not attribution.ok:
        return _error(action, attribution.error_code or "ATTRIBUTION_COVERAGE_LOW", "attribution coverage low")

    try:
        factor_summary = _factor_decomposition(
            repository=repository,
            renderer=renderer,
            run_id=args.run_id,
            target_date=args.target_date,
            dimension=args.dimension,
            element=args.element,
        )
    except ValueError as exc:
        code = str(exc)
        if code in {"SQL_GUARD_REJECTED", "NO_CURRENT_DATA", "INSUFFICIENT_BASELINE_DATA"}:
            return _error(action, code, "factor decomposition failed")
        raise
    result_summary = {
        "metric_id": args.metric_id,
        "dimension": args.dimension,
        "element": args.element,
        "input_evidence_ids": args.evidence_ids,
        "query_sources": _query_sources(current_plan=current_plan, baseline_plan=baseline_plan),
        "decomposition": factor_summary["decomposition"],
        "factor_query_sources": factor_summary["query_sources"],
        "candidates": [candidate.model_dump(mode="json") for candidate in attribution.candidates],
    }
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
    repository.create_evidence(_evidence_row(args.run_id, evidence))
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


def _run_context_error(repository: Any, run_id: str, metric_id: str, target_date: Any) -> str | None:
    row = repository.get_agent_run(run_id)
    if row is None or row.get("status") != "running":
        return "RUN_NOT_FOUND"
    if row.get("metric_id") != metric_id or str(row.get("target_date")) != str(target_date):
        return "RUN_CONTEXT_MISMATCH"
    return None


def _current_run_guarded_evidence(
    repository: Any,
    run_id: str,
    evidence_ids: list[str],
    required_aliases: set[str],
) -> bool:
    if not evidence_ids:
        return False
    aliases = {evidence_id.split(":", maxsplit=1)[1] for evidence_id in evidence_ids if ":" in evidence_id}
    if not required_aliases.issubset(aliases):
        return False
    for evidence_id in evidence_ids:
        if not evidence_id.startswith(f"{run_id}:"):
            return False
        row = repository.get_evidence(run_id=run_id, evidence_id=evidence_id)
        if row is None or row.get("guard_status") != "passed":
            return False
    return True


def _error(action: str, code: str, message: str) -> ToolResult:
    return ToolResult(
        observation=Observation(action_name=action, ok=False, error_code=code, message=message)
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


def _factor_decomposition(
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
    query_sources: dict[str, dict[str, Any]] = {}
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
        if current_plan.guard_status != "passed" or baseline_plan.guard_status != "passed":
            raise ValueError("SQL_GUARD_REJECTED")
        current_result = repository.execute_plan(current_plan, run_id=run_id)
        baseline_result = repository.execute_plan(baseline_plan, run_id=run_id)
        current_values[metric_id] = _single_metric_value(current_result.rows)
        baseline_values[metric_id] = _mean_metric_value(baseline_result.rows)
        query_sources[metric_id] = _query_sources(current_plan=current_plan, baseline_plan=baseline_plan)

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
        "query_sources": query_sources,
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


def _query_sources(*, current_plan: SQLPlan, baseline_plan: SQLPlan) -> dict[str, Any]:
    return {
        "current_sql_hash": current_plan.sql_hash,
        "baseline_sql_hash": baseline_plan.sql_hash,
        "current_sql": current_plan.sql,
        "baseline_sql": baseline_plan.sql,
        "current_params": {key: str(value) for key, value in current_plan.params.items()},
        "baseline_params": {key: str(value) for key, value in baseline_plan.params.items()},
    }

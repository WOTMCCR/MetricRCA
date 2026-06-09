"""drilldown_dimension tool: dimension contribution candidates with E2 evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from metric_rca.agent.tools.schemas import DrilldownDimensionArgs, ToolResult
from metric_rca.domain.models import Evidence, Observation, SQLPlan
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
    run_error = _run_context_error(repository, args.run_id, args.metric_id, args.target_date)
    if run_error:
        return _error(action, run_error, "run_id is not an active matching run")
    if not _current_run_guarded_evidence(repository, args.run_id, args.evidence_ids, {"E1"}):
        return _error(action, "EVIDENCE_MISSING", "guard-passed current-run evidence is required")
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
        return _error(action, exc.code, str(exc))
    except QuerySpecError as exc:
        return _error(action, exc.code, str(exc))
    if current_plan.guard_status != "passed" or baseline_plan.guard_status != "passed":
        return _error(action, "SQL_GUARD_REJECTED", "renderer output failed SQLGuard")

    current = repository.execute_plan(current_plan, run_id=args.run_id)
    baseline = repository.execute_plan(baseline_plan, run_id=args.run_id)
    attribution = compute_dimension_contribution(
        metric_definition=metric_definition,
        dimension=args.dimension,
        current_rows=current.rows,
        baseline_rows=baseline.rows,
        evidence_ids=args.evidence_ids,
    )
    if not attribution.ok:
        return _error(action, attribution.error_code or "ATTRIBUTION_COVERAGE_LOW", "attribution coverage low")

    result_summary = {
        "metric_id": args.metric_id,
        "dimension": args.dimension,
        "input_evidence_ids": args.evidence_ids,
        "query_sources": _query_sources(current_plan=current_plan, baseline_plan=baseline_plan),
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
    repository.create_evidence(_evidence_row(args.run_id, evidence))
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


def _query_sources(*, current_plan: SQLPlan, baseline_plan: SQLPlan) -> dict[str, Any]:
    return {
        "current_sql_hash": current_plan.sql_hash,
        "baseline_sql_hash": baseline_plan.sql_hash,
        "current_sql": current_plan.sql,
        "baseline_sql": baseline_plan.sql,
        "current_params": {key: str(value) for key, value in current_plan.params.items()},
        "baseline_params": {key: str(value) for key, value in baseline_plan.params.items()},
    }

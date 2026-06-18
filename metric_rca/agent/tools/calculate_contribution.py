"""calculate_contribution tool: E4 final contribution from current-run evidence and fresh guarded queries."""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any

from metric_rca.agent.tools.runtime import (
    ToolRuntimeError,
    current_run_guarded_evidence,
    current_run_guarded_evidence_hint,
    evidence_row,
    execute_guarded_plan,
    persist_evidence,
    query_sources,
    run_context_error,
    runtime_error,
    tool_error,
)
from metric_rca.agent.tools.schemas import CalculateContributionArgs, ToolResult
from metric_rca.business.policy_registry import root_cause_type_for_metric_dimension
from metric_rca.domain.enums import RootCauseType
from metric_rca.domain.models import ContributionSet, Evidence, Observation, RootCauseCandidate
from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql
from metric_rca.services.attribution_service import (
    compute_dimension_contribution,
    compute_gmv_decomposition,
    compute_net_gmv_components,
)
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
        evidence_hint = current_run_guarded_evidence_hint(repository, args.run_id, ["E1", "E2", "E3"])
        retry_hint = evidence_hint or [f"{args.run_id}:E1", f"{args.run_id}:E2", f"{args.run_id}:E3"]
        return tool_error(
            action,
            "EVIDENCE_MISSING",
            (
                f"guard-passed current-run E1, E2, and E3 are required; "
                "copy the exact E1, E2-family, and E3-family evidence_ids from prior tool output, "
                f"then retry with evidence_ids {retry_hint}"
            ),
        )
    evidence_alias = args.evidence_alias
    evidence_id = f"{args.run_id}:{evidence_alias}"
    existing = _existing_contribution_result(args, repository=repository)
    if existing is not None:
        return existing
    renderer = renderer or SQLRenderer()
    filters = {**args.filters}

    if filters.get(args.dimension) not in {None, args.element}:
        return tool_error(
            action,
            "QUERY_SPEC_INVALID",
            "filters conflict with selected contribution element",
        )
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
    current_run_evidence = [*args.evidence_ids, evidence_id]
    anomaly_direction = _anomaly_direction(args.run_id, repository=repository)
    attribution = compute_dimension_contribution(
        metric_definition=metric_definition,
        dimension=args.dimension,
        current_rows=current.rows,
        baseline_rows=baseline.rows,
        evidence_ids=current_run_evidence,
        anomaly_direction=anomaly_direction,
    )
    if not attribution.ok:
        return tool_error(
            action,
            attribution.error_code or "ATTRIBUTION_COVERAGE_LOW",
            "attribution coverage low",
            sql_count=2,
        )

    selected_candidate = _candidate_for_selected_element(
        candidates=attribution.candidates,
        dimension=args.dimension,
        element=args.element,
    )
    if selected_candidate is None:
        return tool_error(
            action,
            "ATTRIBUTION_COVERAGE_LOW",
            "selected element is not an attributed candidate",
            sql_count=2,
        )
    selected_candidate = _with_selected_signal_severity(
        selected_candidate,
        args=args,
        repository=repository,
    )
    attribution_candidates = [
        selected_candidate if candidate.dimension == args.dimension and str(candidate.element) == str(args.element) else candidate
        for candidate in attribution.candidates
    ]

    result_summary = {
        "metric_id": args.metric_id,
        "dimension": args.dimension,
        "element": args.element,
        "input_evidence_ids": args.evidence_ids,
        "query_sources": query_sources(current_plan=current_plan, baseline_plan=baseline_plan),
        "selected_candidate": selected_candidate.model_dump(mode="json"),
        "candidates": [candidate.model_dump(mode="json") for candidate in attribution_candidates],
    }
    sql_count = 2
    if args.metric_id == "gmv":
        try:
            factor_summary = _gmv_factor_decomposition(
                repository=repository,
                renderer=renderer,
                run_id=args.run_id,
                target_date=args.target_date,
                dimension=args.dimension,
                element=args.element,
                base_filters=filters,
                contribution_current_rows=current.rows,
                contribution_baseline_rows=baseline.rows,
            )
        except ToolRuntimeError as exc:
            return runtime_error(action, exc)
        except QuerySpecError as exc:
            return tool_error(action, exc.code, str(exc))
        except ValueError as exc:
            code = str(exc)
            if code in {"NO_CURRENT_DATA", "INSUFFICIENT_BASELINE_DATA"}:
                return tool_error(action, code, "factor decomposition failed")
            raise
        if factor_summary["decomposition"]["largest_drop_factor"] == "aov" and anomaly_direction != "increase":
            selected_candidate = selected_candidate.model_copy(update={"root_cause_type": RootCauseType.AOV_DROP.value})
            attribution_candidates = [
                selected_candidate
                if candidate.dimension == args.dimension and str(candidate.element) == str(args.element)
                else candidate
                for candidate in attribution_candidates
            ]
            result_summary["selected_candidate"] = selected_candidate.model_dump(mode="json")
            result_summary["candidates"] = [candidate.model_dump(mode="json") for candidate in attribution_candidates]
        result_summary["decomposition"] = factor_summary["decomposition"]
        result_summary["factor_query_sources"] = factor_summary["query_sources"]
        sql_count += int(factor_summary["sql_count"])
    elif args.metric_id == "net_gmv":
        try:
            factor_summary = _net_gmv_factor_decomposition(
                repository=repository,
                renderer=renderer,
                run_id=args.run_id,
                target_date=args.target_date,
                dimension=args.dimension,
                element=args.element,
                base_filters=filters,
            )
        except ToolRuntimeError as exc:
            return runtime_error(action, exc)
        except QuerySpecError as exc:
            return tool_error(action, exc.code, str(exc))
        except ValueError as exc:
            code = str(exc)
            if code in {"NO_CURRENT_DATA", "INSUFFICIENT_BASELINE_DATA"}:
                return tool_error(action, code, "factor decomposition failed")
            raise
        if factor_summary["decomposition"]["largest_driver"] == "refund_increase":
            selected_candidate = selected_candidate.model_copy(
                update={"root_cause_type": RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value}
            )
            attribution_candidates = [
                selected_candidate
                if candidate.dimension == args.dimension and str(candidate.element) == str(args.element)
                else candidate
                for candidate in attribution_candidates
            ]
            result_summary["selected_candidate"] = selected_candidate.model_dump(mode="json")
            result_summary["candidates"] = [candidate.model_dump(mode="json") for candidate in attribution_candidates]
        result_summary["net_gmv_decomposition"] = factor_summary["decomposition"]
        result_summary["net_gmv_chain"] = factor_summary["net_gmv_chain"]
        result_summary["factor_query_sources"] = factor_summary["query_sources"]
        sql_count += int(factor_summary["sql_count"])
    else:
        result_summary["metric_contribution"] = _metric_contribution_summary(
            metric_id=args.metric_id,
            dimension=args.dimension,
            element=args.element,
            coverage=attribution.coverage,
        )
    result_summary = _with_canonical_contribution_set(
        result_summary=result_summary,
        selected_candidate=selected_candidate,
        candidates=attribution_candidates,
        evidence_ids=current_run_evidence,
        dimension=args.dimension,
    )
    evidence = Evidence(
        evidence_id=evidence_id,
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
        evidence_alias=evidence_alias,
        candidates=attribution_candidates,
        sql_count=sql_count,
    )


def _existing_contribution_result(args: CalculateContributionArgs, *, repository: Any) -> ToolResult | None:
    evidence_id = f"{args.run_id}:{args.evidence_alias}"
    row = repository.get_evidence(run_id=args.run_id, evidence_id=evidence_id)
    if row is None or row.get("guard_status") != "passed":
        return None
    summary = row.get("result_summary")
    if not isinstance(summary, dict):
        return _existing_e4_mismatch_result(evidence_id, args.evidence_alias)
    if (
        summary.get("metric_id") != args.metric_id
        or summary.get("dimension") != args.dimension
        or str(summary.get("element")) != str(args.element)
    ):
        return _existing_e4_mismatch_result(evidence_id, args.evidence_alias)
    if [str(item) for item in summary.get("input_evidence_ids", [])] != [str(item) for item in args.evidence_ids]:
        return _existing_e4_mismatch_result(evidence_id, args.evidence_alias)
    contribution_set = summary.get("contribution_set")
    if not isinstance(contribution_set, dict):
        return _existing_e4_mismatch_result(evidence_id, args.evidence_alias)
    candidates = ContributionSet.model_validate(contribution_set).candidates
    return ToolResult(
        observation=Observation(
            action_name="calculate_contribution",
            ok=True,
            payload=summary,
            evidence_ids=[evidence_id],
        ),
        evidence_alias=args.evidence_alias,
        candidates=candidates,
    )


def _with_canonical_contribution_set(
    *,
    result_summary: dict[str, Any],
    selected_candidate: RootCauseCandidate,
    candidates: list[RootCauseCandidate],
    evidence_ids: list[str],
    dimension: str,
) -> dict[str, Any]:
    contribution_set = ContributionSet(
        selected_candidate=selected_candidate,
        candidates=candidates,
        evidence_ids=_ordered_unique(evidence_ids),
        factor_graph=_factor_graph_from_summary(result_summary),
        selection_evidence_id=_selection_evidence_id(evidence_ids=evidence_ids, dimension=dimension),
    )
    result_summary = dict(result_summary)
    result_summary["contribution_set"] = contribution_set.model_dump(mode="json")
    result_summary["selected_candidate"] = contribution_set.selected_candidate.model_dump(mode="json")
    result_summary["candidates"] = [candidate.model_dump(mode="json") for candidate in contribution_set.candidates]
    return result_summary


def _factor_graph_from_summary(result_summary: dict[str, Any]) -> dict[str, Any]:
    factor_graph: dict[str, Any] = {}
    for key in ("decomposition", "net_gmv_decomposition", "net_gmv_chain", "metric_contribution"):
        if key in result_summary:
            factor_graph[key] = result_summary[key]
    return factor_graph


def _selection_evidence_id(*, evidence_ids: list[str], dimension: str) -> str | None:
    for evidence_id in evidence_ids:
        alias = str(evidence_id).split(":", maxsplit=1)[-1]
        if alias == f"E_select_{dimension}" or alias.startswith(f"E_select_{dimension}_"):
            return str(evidence_id)
    return None


def _ordered_unique(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = str(value)
        if text not in unique:
            unique.append(text)
    return unique


def _existing_e4_mismatch_result(evidence_id: str, evidence_alias: str = "E4") -> ToolResult:
    return ToolResult(
        observation=Observation(
            action_name="calculate_contribution",
            ok=False,
            error_code="E4_ALREADY_EXISTS",
            message="E4 evidence is already persisted for this run; call rank_root_causes with the existing E4.",
            evidence_ids=[evidence_id],
        ),
        evidence_alias=evidence_alias,
    )


def _gmv_factor_decomposition(
    *,
    repository: Any,
    renderer: SQLRenderer,
    run_id: str,
    target_date,
    dimension: str,
    element: str,
    base_filters: dict[str, str],
    contribution_current_rows: list[dict[str, Any]],
    contribution_baseline_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    filters = _selected_element_filters(base_filters=base_filters, dimension=dimension, element=element)
    current_values: dict[str, float] = {
        "gmv": _selected_metric_value(contribution_current_rows, dimension, element)
    }
    baseline_values: dict[str, float] = {
        "gmv": _selected_baseline_mean(contribution_baseline_rows, dimension, element)
    }
    factor_query_sources: dict[str, dict[str, Any]] = {}
    for metric_id in ["uv", "pay_cvr"]:
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
        "sql_count": len(factor_query_sources) * 2,
    }


def _net_gmv_factor_decomposition(
    *,
    repository: Any,
    renderer: SQLRenderer,
    run_id: str,
    target_date,
    dimension: str,
    element: str,
    base_filters: dict[str, str],
) -> dict[str, Any]:
    filters = _selected_element_filters(base_filters=base_filters, dimension=dimension, element=element)
    current_values: dict[str, float] = {}
    baseline_values: dict[str, float] = {}
    factor_query_sources: dict[str, dict[str, Any]] = {}
    for metric_id in ["gmv", "net_gmv"]:
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

    current_components = compute_net_gmv_components(
        gmv=current_values["gmv"],
        refund=current_values["gmv"] - current_values["net_gmv"],
    )
    baseline_components = compute_net_gmv_components(
        gmv=baseline_values["gmv"],
        refund=baseline_values["gmv"] - baseline_values["net_gmv"],
    )
    gmv_drop = _relative_drop(
        current=current_components["gmv"],
        baseline=baseline_components["gmv"],
    )
    refund_increase = _relative_increase(
        current=current_components["refund"],
        baseline=baseline_components["refund"],
    )
    drivers = {
        "gmv_drop": gmv_drop,
        "refund_increase": refund_increase,
    }
    largest_driver = max(drivers, key=drivers.get)
    return {
        "decomposition": {
            "current": current_components,
            "baseline": baseline_components,
            "relative_drops_or_increases": drivers,
            "largest_driver": largest_driver,
        },
        "net_gmv_chain": {
            "model": "net_gmv_chain",
            "first_split": {
                "gmv_delta": baseline_components["gmv"] - current_components["gmv"],
                "refund_delta": current_components["refund"] - baseline_components["refund"],
                "net_gmv_delta": baseline_components["net_gmv"] - current_components["net_gmv"],
            },
            "dominant_side": "gmv" if largest_driver == "gmv_drop" else "refund",
            "continued_path": "uv_pay_cvr_aov" if largest_driver == "gmv_drop" else "refund_dimension_drilldown",
        },
        "query_sources": factor_query_sources,
        "sql_count": len(factor_query_sources) * 2,
    }


def _selected_element_filters(
    *,
    base_filters: dict[str, str],
    dimension: str,
    element: str,
) -> dict[str, str]:
    existing = base_filters.get(dimension)
    if existing is not None and existing != element:
        raise QuerySpecError(
            "QUERY_SPEC_INVALID",
            "filters conflict with selected contribution element",
        )
    return {**base_filters, dimension: element}


def _candidate_for_selected_element(
    *,
    candidates: list[Any],
    dimension: str,
    element: str,
) -> Any | None:
    for candidate in candidates:
        if candidate.dimension == dimension and str(candidate.element) == str(element):
            return candidate
    return None


def _anomaly_direction(run_id: str, *, repository: Any) -> str | None:
    row = repository.get_evidence(run_id=run_id, evidence_id=f"{run_id}:E1")
    summary = row.get("result_summary") if isinstance(row, dict) else None
    if not isinstance(summary, dict):
        return None
    delta = summary.get("delta")
    if delta is None:
        return None
    numeric_delta = float(delta)
    if numeric_delta > 0:
        return "increase"
    if numeric_delta < 0:
        return "decrease"
    return None


def _with_selected_signal_severity(candidate: Any, *, args: CalculateContributionArgs, repository: Any) -> Any:
    signal_summary = _selected_signal_summary(args=args, repository=repository)
    if signal_summary is None:
        return candidate
    updates: dict[str, Any] = {}
    signal_type = signal_summary.get("signal_type")
    if isinstance(signal_type, str) and signal_type:
        updates["root_cause_type"] = root_cause_type_for_metric_dimension(
            metric_id=args.metric_id,
            dimension=args.dimension,
            signal_type=signal_type,
        )
    delta_pct = signal_summary.get("delta_pct")
    try:
        signal_severity = min(1.0, abs(float(delta_pct)))
    except (TypeError, ValueError):
        return candidate.model_copy(update=updates) if updates else candidate
    signal_severity = max(float(candidate.signal_severity), signal_severity)
    updates.update(
        {
            "signal_severity": signal_severity,
            "eng_confidence": (
                float(candidate.contribution_pct)
                * signal_severity
                * float(candidate.evidence_support)
                * float(candidate.reflection_factor)
            ),
        }
    )
    return candidate.model_copy(update=updates)


def _selected_signal_summary(*, args: CalculateContributionArgs, repository: Any) -> dict[str, Any] | None:
    for evidence_id in args.evidence_ids:
        if not str(evidence_id).startswith(f"{args.run_id}:E3"):
            continue
        row = repository.get_evidence(run_id=args.run_id, evidence_id=str(evidence_id))
        summary = row.get("result_summary") if isinstance(row, dict) else None
        if not isinstance(summary, dict):
            continue
        if summary.get("dimension") == args.dimension and str(summary.get("element")) == str(args.element):
            return summary
    return None


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


def _selected_metric_value(rows: list[dict[str, Any]], dimension: str, element: str) -> float:
    for row in rows:
        if str(row.get(dimension)) == str(element):
            return _single_metric_value([row])
    raise ValueError("NO_CURRENT_DATA")


def _selected_baseline_mean(rows: list[dict[str, Any]], dimension: str, element: str) -> float:
    selected = [row for row in rows if str(row.get(dimension)) == str(element)]
    return _mean_metric_value(selected)


def _relative_drop(*, current: float, baseline: float) -> float:
    return max(0.0, (baseline - current) / baseline) if baseline else 0.0


def _relative_increase(*, current: float, baseline: float) -> float:
    return max(0.0, (current - baseline) / baseline) if baseline else max(0.0, current)

"""select_signal_element tool: auditable E_select evidence for dynamic discovery."""

from __future__ import annotations

from datetime import datetime, timezone
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
from metric_rca.agent.tools.schemas import SelectSignalElementArgs, ToolResult
from metric_rca.config.settings import Settings, get_settings
from metric_rca.domain.models import Evidence, MetricDefinition, Observation
from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql
from metric_rca.services.metric_service import MetricServiceError


def select_signal_element(
    args: SelectSignalElementArgs,
    *,
    repository: Any,
    metric_service: Any,
    renderer: SQLRenderer | None = None,
    settings: Settings | None = None,
) -> ToolResult:
    action = "select_signal_element"
    run_error = run_context_error(repository, args.run_id, args.metric_id, args.target_date)
    if run_error:
        return tool_error(action, run_error, "run_id is not an active matching run")
    required_e2_alias = f"E2_{args.dimension}"
    if not current_run_guarded_evidence(repository, args.run_id, args.evidence_ids, {"E1", required_e2_alias}):
        evidence_hint = current_run_guarded_evidence_hint(repository, args.run_id, ["E1", required_e2_alias])
        retry_hint = evidence_hint or [f"{args.run_id}:E1", f"{args.run_id}:{required_e2_alias}"]
        return tool_error(
            action,
            "EVIDENCE_MISSING",
            (
                f"select_signal_element for dimension={args.dimension} requires guard-passed current-run "
                f"E1 and {required_e2_alias}; copy exact evidence_ids from prior tool output, "
                f"then retry with evidence_ids {retry_hint}"
            ),
        )
    existing = _existing_selection_result(args, repository=repository)
    if existing is not None:
        return existing
    candidate_elements = _candidate_elements(args, repository=repository)
    if not candidate_elements:
        return tool_error(action, "SIGNAL_SELECTION_UNRESOLVED", "no drilldown candidates available for selection")

    renderer = renderer or SQLRenderer()
    settings = settings or get_settings()
    signal_metric_id = _signal_metric_id(args.metric_id, args.signal_type, settings=settings)
    if signal_metric_id is None:
        return tool_error(action, "CONFIG_INVALID", f"signal metric missing: {args.signal_type}")

    signal_hint = "campaign" if args.signal_type == "campaign" else "metric"
    try:
        metric_definition = metric_service.get_metric_definition(signal_metric_id)
        current_spec = build_query_spec(
            metric_id=signal_metric_id,
            start_date=args.target_date,
            end_date=args.target_date,
            group_by=[args.dimension],
            filters=args.filters,
            purpose="signal",
            signal_type=signal_hint,
        )
        baseline_spec = build_query_spec(
            metric_id=signal_metric_id,
            start_date=args.target_date,
            end_date=args.target_date,
            group_by=[args.dimension],
            filters=args.filters,
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
    sql_count = 2

    scores = _score_candidates(
        candidate_elements=candidate_elements,
        dimension=args.dimension,
        current_rows=list(current.rows),
        baseline_rows=list(baseline.rows),
        metric_definition=metric_definition,
    )
    if not scores:
        return tool_error(
            action,
            "SIGNAL_SELECTION_UNRESOLVED",
            "signal selection found no scored drilldown candidate",
            sql_count=2,
        )
    selected = _select_element(scores=scores, element_selection=args.element_selection)
    if selected is None:
        return tool_error(
            action,
            "SIGNAL_SELECTION_UNRESOLVED",
            "signal selection found no selected element",
            sql_count=2,
        )

    evidence_alias = _selection_evidence_alias(args)
    result_summary = {
        "metric_id": args.metric_id,
        "signal_type": args.signal_type,
        "signal_metric_id": signal_metric_id,
        "dimension": args.dimension,
        "filters": args.filters,
        "input_evidence_ids": args.evidence_ids,
        "element_selection": args.element_selection,
        "candidate_count": len(candidate_elements),
        "candidate_scores": scores,
        "selected_element": selected["element"],
        "selection_reason": selected["selection_reason"],
        "query_sources": query_sources(current_plan=current_plan, baseline_plan=baseline_plan),
    }
    evidence = Evidence(
        evidence_id=f"{args.run_id}:{evidence_alias}",
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
        return runtime_error(action, exc, sql_count=sql_count)
    return ToolResult(
        observation=Observation(
            action_name=action,
            ok=True,
            payload=result_summary,
            evidence_ids=[evidence.evidence_id],
        ),
        evidences=[evidence],
        evidence_alias=evidence_alias,
        sql_count=sql_count,
    )


def _existing_selection_result(args: SelectSignalElementArgs, *, repository: Any) -> ToolResult | None:
    evidence_alias = _selection_evidence_alias(args)
    evidence_id = f"{args.run_id}:{evidence_alias}"
    row = repository.get_evidence(run_id=args.run_id, evidence_id=evidence_id)
    if row is None or row.get("guard_status") != "passed":
        return None
    summary = row.get("result_summary")
    if not isinstance(summary, dict):
        return None
    if (
        summary.get("metric_id") != args.metric_id
        or summary.get("signal_type") != args.signal_type
        or summary.get("dimension") != args.dimension
        or {str(key): str(value) for key, value in (summary.get("filters") or {}).items()}
        != {str(key): str(value) for key, value in args.filters.items()}
        or [str(item) for item in summary.get("input_evidence_ids", [])] != [str(item) for item in args.evidence_ids]
    ):
        return None
    return ToolResult(
        observation=Observation(
            action_name="select_signal_element",
            ok=True,
            payload=summary,
            evidence_ids=[evidence_id],
        ),
        evidence_alias=evidence_alias,
        sql_count=0,
    )


def _selection_evidence_alias(args: SelectSignalElementArgs) -> str:
    return args.evidence_alias or f"E_select_{args.dimension}"


def _candidate_elements(args: SelectSignalElementArgs, *, repository: Any) -> list[str]:
    row = repository.get_evidence(run_id=args.run_id, evidence_id=f"{args.run_id}:E2_{args.dimension}")
    summary = row.get("result_summary") if isinstance(row, dict) else None
    candidates = summary.get("candidates") if isinstance(summary, dict) else None
    if not isinstance(candidates, list):
        return []
    elements: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("element") is not None:
            elements.append(str(candidate["element"]))
    return elements


def _signal_metric_id(metric_id: str, signal_type: str, *, settings: Settings) -> str | None:
    if signal_type == "interaction":
        return metric_id
    return settings.signal_metric_by_type.get(signal_type)


def _score_candidates(
    *,
    candidate_elements: list[str],
    dimension: str,
    current_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    metric_definition: MetricDefinition,
) -> list[dict[str, Any]]:
    current_by_element: dict[str, float] = {}
    baseline_values: dict[str, list[float]] = {}
    for row in current_rows:
        element = row.get(dimension)
        metric_value = row.get("metric_value")
        if element is not None and metric_value is not None:
            current_by_element[str(element)] = float(metric_value)
    for row in baseline_rows:
        element = row.get(dimension)
        metric_value = row.get("metric_value")
        if element is not None and metric_value is not None:
            baseline_values.setdefault(str(element), []).append(float(metric_value))

    higher_is_better = bool(metric_definition.higher_is_better)
    scores: list[dict[str, Any]] = []
    for element in candidate_elements:
        if element not in current_by_element or element not in baseline_values or not baseline_values[element]:
            continue
        current_value = current_by_element[element]
        baseline_value = sum(baseline_values[element]) / len(baseline_values[element])
        delta = current_value - baseline_value
        delta_pct = delta / baseline_value if baseline_value else 0.0
        bad_delta_pct = -delta_pct if higher_is_better else delta_pct
        is_bad_direction = bad_delta_pct > 0
        signal_score = bad_delta_pct if is_bad_direction else -abs(delta_pct)
        scores.append(
            {
                "element": element,
                "current_value": current_value,
                "baseline_value": baseline_value,
                "delta": delta,
                "delta_pct": delta_pct,
                "is_bad_direction": is_bad_direction,
                "signal_score": signal_score,
            }
        )
    return scores


def _select_element(*, scores: list[dict[str, Any]], element_selection: str) -> dict[str, Any] | None:
    if not scores:
        return None
    if element_selection == "signal_level":
        selected = max(scores, key=lambda item: (float(item["current_value"]), float(item["signal_score"])))
    elif element_selection == "top_candidate":
        selected = scores[0]
    else:
        selected = max(scores, key=lambda item: (int(bool(item["is_bad_direction"])), float(item["signal_score"])))
    return {**selected, "selection_reason": element_selection}

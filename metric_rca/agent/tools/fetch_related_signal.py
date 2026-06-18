"""fetch_related_signal tool: E3 evidence for campaign, inventory, conversion, and refund-quality signals."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any

from metric_rca.agent.evidence_aliases import e3_alias_for_dimension
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
from metric_rca.agent.tools.schemas import FetchRelatedSignalArgs, ToolResult
from metric_rca.agent.tools.signal_policy import select_signal_type, select_signal_type_for_metric_dimension
from metric_rca.business.policy_registry import root_cause_type_for_metric_dimension
from metric_rca.config.settings import Settings, get_settings
from metric_rca.domain.models import Evidence, Observation
from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql
from metric_rca.services.anomaly_service import detect_anomaly_from_rows
from metric_rca.services.metric_service import MetricServiceError

MAX_SIGNAL_ELEMENT_TOKEN_LENGTH = 12


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
        evidence_hint = current_run_guarded_evidence_hint(repository, args.run_id, ["E1", "E2"])
        retry_hint = evidence_hint or [f"{args.run_id}:E1", f"{args.run_id}:E2"]
        return tool_error(
            action,
            "EVIDENCE_MISSING",
            (
                f"guard-passed current-run E1 and E2 are required; "
                "copy the exact E1 and E2-family evidence_ids from prior tool output, "
                f"then retry with evidence_ids {retry_hint}"
            ),
        )
    required_e2_alias = f"E2_{args.dimension}"
    if not current_run_guarded_evidence(repository, args.run_id, args.evidence_ids, {"E1", required_e2_alias}):
        evidence_hint = current_run_guarded_evidence_hint(repository, args.run_id, ["E1", required_e2_alias])
        retry_hint = evidence_hint or [f"{args.run_id}:E1", f"{args.run_id}:{required_e2_alias}"]
        return tool_error(
            action,
            "EVIDENCE_MISSING",
            (
                f"fetch_related_signal for dimension={args.dimension} requires guard-passed current-run "
                f"{required_e2_alias}; copy the exact E1/{required_e2_alias} evidence_ids from prior "
                f"tool output, then retry with evidence_ids {retry_hint}"
            ),
        )
    try:
        expected_signal_type = select_signal_type_for_metric_dimension(
            metric_id=args.metric_id,
            dimension=args.dimension,
        )
    except ValueError as exc:
        return tool_error(action, str(exc), "signal policy missing for metric/dimension")
    if args.signal_type != expected_signal_type and not _is_allowed_explicit_signal_type(args):
        return tool_error(
            action,
            "QUERY_SPEC_INVALID",
            f"signal_type must be {expected_signal_type} for metric_id={args.metric_id} dimension={args.dimension}",
        )
    filters = {str(key): str(value) for key, value in args.filters.items()}
    if filters.get(args.dimension) not in {None, args.element}:
        return tool_error(
            action,
            "QUERY_SPEC_INVALID",
            "filters conflict with selected signal dimension/element",
        )
    existing = _existing_signal_result(args, repository=repository)
    if existing is not None:
        return existing
    renderer = renderer or SQLRenderer()
    settings = settings or get_settings()
    signal_metric_id = _signal_metric_id(args.metric_id, args.signal_type, settings=settings)
    if signal_metric_id is None:
        return tool_error(action, "CONFIG_INVALID", f"signal metric missing: {args.signal_type}")
    filters = {**filters, args.dimension: args.element}
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
        "filters": filters,
        "input_evidence_ids": args.evidence_ids,
        "query_sources": query_sources(current_plan=current_plan, baseline_plan=baseline_plan),
        **signal.result_summary,
    }
    evidence_alias = _signal_evidence_alias(args)
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
        evidence_alias=evidence_alias,
        sql_count=2,
    )


def _existing_signal_result(args: FetchRelatedSignalArgs, *, repository: Any) -> ToolResult | None:
    for evidence_alias in [_signal_evidence_alias(args), "E3"]:
        evidence_id = f"{args.run_id}:{evidence_alias}"
        row = repository.get_evidence(run_id=args.run_id, evidence_id=evidence_id)
        if row is None or row.get("guard_status") != "passed":
            continue
        summary = row.get("result_summary")
        if not isinstance(summary, dict):
            continue
        if (
            summary.get("signal_type") != args.signal_type
            or summary.get("dimension") != args.dimension
            or str(summary.get("element")) != str(args.element)
        ):
            continue
        if [str(item) for item in summary.get("input_evidence_ids", [])] != [str(item) for item in args.evidence_ids]:
            continue
        return ToolResult(
            observation=Observation(
                action_name="fetch_related_signal",
                ok=True,
                payload=summary,
                evidence_ids=[evidence_id],
                error_code=summary.get("error_code"),
            ),
            evidence_alias=evidence_alias,
        )
    return None


def _signal_evidence_alias(args: FetchRelatedSignalArgs) -> str:
    dimension_prefix = e3_alias_for_dimension(args.dimension)
    dimension_token = (
        dimension_prefix.removeprefix("E3_")
        if dimension_prefix is not None
        else _alias_token(args.dimension)
    )
    return f"E3_{dimension_token}_{_alias_token(args.element)}"


def _signal_metric_id(metric_id: str, signal_type: str, *, settings: Settings) -> str | None:
    if signal_type == "interaction":
        return metric_id
    return settings.signal_metric_by_type.get(signal_type)


def _is_allowed_explicit_signal_type(args: FetchRelatedSignalArgs) -> bool:
    try:
        root_cause_type = root_cause_type_for_metric_dimension(
            metric_id=args.metric_id,
            dimension=args.dimension,
            signal_type=args.signal_type,
        )
        expected = select_signal_type(
            metric_id=args.metric_id,
            dimension=args.dimension,
            root_cause_type=root_cause_type,
        )
    except ValueError:
        return False
    return expected == args.signal_type


def _alias_token(value: str) -> str:
    token = "".join(char if char.isalnum() or char == "_" else "_" for char in str(value).lower())
    token = token.strip("_") or "value"
    if len(token) <= MAX_SIGNAL_ELEMENT_TOKEN_LENGTH:
        return token
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:4]
    return f"{token[:7]}_{digest}"

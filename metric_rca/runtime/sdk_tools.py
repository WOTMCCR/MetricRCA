"""OpenAI Agents SDK tool registry and deterministic tool executor."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from pydantic import ValidationError

from metric_rca.domain.models import Observation, StrictModel
from metric_rca.runtime.dependencies import RuntimeDependencies
from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_models import RcaAction
from metric_rca.runtime.ranking import rank_from_persisted_e4
from metric_rca.runtime.run_context import RunContext
from metric_rca.runtime.tool_models import MetricRCAToolHandler, ToolExecutionResult


RCA_TOOL_NAMES = frozenset(
    {"detect_anomaly", "drilldown_dimension", "fetch_related_signal", "calculate_contribution", "rank_root_causes"}
)
EVIDENCE_INPUT_ACTIONS = frozenset(
    {"drilldown_dimension", "fetch_related_signal", "calculate_contribution"}
)
INTERNAL_ACTION_ARG_NAMES = frozenset({"element_selection"})
ELEMENT_SELECTION_SIGNAL_ANOMALY = "signal_anomaly"
ELEMENT_SELECTION_SIGNAL_LEVEL = "signal_level"


class RankRootCausesArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date


class ToolExecutor:
    def __init__(
        self,
        *,
        dependencies: RuntimeDependencies,
        handlers: Mapping[str, MetricRCAToolHandler] | None = None,
    ) -> None:
        self._dependencies = dependencies
        self._handlers = dict(handlers or build_default_tool_handlers())

    def execute(self, ctx: RunContext, action: RcaAction, evidence_graph: EvidenceGraph) -> ToolExecutionResult:
        handler = self._handlers.get(action.kind)
        if handler is None:
            return _error(action.kind, "TOOL_NOT_REGISTERED", f"tool is not registered: {action.kind}")

        resolved_args, error = _resolve_action_args(ctx, action, evidence_graph, self._dependencies)
        if error is not None:
            return error

        try:
            typed_args = handler.args_model.model_validate(resolved_args)
        except ValidationError as exc:
            return _error(action.kind, "ACTION_SCHEMA_INVALID", exc.errors()[0]["msg"])

        result = handler.call(typed_args, self._dependencies)
        return _coerce_tool_result(result)


def build_default_tool_handlers() -> dict[str, MetricRCAToolHandler]:
    from metric_rca.agent.tools.calculate_contribution import calculate_contribution
    from metric_rca.agent.tools.detect_anomaly import detect_anomaly
    from metric_rca.agent.tools.drilldown_dimension import drilldown_dimension
    from metric_rca.agent.tools.fetch_related_signal import fetch_related_signal
    from metric_rca.agent.tools.schemas import (
        CalculateContributionArgs,
        DetectAnomalyArgs,
        DrilldownDimensionArgs,
        FetchRelatedSignalArgs,
    )

    def _detect(args: DetectAnomalyArgs, dependencies: Any) -> Any:
        return detect_anomaly(
            args,
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
            settings=dependencies.settings,
        )

    def _drilldown(args: DrilldownDimensionArgs, dependencies: Any) -> Any:
        return drilldown_dimension(
            args,
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
        )

    def _fetch(args: FetchRelatedSignalArgs, dependencies: Any) -> Any:
        return fetch_related_signal(
            args,
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
            settings=dependencies.settings,
        )

    def _calculate(args: CalculateContributionArgs, dependencies: Any) -> Any:
        return calculate_contribution(
            args,
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
        )

    def _rank(args: RankRootCausesArgs, dependencies: RuntimeDependencies) -> ToolExecutionResult:
        return rank_from_persisted_e4(
            repository=dependencies.repository,
            settings=dependencies.settings,
            run_id=args.run_id,
            metric_id=args.metric_id,
            target_date=args.target_date,
        )

    return {
        "detect_anomaly": MetricRCAToolHandler(args_model=DetectAnomalyArgs, call=_detect),
        "drilldown_dimension": MetricRCAToolHandler(args_model=DrilldownDimensionArgs, call=_drilldown),
        "fetch_related_signal": MetricRCAToolHandler(args_model=FetchRelatedSignalArgs, call=_fetch),
        "calculate_contribution": MetricRCAToolHandler(args_model=CalculateContributionArgs, call=_calculate),
        "rank_root_causes": MetricRCAToolHandler(args_model=RankRootCausesArgs, call=_rank),
    }


def _resolve_action_args(
    ctx: RunContext,
    action: RcaAction,
    evidence_graph: EvidenceGraph,
    dependencies: Any,
) -> tuple[dict[str, Any], ToolExecutionResult | None]:
    args = dict(action.args)
    args["run_id"] = ctx.run_id
    if action.kind in EVIDENCE_INPUT_ACTIONS and "evidence_ids" not in args:
        args["evidence_ids"] = _required_evidence_ids(action, evidence_graph)
    if action.dynamic and args.get("element") is None:
        element, resolution_error = _dynamic_candidate_element(ctx, args, dependencies)
        if resolution_error is not None:
            return args, resolution_error
        if element is None:
            return args, _error(
                action.kind,
                "DYNAMIC_ACTION_UNRESOLVED",
                f"action {action.action_id} could not resolve element from top drilldown candidate",
            )
        args["element"] = element
    for name in INTERNAL_ACTION_ARG_NAMES:
        args.pop(name, None)
    return args, None


def _dynamic_candidate_element(
    ctx: RunContext,
    args: dict[str, Any],
    dependencies: Any,
) -> tuple[str | None, ToolExecutionResult | None]:
    signal_element = _signal_evidence_element(ctx, str(args.get("dimension") or ""))
    if signal_element is not None:
        return signal_element, None
    if args.get("element_selection") == ELEMENT_SELECTION_SIGNAL_ANOMALY:
        selected, error = _top_signal_anomaly_element(ctx=ctx, args=args, dependencies=dependencies)
        if error is not None:
            return selected, error
        if selected is None:
            return None, _error(
                "fetch_related_signal",
                "SIGNAL_SELECTION_UNRESOLVED",
                "signal-anomaly element selection found no scored drilldown candidate",
            )
        return selected, None
    if args.get("element_selection") == ELEMENT_SELECTION_SIGNAL_LEVEL:
        selected, error = _top_signal_level_element(ctx=ctx, args=args, dependencies=dependencies)
        if error is not None:
            return selected, error
        if selected is None:
            return None, _error(
                "fetch_related_signal",
                "SIGNAL_SELECTION_UNRESOLVED",
                "signal-level element selection found no scored drilldown candidate",
            )
        return selected, None
    if args.get("signal_type") == "refund_quality":
        selected, error = _top_signal_level_element(ctx=ctx, args=args, dependencies=dependencies)
        if error is not None or selected is not None:
            return selected, error
    return _top_candidate_element(ctx, str(args.get("dimension") or "")), None


def _required_evidence_ids(action: RcaAction, evidence_graph: EvidenceGraph) -> list[str]:
    evidence_ids: list[str] = []
    for alias in action.requires:
        for evidence_id in evidence_graph.matching(alias):
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
    return evidence_ids


def _top_signal_anomaly_element(
    *,
    ctx: RunContext,
    args: dict[str, Any],
    dependencies: Any,
) -> tuple[str | None, ToolExecutionResult | None]:
    repository = ctx.repository
    dimension = str(args.get("dimension") or "")
    if repository is None or not dimension:
        return None, None
    candidate_elements = _candidate_elements(ctx, dimension)
    if not candidate_elements:
        return None, None
    settings = getattr(dependencies, "settings", None)
    signal_metric_by_type = getattr(settings, "signal_metric_by_type", {}) if settings is not None else {}
    signal_type = str(args.get("signal_type") or "")
    signal_metric_id = signal_metric_by_type.get(signal_type)
    if not signal_metric_id:
        return None, _error(
            "fetch_related_signal",
            "SIGNAL_POLICY_MISSING",
            f"{signal_type or 'unknown'} signal metric is not configured",
        )
    metric_service = getattr(dependencies, "metric_service", None)
    renderer = getattr(dependencies, "renderer", None)
    if metric_service is None or renderer is None:
        return None, _error(
            "fetch_related_signal",
            "CONFIG_INVALID",
            "signal element selection requires metric_service and renderer",
        )
    try:
        from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
        from metric_rca.guardrails.sql_guard import guard_sql
        from metric_rca.services.anomaly_service import detect_anomaly_from_rows
        from metric_rca.services.metric_contracts import MetricServiceError

        metric_definition = metric_service.get_metric_definition(str(signal_metric_id))
    except QuerySpecError as exc:
        return None, _error("fetch_related_signal", exc.code, str(exc))
    except MetricServiceError as exc:
        return None, _error("fetch_related_signal", exc.code, str(exc))
    except RuntimeError as exc:
        return None, _error("fetch_related_signal", _runtime_code(exc), str(exc))

    ranked: list[tuple[str, tuple[int, int, float, float]]] = []
    for element in candidate_elements:
        filters = _string_filters(args.get("filters"))
        filters[dimension] = element
        signal_hint = "campaign" if signal_type == "campaign" else "metric"
        try:
            current_spec = build_query_spec(
                metric_id=str(signal_metric_id),
                start_date=ctx.target_date,
                end_date=ctx.target_date,
                filters=filters,
                purpose="signal",
                signal_type=signal_hint,
            )
            baseline_spec = build_query_spec(
                metric_id=str(signal_metric_id),
                start_date=ctx.target_date,
                end_date=ctx.target_date,
                filters=filters,
                purpose="baseline",
                signal_type=signal_hint,
            )
            current_plan = guard_sql(renderer.render(current_spec))
            baseline_plan = guard_sql(renderer.render(baseline_spec))
            current = repository.execute_plan(current_plan, run_id=ctx.run_id)
            baseline = repository.execute_plan(baseline_plan, run_id=ctx.run_id)
        except QuerySpecError as exc:
            return None, _error("fetch_related_signal", exc.code, str(exc))
        except RuntimeError as exc:
            return None, _error("fetch_related_signal", _runtime_code(exc), str(exc))
        try:
            signal = detect_anomaly_from_rows(
                current_rows=list(getattr(current, "rows", []) or []),
                baseline_rows=list(getattr(baseline, "rows", []) or []),
                metric_definition=metric_definition,
                thresh_pct=0.10,
                z_thresh=1.0,
            )
        except ValueError as exc:
            return None, _error("fetch_related_signal", "SIGNAL_SELECTION_INVALID_ROWS", str(exc))
        if not signal.ok:
            continue
        ranked.append((element, _signal_selection_score(signal)))
    if not ranked:
        return None, None
    return max(ranked, key=lambda item: item[1])[0], None


def _signal_selection_score(signal: Any) -> tuple[int, int, float, float]:
    return (
        int(bool(signal.is_anomaly and signal.bad_direction)),
        int(bool(signal.bad_direction)),
        abs(float(signal.delta_pct or 0.0)),
        abs(float(signal.z_score or 0.0)),
    )


def _top_signal_level_element(
    *,
    ctx: RunContext,
    args: dict[str, Any],
    dependencies: Any,
) -> tuple[str | None, ToolExecutionResult | None]:
    repository = ctx.repository
    dimension = str(args.get("dimension") or "")
    if repository is None or not dimension:
        return None, None
    candidate_elements = _candidate_elements(ctx, dimension)
    if not candidate_elements:
        return None, None
    settings = getattr(dependencies, "settings", None)
    signal_metric_by_type = getattr(settings, "signal_metric_by_type", {}) if settings is not None else {}
    signal_type = str(args.get("signal_type") or "")
    signal_metric_id = signal_metric_by_type.get(signal_type)
    if not signal_metric_id:
        return None, _error(
            "fetch_related_signal",
            "SIGNAL_POLICY_MISSING",
            f"{signal_type or 'unknown'} signal metric is not configured",
        )
    renderer = getattr(dependencies, "renderer", None)
    if renderer is None:
        return None, _error(
            "fetch_related_signal",
            "CONFIG_INVALID",
            "signal element selection requires renderer",
        )
    try:
        from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
        from metric_rca.guardrails.sql_guard import guard_sql

        current_spec = build_query_spec(
            metric_id=str(signal_metric_id),
            start_date=ctx.target_date,
            end_date=ctx.target_date,
            group_by=[dimension],
            filters=_string_filters(args.get("filters")),
            purpose="current",
        )
        current_plan = guard_sql(renderer.render(current_spec))
        current = repository.execute_plan(current_plan, run_id=ctx.run_id)
    except QuerySpecError as exc:
        return None, _error("fetch_related_signal", exc.code, str(exc))
    except RuntimeError as exc:
        return None, _error("fetch_related_signal", _runtime_code(exc), str(exc))

    value_by_element: dict[str, float] = {}
    for row in getattr(current, "rows", []) or []:
        element = row.get(dimension)
        metric_value = row.get("metric_value")
        if element is None or metric_value is None:
            continue
        value_by_element[str(element)] = float(metric_value)
    ranked = [
        (element, value_by_element[element])
        for element in candidate_elements
        if element in value_by_element
    ]
    if not ranked:
        return None, None
    return max(ranked, key=lambda item: item[1])[0], None


def _top_candidate_element(ctx: RunContext, dimension: str) -> str | None:
    elements = _candidate_elements(ctx, dimension)
    return elements[0] if elements else None


def _candidate_elements(ctx: RunContext, dimension: str) -> list[str]:
    if ctx.repository is None or not dimension:
        return []
    row = ctx.repository.get_evidence(run_id=ctx.run_id, evidence_id=f"{ctx.run_id}:E2_{dimension}")
    if not isinstance(row, dict) or row.get("guard_status") != "passed":
        return []
    summary = row.get("result_summary")
    candidates = summary.get("candidates") if isinstance(summary, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return []
    elements: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("element") is not None:
            elements.append(str(candidate["element"]))
    return elements


def _signal_evidence_element(ctx: RunContext, dimension: str) -> str | None:
    if ctx.repository is None or not dimension:
        return None
    for row in ctx.repository.get_evidences(ctx.run_id):
        if not isinstance(row, dict) or row.get("guard_status") != "passed":
            continue
        evidence_id = str(row.get("evidence_id") or "")
        if not evidence_id.startswith(f"{ctx.run_id}:E3"):
            continue
        summary = row.get("result_summary")
        if not isinstance(summary, dict):
            continue
        if summary.get("dimension") == dimension and summary.get("element") is not None:
            return str(summary["element"])
    return None


def _runtime_code(exc: RuntimeError) -> str:
    message_code = str(exc).split(":", maxsplit=1)[0]
    if message_code and message_code.isupper():
        return message_code
    return "SIGNAL_QUERY_FAILED"


def _string_filters(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _coerce_tool_result(result: Any) -> ToolExecutionResult:
    if isinstance(result, ToolExecutionResult):
        return result
    observation = result.observation
    evidence_ids = list(getattr(observation, "evidence_ids", []) or [])
    if not evidence_ids:
        evidence_ids = [
            evidence.evidence_id
            for evidence in getattr(result, "evidences", [])
            if getattr(evidence, "evidence_id", None)
        ]
    return ToolExecutionResult(
        observation=observation,
        evidence_ids=evidence_ids,
        candidates=list(getattr(result, "candidates", []) or []),
    )


def _error(action_name: str, error_code: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        observation=Observation(
            action_name=action_name,
            ok=False,
            error_code=error_code,
            message=message,
        )
    )

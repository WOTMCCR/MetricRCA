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
    {
        "detect_anomaly",
        "drilldown_dimension",
        "select_signal_element",
        "fetch_related_signal",
        "calculate_contribution",
        "merge_contribution_sets",
        "rank_root_causes",
    }
)
EVIDENCE_INPUT_ACTIONS = frozenset(
    {"drilldown_dimension", "select_signal_element", "fetch_related_signal", "calculate_contribution"}
)
INTERNAL_ACTION_ARG_NAMES = frozenset({"element_selection"})
GATE_ONLY_ACTION_ARG_NAMES = frozenset({"explicit_scope_policy"})


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
    from metric_rca.agent.tools.merge_contribution_sets import merge_contribution_sets
    from metric_rca.agent.tools.select_signal_element import select_signal_element
    from metric_rca.agent.tools.schemas import (
        CalculateContributionArgs,
        DetectAnomalyArgs,
        DrilldownDimensionArgs,
        FetchRelatedSignalArgs,
        MergeContributionSetsArgs,
        SelectSignalElementArgs,
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

    def _select(args: SelectSignalElementArgs, dependencies: Any) -> Any:
        return select_signal_element(
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

    def _merge(args: MergeContributionSetsArgs, dependencies: Any) -> Any:
        return merge_contribution_sets(
            args,
            repository=dependencies.repository,
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
        "select_signal_element": MetricRCAToolHandler(args_model=SelectSignalElementArgs, call=_select),
        "fetch_related_signal": MetricRCAToolHandler(args_model=FetchRelatedSignalArgs, call=_fetch),
        "calculate_contribution": MetricRCAToolHandler(args_model=CalculateContributionArgs, call=_calculate),
        "merge_contribution_sets": MetricRCAToolHandler(args_model=MergeContributionSetsArgs, call=_merge),
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
    if action.dynamic and "element" in args and args.get("element") is None:
        element, resolution_error = _dynamic_candidate_element(ctx, action, args)
        if resolution_error is not None:
            return args, resolution_error
        if element is None:
            return args, _error(
                action.kind,
                "DYNAMIC_ACTION_UNRESOLVED",
                f"action {action.action_id} could not resolve element from top drilldown candidate",
            )
        args["element"] = element
    if action.kind != "select_signal_element":
        for name in INTERNAL_ACTION_ARG_NAMES:
            args.pop(name, None)
    for name in GATE_ONLY_ACTION_ARG_NAMES:
        args.pop(name, None)
    return args, None


def _dynamic_candidate_element(
    ctx: RunContext,
    action: RcaAction,
    args: dict[str, Any],
) -> tuple[str | None, ToolExecutionResult | None]:
    dimension = str(args.get("dimension") or "")
    selected = _selection_evidence_element(ctx, dimension, action.requires)
    if selected is not None:
        return selected, None
    return None, _error(
        action.kind,
        "DYNAMIC_ACTION_UNRESOLVED",
        f"action {action.action_id} could not resolve element from E_select_{dimension}",
    )


def _required_evidence_ids(action: RcaAction, evidence_graph: EvidenceGraph) -> list[str]:
    evidence_ids: list[str] = []
    for alias in action.requires:
        for evidence_id in evidence_graph.matching(alias):
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
    return evidence_ids


def _selection_evidence_element(ctx: RunContext, dimension: str, required_aliases: list[str]) -> str | None:
    if ctx.repository is None or not dimension:
        return None
    aliases = [alias for alias in required_aliases if alias == f"E_select_{dimension}" or alias.startswith("E_select")]
    if not aliases:
        return None
    for alias in aliases:
        for evidence_id in (f"{ctx.run_id}:{alias}",):
            row = ctx.repository.get_evidence(run_id=ctx.run_id, evidence_id=evidence_id)
            if not isinstance(row, dict) or row.get("guard_status") != "passed":
                continue
            summary = row.get("result_summary")
            if not isinstance(summary, dict):
                continue
            if summary.get("dimension") != dimension:
                continue
            selected = summary.get("selected_element")
            if selected is not None:
                return str(selected)
    return None


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
        sql_count=int(getattr(result, "sql_count", 0) or 0),
        sql_audit_delta=int(getattr(result, "sql_audit_delta", getattr(result, "sql_count", 0)) or 0),
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

"""Deterministic action gate for compiled RCA plans."""

from __future__ import annotations

from datetime import date
from typing import Any

from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_models import GateDecision, RcaAction
from metric_rca.runtime.run_context import RunContext


DATA_FETCHING_ACTIONS = frozenset(
    {
        "detect_anomaly",
        "drilldown_dimension",
        "select_signal_element",
        "fetch_related_signal",
        "calculate_contribution",
    }
)
DOWNSTREAM_ACTIONS = frozenset(
    {
        "drilldown_dimension",
        "select_signal_element",
        "fetch_related_signal",
        "calculate_contribution",
        "merge_contribution_sets",
        "rank_root_causes",
    }
)


class ActionGate:
    def validate(self, ctx: RunContext, action: RcaAction, evidence_graph: EvidenceGraph) -> GateDecision:
        metric_error = _metric_scope_error(ctx, action)
        if metric_error is not None:
            return _deny("METRIC_SCOPE_VIOLATION", metric_error)

        date_error = _target_date_scope_error(ctx, action)
        if date_error is not None:
            return _deny("METRIC_SCOPE_VIOLATION", date_error)

        evidence_error = _required_evidence_error(action, evidence_graph)
        if evidence_error is not None:
            return _deny("EVIDENCE_MISSING", evidence_error)

        explicit_scope_error = _explicit_scope_error(ctx, action)
        if explicit_scope_error is not None:
            return _deny("ACTION_SCHEMA_INVALID", explicit_scope_error)

        no_anomaly_error = _no_anomaly_downstream_error(ctx, action)
        if no_anomaly_error is not None:
            return _deny("NO_ANOMALY_CONTRACT_VIOLATED", no_anomaly_error)

        budget_error = _budget_error(ctx, action)
        if budget_error is not None:
            return _deny(budget_error[0], budget_error[1])

        return GateDecision(allowed=True)


def _deny(error_code: str, message: str) -> GateDecision:
    return GateDecision(allowed=False, error_code=error_code, message=message)


def _metric_scope_error(ctx: RunContext, action: RcaAction) -> str | None:
    metric_id = action.args.get("metric_id")
    if metric_id is None or str(metric_id) == ctx.metric_id:
        return None
    return f"run target metric is {ctx.metric_id}; action {action.action_id} requested metric_id={metric_id}"


def _target_date_scope_error(ctx: RunContext, action: RcaAction) -> str | None:
    target_date = action.args.get("target_date")
    if target_date is None or _iso_text(target_date) == ctx.target_date.isoformat():
        return None
    return f"run target_date is {ctx.target_date.isoformat()}; action {action.action_id} requested target_date={target_date}"


def _required_evidence_error(action: RcaAction, evidence_graph: EvidenceGraph) -> str | None:
    missing = [alias for alias in action.requires if not evidence_graph.has_alias(alias)]
    if not missing:
        return None
    return f"action {action.action_id} requires missing evidence aliases: {missing}"


def _explicit_scope_error(ctx: RunContext, action: RcaAction) -> str | None:
    if action.kind not in DATA_FETCHING_ACTIONS or len(ctx.explicit_scope) != 1:
        return None
    dimension, element = next(iter(ctx.explicit_scope.items()))
    filters = _string_filters(action.args.get("filters"))
    if action.kind == "detect_anomaly":
        if filters.get(dimension) != element:
            return f"explicit question scope requires filters.{dimension}={element}"
        return None
    if ctx.scope_mode == "explicit_multi_driver":
        return _explicit_multi_driver_scope_error(dimension, element, action, filters)
    if action.args.get("dimension") != dimension:
        return f"explicit question scope requires dimension={dimension}"
    if action.kind in {"fetch_related_signal", "calculate_contribution"} and str(action.args.get("element")) != element:
        return f"explicit question scope requires element={element}"
    if action.kind in {"drilldown_dimension", "calculate_contribution"} and filters.get(dimension) != element:
        return f"explicit question scope requires filters.{dimension}={element}"
    return None


def _explicit_multi_driver_scope_error(
    dimension: str,
    element: str,
    action: RcaAction,
    filters: dict[str, str],
) -> str | None:
    filtered_element = filters.get(dimension)
    if filtered_element is not None and filtered_element != element:
        return (
            f"action {action.action_id} filters.{dimension}={filtered_element} "
            f"contradicts explicit question scope {dimension}={element}"
        )
    action_dimension = action.args.get("dimension")
    if action_dimension != dimension and filters.get(dimension) != element:
        return f"explicit multi-driver lane requires filters.{dimension}={element}"
    if action.kind == "select_signal_element" and action_dimension == dimension:
        return f"explicit multi-driver lane for {dimension} must bind element={element} directly"
    if action.kind in {"fetch_related_signal", "calculate_contribution"} and action_dimension == dimension:
        action_element = action.args.get("element")
        if action_element != element:
            return f"explicit multi-driver lane for {dimension} must bind element={element} directly"
    return None


def _no_anomaly_downstream_error(ctx: RunContext, action: RcaAction) -> str | None:
    if action.kind not in DOWNSTREAM_ACTIONS or ctx.repository is None:
        return None
    e1 = ctx.repository.get_evidence(run_id=ctx.run_id, evidence_id=f"{ctx.run_id}:E1")
    if not isinstance(e1, dict) or e1.get("guard_status") != "passed":
        return None
    summary = e1.get("result_summary")
    if not isinstance(summary, dict):
        return None
    if summary.get("is_anomaly") is False or summary.get("error_code") == "NO_ANOMALY_DETECTED":
        return "detect_anomaly returned no anomaly; downstream RCA actions are forbidden"
    return None


def _budget_error(ctx: RunContext, action: RcaAction) -> tuple[str, str] | None:
    limits = _budget_limits(ctx.budget)
    if isinstance(limits, str):
        return "BUDGET_CONFIG_INVALID", limits
    max_steps, max_query, max_drilldown_depth = limits
    if ctx.step_count >= max_steps and action.kind != "rank_root_causes":
        return "BUDGET_EXCEEDED", "step budget exhausted; call rank_root_causes or stop"
    if action.kind in DATA_FETCHING_ACTIONS and ctx.query_count >= max_query:
        return "BUDGET_EXCEEDED", "query budget exhausted; call rank_root_causes or stop"
    if action.kind == "drilldown_dimension" and ctx.drilldown_depth >= max_drilldown_depth:
        return "BUDGET_EXCEEDED", "drilldown depth exhausted; call rank_root_causes or stop"
    return None


def _budget_limits(budget: dict[str, int]) -> tuple[int, int, int] | str:
    missing = [key for key in ("max_steps", "max_query", "max_drilldown_depth") if key not in budget]
    if missing:
        return f"runtime budget missing required keys: {missing}"
    try:
        max_steps = int(budget["max_steps"])
        max_query = int(budget["max_query"])
        max_drilldown_depth = int(budget["max_drilldown_depth"])
    except (TypeError, ValueError) as exc:
        return f"runtime budget values must be integers: {exc}"
    if min(max_steps, max_query, max_drilldown_depth) < 0:
        return "runtime budget values must be non-negative"
    return max_steps, max_query, max_drilldown_depth


def _string_filters(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _iso_text(raw: Any) -> str:
    if isinstance(raw, date):
        return raw.isoformat()
    return str(raw)

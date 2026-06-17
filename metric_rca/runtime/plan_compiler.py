"""Compile parsed metric intent into a deterministic RCA action plan."""

from __future__ import annotations

from typing import Any

from metric_rca.business.discovery_policy import DiscoveryPolicy, discovery_policy_from_intent
from metric_rca.runtime.plan_models import CasePrior, RcaAction, RcaPlan
from metric_rca.services.metric_contracts import ParsedIntent


RATE_FAMILY_METRICS = frozenset({"pay_cvr", "refund_rate", "stockout_rate", "complaint_rate"})


class PlanCompilerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RcaPlanCompiler:
    def compile(
        self,
        *,
        run_id: str,
        parsed_intent: ParsedIntent,
        memory_hints: list[CasePrior] | None = None,
        budget: dict[str, int] | None = None,
    ) -> RcaPlan:
        explicit_scope = _explicit_scope(parsed_intent)
        actions = [
            RcaAction(
                action_id="A1",
                kind="detect_anomaly",
                args={
                    "metric_id": parsed_intent.metric_id,
                    "target_date": parsed_intent.target_date,
                    "filters": explicit_scope,
                },
                produces=["E1"],
            )
        ]
        explicit_dimension, explicit_element = _explicit_dimension_element(parsed_intent)
        if explicit_dimension is not None and explicit_element is not None:
            actions.extend(_explicit_actions(parsed_intent, explicit_dimension, explicit_element, explicit_scope))
        else:
            actions.extend(_broad_actions(parsed_intent, discovery_policy_from_intent(parsed_intent)))
        return RcaPlan(
            run_id=run_id,
            metric_id=parsed_intent.metric_id,
            target_date=parsed_intent.target_date,
            question_family=parsed_intent.question_family,
            family=_metric_family(parsed_intent.metric_id),
            explicit_scope=explicit_scope,
            actions=actions,
            budget=budget or {"max_steps": 8, "max_query": 12, "max_drilldown_depth": 3},
            memory_hints=memory_hints or [],
        )


def _explicit_actions(
    parsed_intent: ParsedIntent,
    dimension: str,
    element: str,
    filters: dict[str, str],
) -> list[RcaAction]:
    signal_type = _signal_type_for_metric_dimension(parsed_intent.metric_id, dimension)
    return [
        RcaAction(
            action_id="A2",
            kind="drilldown_dimension",
            args={
                "metric_id": parsed_intent.metric_id,
                "target_date": parsed_intent.target_date,
                "dimension": dimension,
                "filters": filters,
            },
            requires=["E1"],
            produces=[f"E2_{dimension}"],
        ),
        RcaAction(
            action_id="A3",
            kind="fetch_related_signal",
            args={
                "metric_id": parsed_intent.metric_id,
                "target_date": parsed_intent.target_date,
                "signal_type": signal_type,
                "dimension": dimension,
                "element": element,
                "filters": filters,
            },
            requires=["E1", f"E2_{dimension}"],
            produces=[f"E3_{_dimension_prefix(dimension)}_{element}"],
        ),
        RcaAction(
            action_id="A4",
            kind="calculate_contribution",
            args={
                "metric_id": parsed_intent.metric_id,
                "target_date": parsed_intent.target_date,
                "dimension": dimension,
                "element": element,
                "filters": filters,
            },
            requires=["E1", f"E2_{dimension}", "E3"],
            produces=["E4"],
        ),
        RcaAction(
            action_id="A5",
            kind="rank_root_causes",
            args={"metric_id": parsed_intent.metric_id, "target_date": parsed_intent.target_date},
            requires=["E1", f"E2_{dimension}", "E3", "E4"],
            produces=["E_rank"],
        ),
    ]


def _broad_actions(parsed_intent: ParsedIntent, policy: DiscoveryPolicy) -> list[RcaAction]:
    if not policy.required_drilldowns:
        raise PlanCompilerError("DISCOVERY_POLICY_MISSING", "unscoped RCA requires discovery policy")
    actions: list[RcaAction] = []
    for index, dimension in enumerate(policy.required_drilldowns, start=2):
        actions.append(
            RcaAction(
                action_id=f"A{index}",
                kind="drilldown_dimension",
                args={
                    "metric_id": parsed_intent.metric_id,
                    "target_date": parsed_intent.target_date,
                    "dimension": dimension,
                    "filters": {},
                },
                requires=["E1"],
                produces=[f"E2_{dimension}"],
            )
        )
    signal_dimension = policy.first_signal_dimension
    signal_type = policy.first_signal_type
    if signal_dimension is None or signal_type is None:
        raise PlanCompilerError("DISCOVERY_POLICY_MISSING", "broad RCA policy requires first signal")
    next_index = len(actions) + 2
    actions.append(
        RcaAction(
            action_id=f"A{next_index}",
            kind="fetch_related_signal",
            args={
                "metric_id": parsed_intent.metric_id,
                "target_date": parsed_intent.target_date,
                "signal_type": signal_type,
                "dimension": signal_dimension,
                "element": policy.first_signal_element,
                "filters": {},
                "element_selection": policy.element_selection,
            },
            requires=["E1", f"E2_{signal_dimension}"],
            produces=[f"E3_{_dimension_prefix(signal_dimension)}"],
            dynamic=policy.first_signal_element is None,
        )
    )
    actions.append(
        RcaAction(
            action_id=f"A{next_index + 1}",
            kind="calculate_contribution",
            args={
                "metric_id": parsed_intent.metric_id,
                "target_date": parsed_intent.target_date,
                "dimension": signal_dimension,
                "element": policy.first_signal_element,
                "filters": {},
                "element_selection": policy.element_selection,
            },
            requires=["E1", f"E2_{signal_dimension}", "E3"],
            produces=["E4"],
            dynamic=policy.first_signal_element is None,
        )
    )
    actions.append(
        RcaAction(
            action_id=f"A{next_index + 2}",
            kind="rank_root_causes",
            args={"metric_id": parsed_intent.metric_id, "target_date": parsed_intent.target_date},
            requires=["E1", *[f"E2_{dimension}" for dimension in policy.required_drilldowns], "E3", "E4"],
            produces=["E_rank"],
        )
    )
    return actions


def _explicit_scope(parsed_intent: ParsedIntent) -> dict[str, str]:
    if parsed_intent.filters:
        return {str(key): str(value) for key, value in parsed_intent.filters.items()}
    if parsed_intent.dimension is not None and parsed_intent.element is not None:
        return {parsed_intent.dimension: parsed_intent.element}
    return {}


def _explicit_dimension_element(parsed_intent: ParsedIntent) -> tuple[str | None, str | None]:
    if parsed_intent.dimension is not None and parsed_intent.element is not None:
        return parsed_intent.dimension, parsed_intent.element
    if len(parsed_intent.filters) == 1:
        return next(iter(parsed_intent.filters.items()))
    return None, None


def _metric_family(metric_id: str) -> str:
    return "rate_family" if metric_id in RATE_FAMILY_METRICS else "gmv_family"


def _signal_type_for_metric_dimension(metric_id: str, dimension: str) -> str:
    from metric_rca.business.signal_policy import select_signal_type_for_metric_dimension

    try:
        return select_signal_type_for_metric_dimension(metric_id=metric_id, dimension=dimension)
    except ValueError as exc:
        raise PlanCompilerError("SIGNAL_POLICY_MISSING", "signal policy missing for metric/dimension") from exc


def _dimension_prefix(dimension: str) -> str:
    return {
        "channel": "ch",
        "category": "cat",
        "device": "dev",
        "product": "prod",
        "warehouse": "wh",
    }.get(dimension, dimension)

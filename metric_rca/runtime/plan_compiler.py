"""Compile parsed metric intent into a deterministic RCA action plan."""

from __future__ import annotations

from typing import Any, Protocol

from metric_rca.agent.evidence_aliases import e3_alias_for_dimension
from metric_rca.business.discovery_policy import DiscoveryPolicy, discovery_policy_from_intent
from metric_rca.business.policy_registry import allowed_dimensions_validator_from_metric_definition
from metric_rca.runtime.plan_models import CasePrior, RcaAction, RcaPlan
from metric_rca.services.metric_contracts import ParsedIntent


class MetricMetadataProvider(Protocol):
    def get_metric_definition(self, metric_id: str) -> Any:
        ...


class PlanCompilerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RcaPlanCompiler:
    def __init__(self, *, metric_service: MetricMetadataProvider | None = None) -> None:
        self._metric_service = metric_service

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
        validate_dimensions = self._allowed_dimensions_validator()
        if explicit_dimension is not None and explicit_element is not None:
            actions.extend(
                _explicit_actions(
                    parsed_intent,
                    explicit_dimension,
                    explicit_element,
                    explicit_scope,
                    validate_dimensions=validate_dimensions,
                )
            )
        else:
            try:
                discovery_policy = discovery_policy_from_intent(
                    parsed_intent,
                    validate_dimensions=validate_dimensions,
                )
            except ValueError as exc:
                raise PlanCompilerError("DISCOVERY_POLICY_INVALID", str(exc)) from exc
            actions.extend(
                _broad_actions(
                    parsed_intent,
                    _policy_with_memory_hints(
                        parsed_intent,
                        discovery_policy,
                        memory_hints or [],
                        validate_dimensions=validate_dimensions,
                    ),
                    validate_dimensions=validate_dimensions,
                )
            )
        return RcaPlan(
            run_id=run_id,
            metric_id=parsed_intent.metric_id,
            target_date=parsed_intent.target_date,
            question_family=parsed_intent.question_family,
            family=self._metric_family(parsed_intent.metric_id),
            explicit_scope=explicit_scope,
            actions=actions,
            budget=_plan_budget(actions, budget),
            memory_hints=memory_hints or [],
        )

    def _metric_family(self, metric_id: str) -> str:
        if self._metric_service is None:
            raise PlanCompilerError("METRIC_METADATA_REQUIRED", "plan compiler requires metric metadata")
        definition = self._metric_service.get_metric_definition(metric_id)
        family = getattr(definition, "metric_family", None)
        if family not in {"gmv_family", "rate_family"}:
            raise PlanCompilerError("METRIC_METADATA_INVALID", "metric_family must be gmv_family or rate_family")
        return str(family)

    def _allowed_dimensions_validator(self):
        if self._metric_service is None:
            raise PlanCompilerError("METRIC_METADATA_REQUIRED", "plan compiler requires metric metadata")
        return allowed_dimensions_validator_from_metric_definition(self._metric_service.get_metric_definition)


def _explicit_actions(
    parsed_intent: ParsedIntent,
    dimension: str,
    element: str,
    filters: dict[str, str],
    *,
    validate_dimensions: Any,
) -> list[RcaAction]:
    signal_type = _signal_type_for_metric_dimension(
        parsed_intent.metric_id,
        dimension,
        validate_dimensions=validate_dimensions,
    )
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
            produces=["E3"],
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


def _broad_actions(parsed_intent: ParsedIntent, policy: DiscoveryPolicy, *, validate_dimensions: Any) -> list[RcaAction]:
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
    if len(policy.required_drilldowns) > 1:
        actions.extend(
            _parallel_broad_contribution_chains(
                parsed_intent=parsed_intent,
                policy=policy,
                first_action_index=len(actions) + 2,
                validate_dimensions=validate_dimensions,
            )
        )
        return actions
    next_index = len(actions) + 2
    selection_alias = f"E_select_{signal_dimension}"
    actions.append(
        RcaAction(
            action_id=f"A{next_index}",
            kind="select_signal_element",
            args={
                "metric_id": parsed_intent.metric_id,
                "target_date": parsed_intent.target_date,
                "signal_type": signal_type,
                "dimension": signal_dimension,
                "filters": {},
                "element_selection": policy.element_selection,
            },
            requires=["E1", f"E2_{signal_dimension}"],
            produces=[selection_alias],
        )
    )
    actions.append(
        RcaAction(
            action_id=f"A{next_index + 1}",
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
            requires=["E1", f"E2_{signal_dimension}", selection_alias],
            produces=["E3"],
            dynamic=policy.first_signal_element is None,
        )
    )
    actions.append(
        RcaAction(
            action_id=f"A{next_index + 2}",
            kind="calculate_contribution",
            args={
                "metric_id": parsed_intent.metric_id,
                "target_date": parsed_intent.target_date,
                "dimension": signal_dimension,
                "element": policy.first_signal_element,
                "filters": {},
                "element_selection": policy.element_selection,
            },
            requires=["E1", f"E2_{signal_dimension}", selection_alias, "E3"],
            produces=["E4"],
            dynamic=policy.first_signal_element is None,
        )
    )
    actions.append(
        RcaAction(
            action_id=f"A{next_index + 3}",
            kind="rank_root_causes",
            args={"metric_id": parsed_intent.metric_id, "target_date": parsed_intent.target_date},
            requires=["E1", *[f"E2_{dimension}" for dimension in policy.required_drilldowns], selection_alias, "E3", "E4"],
            produces=["E_rank"],
        )
    )
    return actions


def _parallel_broad_contribution_chains(
    *,
    parsed_intent: ParsedIntent,
    policy: DiscoveryPolicy,
    first_action_index: int,
    validate_dimensions: Any,
) -> list[RcaAction]:
    signal_dimension = policy.first_signal_dimension
    signal_type = policy.first_signal_type
    if signal_dimension is None or signal_type is None:
        raise PlanCompilerError("DISCOVERY_POLICY_MISSING", "broad RCA policy requires first signal")

    chain_dimensions = [
        signal_dimension,
        *[dimension for dimension in policy.required_drilldowns if dimension != signal_dimension],
    ]
    actions: list[RcaAction] = []
    e4_aliases: list[str] = []
    next_index = first_action_index
    for dimension in chain_dimensions:
        is_primary_chain = dimension == signal_dimension
        chain_signal_type = (
            signal_type
            if is_primary_chain or signal_type == "interaction"
            else _signal_type_for_metric_dimension(
                parsed_intent.metric_id,
                dimension,
                validate_dimensions=validate_dimensions,
            )
        )
        element_selection = policy.element_selection if is_primary_chain else "top_candidate"
        first_signal_element = policy.first_signal_element if is_primary_chain else None
        selection_alias = f"E_select_{dimension}"
        e3_alias = e3_alias_for_dimension(dimension) or f"E3_{dimension}"
        e4_alias = f"E4_{dimension}"
        actions.extend(
            [
                RcaAction(
                    action_id=f"A{next_index}",
                    kind="select_signal_element",
                    args={
                        "metric_id": parsed_intent.metric_id,
                        "target_date": parsed_intent.target_date,
                        "signal_type": chain_signal_type,
                        "dimension": dimension,
                        "filters": {},
                        "element_selection": element_selection,
                    },
                    requires=["E1", f"E2_{dimension}"],
                    produces=[selection_alias],
                ),
                RcaAction(
                    action_id=f"A{next_index + 1}",
                    kind="fetch_related_signal",
                    args={
                        "metric_id": parsed_intent.metric_id,
                        "target_date": parsed_intent.target_date,
                        "signal_type": chain_signal_type,
                        "dimension": dimension,
                        "element": first_signal_element,
                        "filters": {},
                        "element_selection": element_selection,
                    },
                    requires=["E1", f"E2_{dimension}", selection_alias],
                    produces=[e3_alias],
                    dynamic=first_signal_element is None,
                ),
                RcaAction(
                    action_id=f"A{next_index + 2}",
                    kind="calculate_contribution",
                    args={
                        "metric_id": parsed_intent.metric_id,
                        "target_date": parsed_intent.target_date,
                        "dimension": dimension,
                        "element": first_signal_element,
                        "filters": {},
                        "element_selection": element_selection,
                        "evidence_alias": e4_alias,
                    },
                    requires=["E1", f"E2_{dimension}", selection_alias, e3_alias],
                    produces=[e4_alias],
                    dynamic=first_signal_element is None,
                ),
            ]
        )
        e4_aliases.append(e4_alias)
        next_index += 3

    actions.append(
        RcaAction(
            action_id=f"A{next_index}",
            kind="merge_contribution_sets",
            args={
                "metric_id": parsed_intent.metric_id,
                "target_date": parsed_intent.target_date,
                "source_evidence_aliases": e4_aliases,
            },
            requires=e4_aliases,
            produces=["E4"],
        )
    )
    actions.append(
        RcaAction(
            action_id=f"A{next_index + 1}",
            kind="rank_root_causes",
            args={"metric_id": parsed_intent.metric_id, "target_date": parsed_intent.target_date},
            requires=[
                "E1",
                *[f"E2_{dimension}" for dimension in policy.required_drilldowns],
                *[f"E_select_{dimension}" for dimension in chain_dimensions],
                *[e3_alias_for_dimension(dimension) or f"E3_{dimension}" for dimension in chain_dimensions],
                *e4_aliases,
                "E4",
            ],
            produces=["E_rank"],
        )
    )
    return actions


def _plan_budget(actions: list[RcaAction], budget: dict[str, int] | None) -> dict[str, int]:
    resolved = dict(budget or {"max_steps": 8, "max_query": 20, "max_drilldown_depth": 3})
    if any(action.kind == "merge_contribution_sets" for action in actions):
        resolved["max_steps"] = max(int(resolved.get("max_steps", 0)), len(actions))
        resolved["max_query"] = max(int(resolved.get("max_query", 0)), 50)
        drilldown_count = len([action for action in actions if action.kind == "drilldown_dimension"])
        resolved["max_drilldown_depth"] = max(int(resolved.get("max_drilldown_depth", 0)), drilldown_count)
    return resolved


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


def _policy_with_memory_hints(
    parsed_intent: ParsedIntent,
    policy: DiscoveryPolicy,
    memory_hints: list[CasePrior],
    *,
    validate_dimensions: Any,
) -> DiscoveryPolicy:
    if parsed_intent.analysis_strategy != "standard":
        return policy
    if not memory_hints or not policy.required_drilldowns:
        return policy
    for hint in sorted(memory_hints, key=lambda item: item.confidence, reverse=True):
        if hint.metric_id != parsed_intent.metric_id or hint.confidence < 0.70:
            continue
        preferred_signal_types = set(hint.preferred_signal_types)
        for dimension in hint.preferred_dimensions:
            if dimension not in policy.required_drilldowns:
                continue
            try:
                signal_type = _signal_type_for_metric_dimension(
                    parsed_intent.metric_id,
                    dimension,
                    validate_dimensions=validate_dimensions,
                )
            except PlanCompilerError:
                continue
            if preferred_signal_types and signal_type not in preferred_signal_types:
                continue
            return DiscoveryPolicy(
                required_drilldowns=policy.required_drilldowns,
                first_signal_dimension=dimension,
                first_signal_type=signal_type,
                first_signal_element=None,
                enforce_first_signal_top_candidate=False,
                element_selection=policy.element_selection,
            )
    return policy


def _signal_type_for_metric_dimension(metric_id: str, dimension: str, *, validate_dimensions: Any = None) -> str:
    from metric_rca.business.signal_policy import select_signal_type_for_metric_dimension

    try:
        return select_signal_type_for_metric_dimension(
            metric_id=metric_id,
            dimension=dimension,
            validate_dimensions=validate_dimensions,
        )
    except ValueError as exc:
        raise PlanCompilerError("SIGNAL_POLICY_MISSING", "signal policy missing for metric/dimension") from exc

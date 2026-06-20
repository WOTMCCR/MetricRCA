"""Compile parsed metric intent into a deterministic RCA action plan."""

from __future__ import annotations

from typing import Any, Protocol

from metric_rca.agent.evidence_aliases import (
    MAX_EVIDENCE_ID_LENGTH,
    e3_alias_for_dimension,
    e3_alias_for_signal_lane,
    evidence_alias_fits,
)
from metric_rca.business.discovery_policy import DiscoveryLane, DiscoveryPolicy, discovery_policy_from_intent
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
        scope_mode = "unscoped"
        if explicit_dimension is not None and explicit_element is not None:
            discovery_policy = _discovery_policy_for_intent(parsed_intent, validate_dimensions=validate_dimensions)
            if discovery_policy.scope_mode == "explicit_multi_driver":
                actions.extend(
                    _discovery_actions(
                        parsed_intent,
                        discovery_policy,
                        validate_dimensions=validate_dimensions,
                        explicit_scope=explicit_scope,
                    )
                )
                scope_mode = "explicit_multi_driver"
            else:
                actions.extend(
                    _explicit_actions(
                        parsed_intent,
                        explicit_dimension,
                        explicit_element,
                        explicit_scope,
                        validate_dimensions=validate_dimensions,
                    )
                )
                scope_mode = "explicit_single"
        elif scoped_interaction_actions := _scoped_interaction_actions(
            parsed_intent,
            explicit_scope,
            validate_dimensions=validate_dimensions,
        ):
            actions.extend(scoped_interaction_actions)
            scope_mode = "scoped_interaction"
        else:
            discovery_policy = _discovery_policy_for_intent(parsed_intent, validate_dimensions=validate_dimensions)
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
        _validate_evidence_alias_budget(run_id=run_id, actions=actions)
        return RcaPlan(
            run_id=run_id,
            metric_id=parsed_intent.metric_id,
            target_date=parsed_intent.target_date,
            question_family=parsed_intent.question_family,
            family=self._metric_family(parsed_intent.metric_id),
            explicit_scope=explicit_scope,
            scope_mode=scope_mode,
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


def _validate_evidence_alias_budget(*, run_id: str, actions: list[RcaAction]) -> None:
    for action in actions:
        for alias in action.produces:
            if not evidence_alias_fits(run_id, alias):
                raise PlanCompilerError(
                    "EVIDENCE_ID_TOO_LONG",
                    (
                        f"evidence alias {alias} with run_id length {len(run_id)} exceeds "
                        f"{MAX_EVIDENCE_ID_LENGTH} character evidence_id limit"
                    ),
                )


def _discovery_policy_for_intent(parsed_intent: ParsedIntent, *, validate_dimensions: Any) -> DiscoveryPolicy:
    try:
        return discovery_policy_from_intent(
            parsed_intent,
            validate_dimensions=validate_dimensions,
        )
    except ValueError as exc:
        raise PlanCompilerError("DISCOVERY_POLICY_INVALID", str(exc)) from exc


def _discovery_actions(
    parsed_intent: ParsedIntent,
    policy: DiscoveryPolicy,
    *,
    validate_dimensions: Any,
    explicit_scope: dict[str, str] | None = None,
) -> list[RcaAction]:
    if not policy.required_drilldowns:
        raise PlanCompilerError("DISCOVERY_POLICY_MISSING", "discovery policy requires drilldowns")
    actions: list[RcaAction] = []
    scoped_filters = _scope_filters_by_dimension(
        policy.required_drilldowns,
        explicit_scope or {},
    )
    for index, dimension in enumerate(policy.required_drilldowns, start=2):
        actions.append(
            RcaAction(
                action_id=f"A{index}",
                kind="drilldown_dimension",
                args={
                    "metric_id": parsed_intent.metric_id,
                    "target_date": parsed_intent.target_date,
                    "dimension": dimension,
                    "filters": scoped_filters[dimension],
                },
                requires=["E1"],
                produces=[f"E2_{dimension}"],
            )
        )
    actions.extend(
        _parallel_broad_contribution_chains(
            parsed_intent=parsed_intent,
            policy=policy,
            first_action_index=len(actions) + 2,
            validate_dimensions=validate_dimensions,
            explicit_scope=explicit_scope,
            scoped_filters=scoped_filters if explicit_scope else None,
        )
    )
    return actions


def _scoped_interaction_actions(
    parsed_intent: ParsedIntent,
    explicit_scope: dict[str, str],
    *,
    validate_dimensions: Any,
) -> list[RcaAction] | None:
    if parsed_intent.question_family not in {"interaction_gmv_anomaly", "interaction_uv_anomaly"}:
        return None
    if not {"channel", "category"}.issubset(explicit_scope):
        return None
    try:
        validate_dimensions(parsed_intent.metric_id, ("channel", "category"))
    except ValueError as exc:
        raise PlanCompilerError("DISCOVERY_POLICY_INVALID", str(exc)) from exc
    policy = DiscoveryPolicy(
        required_drilldowns=("channel", "category"),
        first_signal_dimension="channel",
        first_signal_type="interaction",
        element_selection="signal_anomaly",
    )
    scoped_filters = {
        "channel": _filters_except(explicit_scope, "channel"),
        "category": _filters_except(explicit_scope, "category"),
    }
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
                    "filters": scoped_filters[dimension],
                },
                requires=["E1"],
                produces=[f"E2_{dimension}"],
            )
        )
    actions.extend(
        _parallel_broad_contribution_chains(
            parsed_intent=parsed_intent,
            policy=policy,
            first_action_index=len(actions) + 2,
            validate_dimensions=validate_dimensions,
            scoped_elements={
                "channel": explicit_scope["channel"],
                "category": explicit_scope["category"],
            },
            scoped_filters=scoped_filters,
        )
    )
    return actions


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
    scoped_elements: dict[str, str] | None = None,
    scoped_filters: dict[str, dict[str, str]] | None = None,
    explicit_scope: dict[str, str] | None = None,
) -> list[RcaAction]:
    lanes = _discovery_lanes(
        parsed_intent=parsed_intent,
        policy=policy,
        validate_dimensions=validate_dimensions,
    )
    actions: list[RcaAction] = []
    e4_aliases: list[str] = []
    selection_aliases: list[str] = []
    e3_aliases: list[str] = []
    next_index = first_action_index
    dynamic_selection_aliases: set[str] = set()
    e4_alias_set: set[str] = set()
    e3_alias_set: set[str] = set()
    for lane in lanes:
        dimension = lane.dimension
        contribution_filters = dict(scoped_filters.get(dimension, {}) if scoped_filters else {})
        signal_filters = _signal_filters_for_lane(lane, contribution_filters)
        scoped_element = scoped_elements.get(dimension) if scoped_elements else None
        first_signal_element = _lane_element(lane, scoped_element=scoped_element, explicit_scope=explicit_scope)
        selection_alias = lane.selection_alias or f"E_select_{dimension}"
        e3_alias = lane.signal_evidence_alias or e3_alias_for_signal_lane(
            dimension,
            lane.signal_type,
            element_known=first_signal_element is not None,
        )
        e4_alias = lane.evidence_alias or f"E4_{dimension}"
        if e4_alias in e4_alias_set:
            raise PlanCompilerError("DISCOVERY_LANE_ALIAS_CONFLICT", f"duplicate E4 alias {e4_alias}")
        if e3_alias in e3_alias_set:
            raise PlanCompilerError("DISCOVERY_LANE_ALIAS_CONFLICT", f"duplicate E3 alias {e3_alias}")
        e4_alias_set.add(e4_alias)
        e3_alias_set.add(e3_alias)
        e4_aliases.append(e4_alias)
        e3_aliases.append(e3_alias)
        scope_policy_args = _explicit_scope_policy_args(lane, explicit_scope=explicit_scope)

        if first_signal_element is None:
            if selection_alias in dynamic_selection_aliases:
                raise PlanCompilerError(
                    "DISCOVERY_LANE_ALIAS_CONFLICT",
                    f"multiple dynamic discovery lanes require {selection_alias}",
                )
            dynamic_selection_aliases.add(selection_alias)
            selection_aliases.append(selection_alias)
            actions.append(
                RcaAction(
                    action_id=f"A{next_index}",
                    kind="select_signal_element",
                    args={
                        "metric_id": parsed_intent.metric_id,
                        "target_date": parsed_intent.target_date,
                        "signal_type": lane.signal_type,
                        "dimension": dimension,
                        "filters": signal_filters,
                        "element_selection": lane.element_selection,
                        "evidence_alias": selection_alias,
                        **scope_policy_args,
                    },
                    requires=["E1", f"E2_{dimension}"],
                    produces=[selection_alias],
                ),
            )
            next_index += 1

        fetch_requires = ["E1", f"E2_{dimension}"]
        if first_signal_element is None:
            fetch_requires.append(selection_alias)
        actions.append(
            RcaAction(
                action_id=f"A{next_index}",
                kind="fetch_related_signal",
                args={
                    "metric_id": parsed_intent.metric_id,
                    "target_date": parsed_intent.target_date,
                    "signal_type": lane.signal_type,
                    "dimension": dimension,
                    "element": first_signal_element,
                    "filters": signal_filters,
                    "element_selection": lane.element_selection,
                    "evidence_alias": e3_alias,
                    **scope_policy_args,
                },
                requires=fetch_requires,
                produces=[e3_alias],
                dynamic=first_signal_element is None,
            )
        )
        calculate_requires = ["E1", f"E2_{dimension}"]
        if first_signal_element is None:
            calculate_requires.append(selection_alias)
        calculate_requires.append(e3_alias)
        actions.append(
            RcaAction(
                action_id=f"A{next_index + 1}",
                kind="calculate_contribution",
                args={
                    "metric_id": parsed_intent.metric_id,
                    "target_date": parsed_intent.target_date,
                    "dimension": dimension,
                    "element": first_signal_element,
                    "filters": contribution_filters,
                    "element_selection": lane.element_selection,
                    "evidence_alias": e4_alias,
                    **scope_policy_args,
                },
                requires=calculate_requires,
                produces=[e4_alias],
                dynamic=first_signal_element is None,
            )
        )
        next_index += 2

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
            requires=_ordered_unique_list(
                [
                    "E1",
                    *[f"E2_{dimension}" for dimension in policy.required_drilldowns],
                    *selection_aliases,
                    *e3_aliases,
                    *e4_aliases,
                    "E4",
                ]
            ),
            produces=["E_rank"],
        )
    )
    return actions


def _discovery_lanes(
    *,
    parsed_intent: ParsedIntent,
    policy: DiscoveryPolicy,
    validate_dimensions: Any,
) -> list[DiscoveryLane]:
    if policy.lanes:
        return list(policy.lanes)
    signal_dimension = policy.first_signal_dimension
    signal_type = policy.first_signal_type
    if signal_dimension is None or signal_type is None:
        raise PlanCompilerError("DISCOVERY_POLICY_MISSING", "broad RCA policy requires first signal")
    dimensions = [
        signal_dimension,
        *[dimension for dimension in policy.required_drilldowns if dimension != signal_dimension],
    ]
    lanes: list[DiscoveryLane] = []
    for dimension in dimensions:
        is_primary = dimension == signal_dimension
        if is_primary:
            lanes.append(
                DiscoveryLane(
                    dimension=dimension,
                    signal_type=signal_type,
                    element_binding="policy",
                    element=policy.first_signal_element,
                    element_selection=policy.element_selection,
                    evidence_alias=f"E4_{dimension}",
                )
            )
            continue
        lanes.append(
            DiscoveryLane(
                dimension=dimension,
                signal_type=(
                    signal_type
                    if signal_type == "interaction"
                    else _signal_type_for_metric_dimension(
                        parsed_intent.metric_id,
                        dimension,
                        validate_dimensions=validate_dimensions,
                    )
                ),
                evidence_alias=f"E4_{dimension}",
            )
        )
    return lanes


def _signal_filters_for_lane(lane: DiscoveryLane, contribution_filters: dict[str, str]) -> dict[str, str]:
    if lane.signal_filter_mode == "none":
        return {}
    return dict(contribution_filters)


def _explicit_scope_policy_args(lane: DiscoveryLane, *, explicit_scope: dict[str, str] | None) -> dict[str, str]:
    if not explicit_scope or lane.explicit_scope_policy == "strict":
        return {}
    return {"explicit_scope_policy": lane.explicit_scope_policy}


def _lane_element(
    lane: DiscoveryLane,
    *,
    scoped_element: str | None,
    explicit_scope: dict[str, str] | None,
) -> str | None:
    if scoped_element is not None:
        return scoped_element
    if lane.element_binding == "dynamic":
        return None
    if lane.element_binding == "policy":
        return lane.element
    if explicit_scope is None or lane.dimension not in explicit_scope:
        raise PlanCompilerError(
            "DISCOVERY_LANE_SCOPE_MISSING",
            f"lane for dimension={lane.dimension} requires explicit scope",
        )
    return explicit_scope[lane.dimension]


def _filters_except(filters: dict[str, str], excluded_dimension: str) -> dict[str, str]:
    return {dimension: element for dimension, element in filters.items() if dimension != excluded_dimension}


def _scope_filters_by_dimension(dimensions: tuple[str, ...], explicit_scope: dict[str, str]) -> dict[str, dict[str, str]]:
    return {dimension: _filters_except(explicit_scope, dimension) for dimension in dimensions}


def _ordered_unique_list(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _plan_budget(actions: list[RcaAction], budget: dict[str, int] | None) -> dict[str, int]:
    resolved = dict(budget or {"max_steps": 8, "max_query": 20, "max_drilldown_depth": 3})
    if any(action.kind == "merge_contribution_sets" for action in actions):
        resolved["max_steps"] = max(int(resolved.get("max_steps", 0)), len(actions))
        resolved["max_query"] = max(int(resolved.get("max_query", 0)), _estimated_query_budget(actions))
        drilldown_count = len([action for action in actions if action.kind == "drilldown_dimension"])
        resolved["max_drilldown_depth"] = max(int(resolved.get("max_drilldown_depth", 0)), drilldown_count)
    return resolved


def _estimated_query_budget(actions: list[RcaAction]) -> int:
    return sum(_estimated_action_query_cost(action) for action in actions) + 2


def _estimated_action_query_cost(action: RcaAction) -> int:
    if action.kind in {"detect_anomaly", "drilldown_dimension", "select_signal_element", "fetch_related_signal"}:
        return 2
    if action.kind != "calculate_contribution":
        return 0
    metric_id = str(action.args.get("metric_id"))
    if metric_id in {"gmv", "net_gmv"}:
        return 6
    return 2


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

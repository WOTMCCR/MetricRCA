"""Structured business policy registry for MetricRCA planning.

This module is the single policy data source for metric/dimension signal
selection, broad discovery planning, and factor graph selection. Callers may
inject a registry in tests, but production selection reads from
DEFAULT_POLICY_REGISTRY.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from metric_rca.domain.enums import DimensionId, MetricId, RootCauseType

SignalType = Literal["campaign", "inventory", "conversion", "refund_quality", "interaction"]
ElementSelection = Literal["top_candidate", "signal_anomaly", "signal_level"]
FactorGraphType = Literal["uv_pay_cvr_aov", "net_gmv_chain", "dimension_delta"]
DiscoveryScopeMode = Literal["unscoped", "explicit_single", "explicit_multi_driver"]
LaneElementBinding = Literal["dynamic", "explicit_scope", "policy"]
LaneSignalFilterMode = Literal["inherit", "none"]
LaneExplicitScopePolicy = Literal["strict", "global_explanatory"]
AllowedDimensionsValidator = Callable[[str, tuple[str, ...]], None]
MetricDefinitionProvider = Callable[[str], Any]

GMV_DISCOVERY_REQUIRED_DRILLDOWNS = (
    DimensionId.CHANNEL.value,
    DimensionId.CATEGORY.value,
    DimensionId.PRODUCT.value,
)


class PolicyRegistryError(ValueError):
    """Fail-fast error for invalid or missing business policy."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class MetricSignalPolicy:
    metric_id: str
    dimension: str
    signal_type: SignalType
    root_cause_type: str | None = None


@dataclass(frozen=True)
class DiscoveryLane:
    dimension: str
    signal_type: SignalType
    element_binding: LaneElementBinding = "dynamic"
    element: str | None = None
    element_selection: ElementSelection = "top_candidate"
    evidence_alias: str | None = None
    selection_alias: str | None = None
    signal_evidence_alias: str | None = None
    signal_filter_mode: LaneSignalFilterMode = "inherit"
    explicit_scope_policy: LaneExplicitScopePolicy = "strict"


@dataclass(frozen=True)
class DiscoveryPolicy:
    required_drilldowns: tuple[str, ...] = ()
    first_signal_dimension: str | None = None
    first_signal_type: str | None = None
    first_signal_element: str | None = None
    enforce_first_signal_top_candidate: bool = False
    element_selection: ElementSelection = "top_candidate"
    scope_mode: DiscoveryScopeMode = "unscoped"
    explicit_scope_dimensions: tuple[str, ...] = ()
    lanes: tuple[DiscoveryLane, ...] = ()


@dataclass(frozen=True)
class DiscoveryPolicyRule:
    metric_id: str
    question_family: str | None
    analysis_strategy: str | None
    required_drilldowns: tuple[str, ...] = ()
    first_signal_dimension: str | None = None
    first_signal_type: SignalType | None = None
    first_signal_element: str | None = None
    enforce_first_signal_top_candidate: bool = False
    element_selection: ElementSelection = "top_candidate"
    scope_mode: DiscoveryScopeMode = "unscoped"
    explicit_scope_dimensions: tuple[str, ...] = ()
    lanes: tuple[DiscoveryLane, ...] = ()

    def to_policy(self) -> DiscoveryPolicy:
        return DiscoveryPolicy(
            required_drilldowns=self.required_drilldowns,
            first_signal_dimension=self.first_signal_dimension,
            first_signal_type=self.first_signal_type,
            first_signal_element=self.first_signal_element,
            enforce_first_signal_top_candidate=self.enforce_first_signal_top_candidate,
            element_selection=self.element_selection,
            scope_mode=self.scope_mode,
            explicit_scope_dimensions=self.explicit_scope_dimensions,
            lanes=self.lanes,
        )


@dataclass(frozen=True)
class FactorGraphPolicy:
    metric_id: str
    graph_type: FactorGraphType
    factor_metrics: tuple[str, ...] = ()


@dataclass(frozen=True)
class RootCausePolicy:
    metric_id: str
    dimension: str
    root_cause_type: str
    signal_type: str | None = None


@dataclass(frozen=True)
class MetricPolicyRegistry:
    signal_policies: tuple[MetricSignalPolicy, ...]
    discovery_policy_rules: tuple[DiscoveryPolicyRule, ...]
    factor_graph_policies: tuple[FactorGraphPolicy, ...]
    root_cause_policies: tuple[RootCausePolicy, ...] = ()

    def signal_type(
        self,
        *,
        metric_id: str,
        dimension: str,
        root_cause_type: str | None = None,
    ) -> SignalType:
        policies = [
            policy
            for policy in self.signal_policies
            if policy.metric_id == metric_id
            and policy.dimension == dimension
            and (
                (root_cause_type is None and policy.root_cause_type is None)
                or (root_cause_type is not None and policy.root_cause_type in {root_cause_type, None})
            )
        ]
        if root_cause_type is None and not policies:
            policies = [
                policy
                for policy in self.signal_policies
                if policy.metric_id == metric_id and policy.dimension == dimension
            ]
        if root_cause_type is not None:
            exact = [policy for policy in policies if policy.root_cause_type == root_cause_type]
            if exact:
                policies = exact
        signal_types = {policy.signal_type for policy in policies}
        if len(signal_types) != 1:
            raise PolicyRegistryError(
                "SIGNAL_POLICY_MISSING",
                f"signal policy missing for metric_id={metric_id} dimension={dimension}",
            )
        return next(iter(signal_types))

    def discovery_policy(
        self,
        *,
        metric_id: str,
        question_family: str,
        analysis_strategy: str,
    ) -> DiscoveryPolicy:
        exact = self._discovery_policy_rule(
            metric_id=metric_id,
            question_family=question_family,
            analysis_strategy=analysis_strategy,
        )
        if exact is not None:
            return exact.to_policy()
        metric_default = self._discovery_policy_rule(
            metric_id=metric_id,
            question_family=None,
            analysis_strategy=None,
        )
        if metric_default is not None:
            return metric_default.to_policy()
        return DiscoveryPolicy()

    def factor_graph_policy(self, metric_id: str) -> FactorGraphPolicy:
        for policy in self.factor_graph_policies:
            if policy.metric_id == metric_id:
                return policy
        raise PolicyRegistryError("FACTOR_GRAPH_POLICY_MISSING", f"factor graph policy missing for metric_id={metric_id}")

    def root_cause_type(self, *, metric_id: str, dimension: str, signal_type: str | None = None) -> str:
        policies = [
            policy
            for policy in self.root_cause_policies
            if policy.metric_id == metric_id and policy.dimension == dimension
        ]
        if signal_type is not None:
            exact = [policy for policy in policies if policy.signal_type == signal_type]
            if exact:
                policies = exact
            else:
                policies = [policy for policy in policies if policy.signal_type is None]
        else:
            policies = [policy for policy in policies if policy.signal_type is None]
        root_cause_types = {policy.root_cause_type for policy in policies}
        if len(root_cause_types) != 1:
            raise PolicyRegistryError(
                "ROOT_CAUSE_POLICY_MISSING",
                f"root cause policy missing for metric_id={metric_id} dimension={dimension}",
            )
        return next(iter(root_cause_types))

    def _discovery_policy_rule(
        self,
        *,
        metric_id: str,
        question_family: str | None,
        analysis_strategy: str | None,
    ) -> DiscoveryPolicyRule | None:
        for rule in self.discovery_policy_rules:
            if (
                rule.metric_id == metric_id
                and rule.question_family == question_family
                and rule.analysis_strategy == analysis_strategy
            ):
                return rule
        return None


DEFAULT_SIGNAL_POLICIES: tuple[MetricSignalPolicy, ...] = (
    MetricSignalPolicy(
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="inventory",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="inventory",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="inventory",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="refund_quality",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.UV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.PAY_CVR.value,
        dimension=DimensionId.DEVICE.value,
        signal_type="conversion",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.PAY_CVR.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="conversion",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.REFUND_RATE.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="refund_quality",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.REFUND_RATE.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="refund_quality",
    ),
    MetricSignalPolicy(
        metric_id=MetricId.COMPLAINT_RATE.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="refund_quality",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.CAMPAIGN_TRAFFIC_DROP.value,
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="conversion",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.CAMPAIGN_TRAFFIC_DROP.value,
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="conversion",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.STOCKOUT.value,
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="inventory",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.CAMPAIGN_TRAFFIC_DROP.value,
        metric_id=MetricId.UV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.STOCKOUT.value,
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="refund_quality",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="refund_quality",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.STOCKOUT.value,
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="inventory",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.STOCKOUT.value,
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="inventory",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.STOCKOUT.value,
        metric_id=MetricId.STOCKOUT_RATE.value,
        dimension=DimensionId.WAREHOUSE.value,
        signal_type="inventory",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
        metric_id=MetricId.PAY_CVR.value,
        dimension=DimensionId.DEVICE.value,
        signal_type="conversion",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
        metric_id=MetricId.PAY_CVR.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="conversion",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
        metric_id=MetricId.REFUND_RATE.value,
        dimension=DimensionId.PRODUCT.value,
        signal_type="refund_quality",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
        metric_id=MetricId.REFUND_RATE.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="refund_quality",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
        metric_id=MetricId.COMPLAINT_RATE.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="refund_quality",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.INTERACTION_CHANNEL_CATEGORY.value,
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="interaction",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.INTERACTION_CHANNEL_CATEGORY.value,
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="interaction",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.INTERACTION_CHANNEL_CATEGORY.value,
        metric_id=MetricId.UV.value,
        dimension=DimensionId.CHANNEL.value,
        signal_type="interaction",
    ),
    MetricSignalPolicy(
        root_cause_type=RootCauseType.INTERACTION_CHANNEL_CATEGORY.value,
        metric_id=MetricId.UV.value,
        dimension=DimensionId.CATEGORY.value,
        signal_type="interaction",
    ),
)

GMV_STANDARD_DISCOVERY_LANES: tuple[DiscoveryLane, ...] = (
    DiscoveryLane(
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
        evidence_alias="E4_channel",
    ),
    DiscoveryLane(
        dimension=DimensionId.CHANNEL.value,
        signal_type="conversion",
        element_selection="signal_anomaly",
        evidence_alias="E4_channel_conversion",
        selection_alias="E_select_ch_conversion",
        signal_evidence_alias="E3_ch_conversion",
        signal_filter_mode="none",
    ),
    DiscoveryLane(
        dimension=DimensionId.CATEGORY.value,
        signal_type="inventory",
        evidence_alias="E4_category",
    ),
    DiscoveryLane(
        dimension=DimensionId.PRODUCT.value,
        signal_type="inventory",
        evidence_alias="E4_product",
    ),
    DiscoveryLane(
        dimension=DimensionId.CHANNEL.value,
        signal_type="interaction",
        element_selection="signal_anomaly",
        evidence_alias="E4_channel_interaction",
        selection_alias="E_select_ch_interaction",
        signal_evidence_alias="E3_ch_interaction",
        signal_filter_mode="none",
    ),
    DiscoveryLane(
        dimension=DimensionId.CATEGORY.value,
        signal_type="interaction",
        element_selection="signal_anomaly",
        evidence_alias="E4_category_interaction",
        selection_alias="E_select_cat_interaction",
        signal_evidence_alias="E3_cat_interaction",
        signal_filter_mode="none",
    ),
)

GMV_SIGNAL_FIRST_DISCOVERY_LANES: tuple[DiscoveryLane, ...] = (
    DiscoveryLane(
        dimension=DimensionId.CHANNEL.value,
        signal_type="campaign",
        element_selection="signal_anomaly",
        evidence_alias="E4_channel",
    ),
    *GMV_STANDARD_DISCOVERY_LANES[1:],
)

DEFAULT_DISCOVERY_POLICY_RULES: tuple[DiscoveryPolicyRule, ...] = (
    DiscoveryPolicyRule(
        metric_id=MetricId.UV.value,
        question_family=None,
        analysis_strategy=None,
        required_drilldowns=(DimensionId.CHANNEL.value,),
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="campaign",
        enforce_first_signal_top_candidate=True,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.PAY_CVR.value,
        question_family=None,
        analysis_strategy=None,
        required_drilldowns=(DimensionId.CHANNEL.value, DimensionId.DEVICE.value),
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="conversion",
        enforce_first_signal_top_candidate=True,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.NET_GMV.value,
        question_family="net_gmv_drop",
        analysis_strategy="standard",
        required_drilldowns=(DimensionId.CHANNEL.value, DimensionId.CATEGORY.value),
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="campaign",
        scope_mode="explicit_multi_driver",
        explicit_scope_dimensions=(DimensionId.CHANNEL.value,),
        lanes=(
            DiscoveryLane(
                dimension=DimensionId.CHANNEL.value,
                signal_type="campaign",
                element_binding="explicit_scope",
                evidence_alias="E4_channel",
            ),
            DiscoveryLane(
                dimension=DimensionId.CATEGORY.value,
                signal_type="inventory",
                element_binding="dynamic",
                evidence_alias="E4_category",
                signal_filter_mode="none",
            ),
            DiscoveryLane(
                dimension=DimensionId.CHANNEL.value,
                signal_type="conversion",
                element_binding="dynamic",
                element_selection="signal_anomaly",
                evidence_alias="E4_channel_conversion",
                selection_alias="E_select_ch_conversion",
                signal_evidence_alias="E3_ch_conversion",
                signal_filter_mode="none",
                explicit_scope_policy="global_explanatory",
            ),
        ),
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.REFUND_RATE.value,
        question_family=None,
        analysis_strategy=None,
        required_drilldowns=(DimensionId.PRODUCT.value,),
        first_signal_dimension=DimensionId.PRODUCT.value,
        first_signal_type="refund_quality",
        element_selection="signal_level",
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="gmv_drop",
        analysis_strategy="standard",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="campaign",
        enforce_first_signal_top_candidate=True,
        lanes=GMV_STANDARD_DISCOVERY_LANES,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="gmv_drop",
        analysis_strategy="channel_first",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="campaign",
        enforce_first_signal_top_candidate=True,
        lanes=GMV_STANDARD_DISCOVERY_LANES,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="gmv_drop",
        analysis_strategy="signal_first",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="campaign",
        element_selection="signal_anomaly",
        lanes=GMV_SIGNAL_FIRST_DISCOVERY_LANES,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="gmv_drop",
        analysis_strategy="product_first",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.PRODUCT.value,
        first_signal_type="inventory",
        enforce_first_signal_top_candidate=True,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="channel_gmv_anomaly",
        analysis_strategy="standard",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="campaign",
        enforce_first_signal_top_candidate=True,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="channel_gmv_anomaly",
        analysis_strategy="channel_first",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="campaign",
        enforce_first_signal_top_candidate=True,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="channel_gmv_anomaly",
        analysis_strategy="product_first",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="campaign",
        enforce_first_signal_top_candidate=True,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="channel_gmv_anomaly",
        analysis_strategy="signal_first",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="campaign",
        element_selection="signal_anomaly",
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="category_gmv_anomaly",
        analysis_strategy="standard",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CATEGORY.value,
        first_signal_type="inventory",
        enforce_first_signal_top_candidate=True,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="category_gmv_anomaly",
        analysis_strategy="channel_first",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CATEGORY.value,
        first_signal_type="inventory",
        enforce_first_signal_top_candidate=True,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="category_gmv_anomaly",
        analysis_strategy="product_first",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CATEGORY.value,
        first_signal_type="inventory",
        enforce_first_signal_top_candidate=True,
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="category_gmv_anomaly",
        analysis_strategy="signal_first",
        required_drilldowns=GMV_DISCOVERY_REQUIRED_DRILLDOWNS,
        first_signal_dimension=DimensionId.CATEGORY.value,
        first_signal_type="inventory",
        element_selection="signal_anomaly",
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.GMV.value,
        question_family="interaction_gmv_anomaly",
        analysis_strategy="standard",
        required_drilldowns=(DimensionId.CHANNEL.value, DimensionId.CATEGORY.value),
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="interaction",
        element_selection="signal_anomaly",
    ),
    DiscoveryPolicyRule(
        metric_id=MetricId.UV.value,
        question_family="interaction_uv_anomaly",
        analysis_strategy="standard",
        required_drilldowns=(DimensionId.CHANNEL.value, DimensionId.CATEGORY.value),
        first_signal_dimension=DimensionId.CHANNEL.value,
        first_signal_type="interaction",
        element_selection="signal_anomaly",
    ),
)

DEFAULT_FACTOR_GRAPH_POLICIES: tuple[FactorGraphPolicy, ...] = (
    FactorGraphPolicy(
        metric_id=MetricId.GMV.value,
        graph_type="uv_pay_cvr_aov",
        factor_metrics=(MetricId.UV.value, MetricId.PAY_CVR.value),
    ),
    FactorGraphPolicy(
        metric_id=MetricId.NET_GMV.value,
        graph_type="net_gmv_chain",
        factor_metrics=(MetricId.GMV.value, MetricId.NET_GMV.value),
    ),
    FactorGraphPolicy(metric_id=MetricId.UV.value, graph_type="dimension_delta", factor_metrics=(MetricId.UV.value,)),
    FactorGraphPolicy(
        metric_id=MetricId.PAY_CVR.value,
        graph_type="dimension_delta",
        factor_metrics=(MetricId.PAY_CVR.value,),
    ),
    FactorGraphPolicy(
        metric_id=MetricId.REFUND_RATE.value,
        graph_type="dimension_delta",
        factor_metrics=(MetricId.REFUND_RATE.value,),
    ),
    FactorGraphPolicy(
        metric_id=MetricId.STOCKOUT_RATE.value,
        graph_type="dimension_delta",
        factor_metrics=(MetricId.STOCKOUT_RATE.value,),
    ),
    FactorGraphPolicy(
        metric_id=MetricId.COMPLAINT_RATE.value,
        graph_type="dimension_delta",
        factor_metrics=(MetricId.COMPLAINT_RATE.value,),
    ),
    FactorGraphPolicy(metric_id=MetricId.AOV.value, graph_type="dimension_delta", factor_metrics=(MetricId.AOV.value,)),
)

DEFAULT_ROOT_CAUSE_POLICIES: tuple[RootCausePolicy, ...] = (
    RootCausePolicy(
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CHANNEL.value,
        root_cause_type=RootCauseType.CAMPAIGN_TRAFFIC_DROP.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CHANNEL.value,
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
        signal_type="conversion",
    ),
    RootCausePolicy(
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CATEGORY.value,
        root_cause_type=RootCauseType.STOCKOUT.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.PRODUCT.value,
        root_cause_type=RootCauseType.STOCKOUT.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CHANNEL.value,
        root_cause_type=RootCauseType.CAMPAIGN_TRAFFIC_DROP.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CHANNEL.value,
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
        signal_type="conversion",
    ),
    RootCausePolicy(
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CATEGORY.value,
        root_cause_type=RootCauseType.STOCKOUT.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CATEGORY.value,
        root_cause_type=RootCauseType.STOCKOUT.value,
        signal_type="inventory",
    ),
    RootCausePolicy(
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.CHANNEL.value,
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
        signal_type="refund_quality",
    ),
    RootCausePolicy(
        metric_id=MetricId.NET_GMV.value,
        dimension=DimensionId.PRODUCT.value,
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.UV.value,
        dimension=DimensionId.CHANNEL.value,
        root_cause_type=RootCauseType.CAMPAIGN_TRAFFIC_DROP.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.UV.value,
        dimension=DimensionId.CATEGORY.value,
        root_cause_type=RootCauseType.CAMPAIGN_TRAFFIC_DROP.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.PAY_CVR.value,
        dimension=DimensionId.DEVICE.value,
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.PAY_CVR.value,
        dimension=DimensionId.CHANNEL.value,
        root_cause_type=RootCauseType.CONVERSION_DROP.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.REFUND_RATE.value,
        dimension=DimensionId.PRODUCT.value,
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.REFUND_RATE.value,
        dimension=DimensionId.CATEGORY.value,
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.REFUND_RATE.value,
        dimension=DimensionId.CHANNEL.value,
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.STOCKOUT_RATE.value,
        dimension=DimensionId.WAREHOUSE.value,
        root_cause_type=RootCauseType.STOCKOUT.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.STOCKOUT_RATE.value,
        dimension=DimensionId.PRODUCT.value,
        root_cause_type=RootCauseType.STOCKOUT.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.STOCKOUT_RATE.value,
        dimension=DimensionId.CATEGORY.value,
        root_cause_type=RootCauseType.STOCKOUT.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.COMPLAINT_RATE.value,
        dimension=DimensionId.CATEGORY.value,
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.COMPLAINT_RATE.value,
        dimension=DimensionId.PRODUCT.value,
        root_cause_type=RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value,
    ),
    RootCausePolicy(
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CHANNEL.value,
        root_cause_type=RootCauseType.INTERACTION_CHANNEL_CATEGORY.value,
        signal_type="interaction",
    ),
    RootCausePolicy(
        metric_id=MetricId.GMV.value,
        dimension=DimensionId.CATEGORY.value,
        root_cause_type=RootCauseType.INTERACTION_CHANNEL_CATEGORY.value,
        signal_type="interaction",
    ),
    RootCausePolicy(
        metric_id=MetricId.UV.value,
        dimension=DimensionId.CHANNEL.value,
        root_cause_type=RootCauseType.INTERACTION_CHANNEL_CATEGORY.value,
        signal_type="interaction",
    ),
    RootCausePolicy(
        metric_id=MetricId.UV.value,
        dimension=DimensionId.CATEGORY.value,
        root_cause_type=RootCauseType.INTERACTION_CHANNEL_CATEGORY.value,
        signal_type="interaction",
    ),
)

DEFAULT_POLICY_REGISTRY = MetricPolicyRegistry(
    signal_policies=DEFAULT_SIGNAL_POLICIES,
    discovery_policy_rules=DEFAULT_DISCOVERY_POLICY_RULES,
    factor_graph_policies=DEFAULT_FACTOR_GRAPH_POLICIES,
    root_cause_policies=DEFAULT_ROOT_CAUSE_POLICIES,
)


def select_signal_type(
    *,
    metric_id: str,
    dimension: str,
    root_cause_type: str,
    registry: MetricPolicyRegistry = DEFAULT_POLICY_REGISTRY,
    validate_dimensions: AllowedDimensionsValidator | None = None,
) -> SignalType:
    _validate_dimensions(validate_dimensions, metric_id, (dimension,))
    return registry.signal_type(metric_id=metric_id, dimension=dimension, root_cause_type=root_cause_type)


def select_signal_type_for_metric_dimension(
    *,
    metric_id: str,
    dimension: str,
    registry: MetricPolicyRegistry = DEFAULT_POLICY_REGISTRY,
    validate_dimensions: AllowedDimensionsValidator | None = None,
) -> SignalType:
    _validate_dimensions(validate_dimensions, metric_id, (dimension,))
    return registry.signal_type(metric_id=metric_id, dimension=dimension)


def discovery_policy_from_intent(
    parsed_intent: Any,
    *,
    registry: MetricPolicyRegistry = DEFAULT_POLICY_REGISTRY,
    validate_dimensions: AllowedDimensionsValidator | None = None,
) -> DiscoveryPolicy:
    scoped_dimensions = _parsed_scope_dimensions(parsed_intent)
    if scoped_dimensions:
        _validate_dimensions(validate_dimensions, parsed_intent.metric_id, scoped_dimensions)
        policy = registry.discovery_policy(
            metric_id=parsed_intent.metric_id,
            question_family=parsed_intent.question_family,
            analysis_strategy=parsed_intent.analysis_strategy,
        )
        if (
            policy.scope_mode == "explicit_multi_driver"
            and policy.explicit_scope_dimensions
            and set(scoped_dimensions).issubset(set(policy.explicit_scope_dimensions))
        ):
            _validate_dimensions(validate_dimensions, parsed_intent.metric_id, _policy_dimensions(policy))
            return policy
        return DiscoveryPolicy()
    policy = registry.discovery_policy(
        metric_id=parsed_intent.metric_id,
        question_family=parsed_intent.question_family,
        analysis_strategy=parsed_intent.analysis_strategy,
    )
    _validate_dimensions(validate_dimensions, parsed_intent.metric_id, _policy_dimensions(policy))
    return policy


def factor_graph_policy_for_metric(
    metric_id: str,
    *,
    registry: MetricPolicyRegistry = DEFAULT_POLICY_REGISTRY,
) -> FactorGraphPolicy:
    return registry.factor_graph_policy(metric_id)


def root_cause_type_for_metric_dimension(
    *,
    metric_id: str,
    dimension: str,
    signal_type: str | None = None,
    registry: MetricPolicyRegistry = DEFAULT_POLICY_REGISTRY,
    validate_dimensions: AllowedDimensionsValidator | None = None,
) -> str:
    _validate_dimensions(validate_dimensions, metric_id, (dimension,))
    return registry.root_cause_type(metric_id=metric_id, dimension=dimension, signal_type=signal_type)


def allowed_dimensions_validator_from_metric_definition(
    get_metric_definition: MetricDefinitionProvider,
) -> AllowedDimensionsValidator:
    def validate(metric_id: str, dimensions: tuple[str, ...]) -> None:
        definition = get_metric_definition(metric_id)
        raw_allowed = getattr(definition, "allowed_dimensions", None)
        if raw_allowed is None:
            raise PolicyRegistryError(
                "METRIC_METADATA_INVALID",
                f"metric definition missing allowed_dimensions for metric_id={metric_id}",
            )
        allowed = {str(dimension) for dimension in raw_allowed}
        invalid = sorted({dimension for dimension in dimensions if dimension not in allowed})
        if invalid:
            raise PolicyRegistryError(
                "DIMENSION_NOT_ALLOWED",
                f"dimensions not allowed for metric_id={metric_id}: {', '.join(invalid)}",
            )

    return validate


def _validate_dimensions(
    validate_dimensions: AllowedDimensionsValidator | None,
    metric_id: str,
    dimensions: tuple[str, ...],
) -> None:
    if validate_dimensions is None:
        return
    validate_dimensions(metric_id, _ordered_unique(dimensions))


def _parsed_scope_dimensions(parsed_intent: Any) -> tuple[str, ...]:
    dimensions: list[str] = []
    if getattr(parsed_intent, "dimension", None) is not None and getattr(parsed_intent, "element", None) is not None:
        dimensions.append(str(parsed_intent.dimension))
    filters = getattr(parsed_intent, "filters", {})
    if isinstance(filters, dict):
        dimensions.extend(str(key) for key in filters)
    return _ordered_unique(tuple(dimensions))


def _policy_dimensions(policy: DiscoveryPolicy) -> tuple[str, ...]:
    dimensions: list[str] = [*policy.required_drilldowns]
    if policy.first_signal_dimension is not None:
        dimensions.append(policy.first_signal_dimension)
    dimensions.extend(lane.dimension for lane in policy.lanes)
    return _ordered_unique(tuple(dimensions))


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return tuple(unique)

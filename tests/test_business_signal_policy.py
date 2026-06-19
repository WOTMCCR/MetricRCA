from __future__ import annotations

import pytest

from metric_rca.business.discovery_policy import discovery_policy_from_intent
from metric_rca.business.policy_registry import (
    DiscoveryPolicyRule,
    FactorGraphPolicy,
    MetricPolicyRegistry,
    MetricSignalPolicy,
    RootCausePolicy,
    allowed_dimensions_validator_from_metric_definition,
    factor_graph_policy_for_metric,
    root_cause_type_for_metric_dimension,
)
from metric_rca.business.signal_policy import (
    select_signal_type,
    select_signal_type_for_metric_dimension,
)
from metric_rca.domain.enums import RootCauseType
from metric_rca.domain.models import MetricDefinition
from metric_rca.services.metric_contracts import ParsedIntent


def test_root_cause_type_enum_includes_cross_dimension_interaction() -> None:
    assert RootCauseType.INTERACTION_CHANNEL_CATEGORY.value == "interaction_channel_category"


def test_signal_policy_selects_related_signal_for_metric_dimension() -> None:
    assert select_signal_type_for_metric_dimension(metric_id="uv", dimension="channel") == "campaign"
    assert select_signal_type_for_metric_dimension(metric_id="refund_rate", dimension="product") == "refund_quality"


def test_signal_policy_selects_related_signal_for_root_cause() -> None:
    assert (
        select_signal_type(
            metric_id="pay_cvr",
            dimension="device",
            root_cause_type="conversion_drop",
        )
        == "conversion"
    )


@pytest.mark.parametrize(
    ("metric_id", "dimension"),
    [
        ("gmv", "channel"),
        ("gmv", "category"),
        ("uv", "channel"),
        ("uv", "category"),
    ],
)
def test_interaction_signal_policy_is_selected_by_interaction_root_cause(metric_id: str, dimension: str) -> None:
    assert (
        select_signal_type(
            metric_id=metric_id,
            dimension=dimension,
            root_cause_type=RootCauseType.INTERACTION_CHANNEL_CATEGORY.value,
        )
        == "interaction"
    )
    assert (
        root_cause_type_for_metric_dimension(
            metric_id=metric_id,
            dimension=dimension,
            signal_type="interaction",
        )
        == RootCauseType.INTERACTION_CHANNEL_CATEGORY.value
    )


def test_default_signal_policy_stays_single_cause_when_no_interaction_root_cause_is_requested() -> None:
    assert select_signal_type_for_metric_dimension(metric_id="gmv", dimension="channel") == "campaign"
    assert select_signal_type_for_metric_dimension(metric_id="gmv", dimension="category") == "inventory"
    assert root_cause_type_for_metric_dimension(metric_id="gmv", dimension="channel") == "campaign_traffic_drop"
    assert root_cause_type_for_metric_dimension(metric_id="gmv", dimension="category") == "stockout"
    assert root_cause_type_for_metric_dimension(metric_id="uv", dimension="category") == "campaign_traffic_drop"


def test_signal_policy_fails_fast_for_missing_metric_dimension_rule() -> None:
    with pytest.raises(ValueError, match="SIGNAL_POLICY_MISSING"):
        select_signal_type_for_metric_dimension(metric_id="refund_rate", dimension="warehouse")


def test_signal_policy_reads_custom_registry_and_validates_metric_dimensions() -> None:
    registry = MetricPolicyRegistry(
        signal_policies=(
            MetricSignalPolicy(
                metric_id="custom_metric",
                dimension="warehouse",
                signal_type="inventory",
                root_cause_type="stockout",
            ),
        ),
        discovery_policy_rules=(),
        factor_graph_policies=(),
    )
    validator = allowed_dimensions_validator_from_metric_definition(
        lambda metric_id: MetricDefinition(
            metric_id=metric_id,
            display_name=metric_id,
            formula="test",
            metric_family="rate_family",
            source_table="fact_inventory",
            allowed_dimensions=["warehouse"],
        )
    )

    assert (
        select_signal_type_for_metric_dimension(
            metric_id="custom_metric",
            dimension="warehouse",
            registry=registry,
            validate_dimensions=validator,
        )
        == "inventory"
    )


def test_signal_policy_rejects_registry_rule_not_allowed_by_metric_definition() -> None:
    validator = allowed_dimensions_validator_from_metric_definition(
        lambda metric_id: MetricDefinition(
            metric_id=metric_id,
            display_name=metric_id,
            formula="test",
            metric_family="gmv_family",
            source_table="fact_order",
            allowed_dimensions=["channel"],
        )
    )

    with pytest.raises(ValueError, match="DIMENSION_NOT_ALLOWED"):
        select_signal_type_for_metric_dimension(
            metric_id="gmv",
            dimension="product",
            validate_dimensions=validator,
        )


def test_discovery_policy_reads_registry_and_validates_required_drilldowns() -> None:
    registry = MetricPolicyRegistry(
        signal_policies=(),
        discovery_policy_rules=(
            DiscoveryPolicyRule(
                metric_id="custom_metric",
                question_family="gmv_drop",
                analysis_strategy="standard",
                required_drilldowns=("warehouse",),
                first_signal_dimension="warehouse",
                first_signal_type="inventory",
            ),
        ),
        factor_graph_policies=(),
    )
    validator = allowed_dimensions_validator_from_metric_definition(
        lambda metric_id: MetricDefinition(
            metric_id=metric_id,
            display_name=metric_id,
            formula="test",
            metric_family="gmv_family",
            source_table="fact_inventory",
            allowed_dimensions=["warehouse"],
        )
    )
    parsed = ParsedIntent(
        metric_id="custom_metric",
        target_date="2026-06-05",
        question_family="gmv_drop",
    )

    policy = discovery_policy_from_intent(parsed, registry=registry, validate_dimensions=validator)

    assert policy.required_drilldowns == ("warehouse",)
    assert policy.first_signal_dimension == "warehouse"
    assert policy.first_signal_type == "inventory"


def test_discovery_policy_rejects_required_drilldown_not_allowed_by_metric_definition() -> None:
    validator = allowed_dimensions_validator_from_metric_definition(
        lambda metric_id: MetricDefinition(
            metric_id=metric_id,
            display_name=metric_id,
            formula="test",
            metric_family="gmv_family",
            source_table="fact_order",
            allowed_dimensions=["channel", "category"],
        )
    )
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date="2026-06-05",
        question_family="gmv_drop",
        analysis_strategy="product_first",
    )

    with pytest.raises(ValueError, match="DIMENSION_NOT_ALLOWED"):
        discovery_policy_from_intent(parsed, validate_dimensions=validator)


@pytest.mark.parametrize(
    ("question_family", "analysis_strategy", "expected_dimension", "expected_signal_type"),
    [
        ("channel_gmv_anomaly", "channel_first", "channel", "campaign"),
        ("channel_gmv_anomaly", "product_first", "channel", "campaign"),
        ("channel_gmv_anomaly", "signal_first", "channel", "campaign"),
        ("category_gmv_anomaly", "channel_first", "category", "inventory"),
        ("category_gmv_anomaly", "product_first", "category", "inventory"),
        ("category_gmv_anomaly", "signal_first", "category", "inventory"),
    ],
)
def test_gmv_anomaly_discovery_policy_is_family_specific_across_analysis_strategies(
    question_family: str,
    analysis_strategy: str,
    expected_dimension: str,
    expected_signal_type: str,
) -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date="2026-06-05",
        question_family=question_family,
        analysis_strategy=analysis_strategy,
    )

    policy = discovery_policy_from_intent(parsed)

    assert policy.first_signal_dimension == expected_dimension
    assert policy.first_signal_type == expected_signal_type


@pytest.mark.parametrize(
    ("metric_id", "question_family"),
    [
        ("gmv", "interaction_gmv_anomaly"),
        ("uv", "interaction_uv_anomaly"),
    ],
)
def test_interaction_discovery_policy_enables_cross_dimension_analysis(metric_id: str, question_family: str) -> None:
    parsed = ParsedIntent(
        metric_id=metric_id,
        target_date="2026-05-31",
        question_family=question_family,
        analysis_strategy="standard",
    )

    policy = discovery_policy_from_intent(parsed)

    assert policy.required_drilldowns == ("channel", "category")
    assert policy.first_signal_dimension == "channel"
    assert policy.first_signal_type == "interaction"
    assert policy.element_selection == "signal_anomaly"


def test_pay_cvr_discovery_policy_uses_channel_and_device_conversion_lanes() -> None:
    parsed = ParsedIntent(
        metric_id="pay_cvr",
        target_date="2026-05-28",
        question_family="pay_cvr_drop",
        analysis_strategy="standard",
    )

    policy = discovery_policy_from_intent(parsed)

    assert policy.required_drilldowns == ("channel", "device")
    assert policy.first_signal_dimension == "channel"
    assert policy.first_signal_type == "conversion"


def test_net_gmv_explicit_channel_policy_declares_multi_driver_lanes() -> None:
    parsed = ParsedIntent(
        metric_id="net_gmv",
        target_date="2026-05-29",
        question_family="net_gmv_drop",
        analysis_strategy="standard",
        dimension="channel",
        element="paid_ads",
    )

    policy = discovery_policy_from_intent(parsed)

    assert policy.scope_mode == "explicit_multi_driver"
    assert policy.required_drilldowns == ("channel", "category")
    assert [
        (
            lane.dimension,
            lane.signal_type,
            lane.element_binding,
            lane.evidence_alias,
            lane.signal_filter_mode,
        )
        for lane in policy.lanes
        ] == [
            ("channel", "campaign", "explicit_scope", "E4_channel", "inherit"),
            ("category", "inventory", "dynamic", "E4_category", "none"),
            ("channel", "conversion", "explicit_scope", "E4_channel_conversion", "inherit"),
        ]


def test_factor_graph_policy_reads_registry() -> None:
    registry = MetricPolicyRegistry(
        signal_policies=(),
        discovery_policy_rules=(),
        factor_graph_policies=(
            FactorGraphPolicy(
                metric_id="custom_metric",
                graph_type="dimension_delta",
                factor_metrics=("custom_metric",),
            ),
        ),
    )

    assert factor_graph_policy_for_metric("gmv").graph_type == "uv_pay_cvr_aov"
    assert factor_graph_policy_for_metric("net_gmv").graph_type == "net_gmv_chain"
    assert factor_graph_policy_for_metric("custom_metric", registry=registry).factor_metrics == ("custom_metric",)


def test_root_cause_policy_reads_registry_and_validates_dimensions() -> None:
    registry = MetricPolicyRegistry(
        signal_policies=(),
        discovery_policy_rules=(),
        factor_graph_policies=(),
        root_cause_policies=(
            RootCausePolicy(
                metric_id="custom_metric",
                dimension="warehouse",
                root_cause_type="stockout",
            ),
        ),
    )
    validator = allowed_dimensions_validator_from_metric_definition(
        lambda metric_id: MetricDefinition(
            metric_id=metric_id,
            display_name=metric_id,
            formula="test",
            metric_family="rate_family",
            source_table="fact_inventory",
            allowed_dimensions=["warehouse"],
        )
    )

    assert (
        root_cause_type_for_metric_dimension(
            metric_id="custom_metric",
            dimension="warehouse",
            registry=registry,
            validate_dimensions=validator,
        )
        == "stockout"
    )
    assert root_cause_type_for_metric_dimension(metric_id="gmv", dimension="channel") == "campaign_traffic_drop"
    assert (
        root_cause_type_for_metric_dimension(
            metric_id="net_gmv",
            dimension="channel",
            signal_type="refund_quality",
        )
        == "complaint_or_quality_issue"
    )
    assert (
        root_cause_type_for_metric_dimension(
            metric_id="net_gmv",
            dimension="category",
            signal_type="inventory",
        )
        == "stockout"
    )
    assert root_cause_type_for_metric_dimension(metric_id="net_gmv", dimension="category") == "stockout"
    assert (
        root_cause_type_for_metric_dimension(
            metric_id="net_gmv",
            dimension="channel",
            signal_type="conversion",
        )
        == "conversion_drop"
    )
    assert root_cause_type_for_metric_dimension(metric_id="net_gmv", dimension="channel") == "campaign_traffic_drop"


def test_root_cause_policy_fails_fast_when_missing_or_dimension_not_allowed() -> None:
    with pytest.raises(ValueError, match="ROOT_CAUSE_POLICY_MISSING"):
        root_cause_type_for_metric_dimension(metric_id="gmv", dimension="warehouse")

    validator = allowed_dimensions_validator_from_metric_definition(
        lambda metric_id: MetricDefinition(
            metric_id=metric_id,
            display_name=metric_id,
            formula="test",
            metric_family="gmv_family",
            source_table="fact_order",
            allowed_dimensions=["channel"],
        )
    )
    with pytest.raises(ValueError, match="DIMENSION_NOT_ALLOWED"):
        root_cause_type_for_metric_dimension(metric_id="gmv", dimension="product", validate_dimensions=validator)

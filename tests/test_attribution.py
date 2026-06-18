from __future__ import annotations

from datetime import date

import pytest

from metric_rca.business.policy_registry import MetricPolicyRegistry, RootCausePolicy
from metric_rca.domain.models import MetricDefinition
import metric_rca.services.attribution_service as attribution_service
from metric_rca.services.attribution_service import (
    compute_dimension_contribution,
    compute_gmv_decomposition,
    compute_net_gmv_components,
    rank_root_causes,
)


def _metric(metric_id: str = "gmv", *, higher_is_better: bool = True) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        display_name=metric_id,
        formula="test",
        metric_family="gmv_family",
        higher_is_better=higher_is_better,
        allowed_dimensions=["channel", "category", "device", "product"],
        source_table="fact_order",
    )


def _baseline(dimension: str, values: dict[str, float]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for element, value in values.items():
        for index, day in enumerate([29, 22, 15, 8]):
            jitter = value * 0.01 if value < 1 else float(index % 2)
            rows.append(
                {
                    "business_date": date(2026, 5, day),
                    dimension: element,
                    "metric_value": value + jitter,
                }
            )
    return rows


def test_attribution_paid_ads_contribution_top1_campaign_traffic_drop() -> None:
    result = compute_dimension_contribution(
        metric_definition=_metric("gmv"),
        dimension="channel",
        current_rows=[
            {"channel": "paid_ads", "metric_value": 20.0},
            {"channel": "organic", "metric_value": 95.0},
        ],
        baseline_rows=_baseline("channel", {"paid_ads": 100.0, "organic": 100.0}),
        evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        top_threshold=0.60,
    )

    assert result.ok is True
    assert result.candidates[0].root_cause_type == "campaign_traffic_drop"
    assert result.candidates[0].element == "paid_ads"
    assert result.candidates[0].contribution_pct >= 0.80
    assert result.candidates[0].evidence_ids == ["run-1:E1", "run-1:E2", "run-1:E3"]


def test_attribution_stockout_electronics_top1_stockout() -> None:
    result = compute_dimension_contribution(
        metric_definition=_metric("gmv"),
        dimension="category",
        current_rows=[
            {"category": "electronics", "metric_value": 25.0},
            {"category": "fashion", "metric_value": 92.0},
        ],
        baseline_rows=_baseline("category", {"electronics": 100.0, "fashion": 100.0}),
        evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        top_threshold=0.60,
    )

    assert result.ok is True
    assert result.candidates[0].root_cause_type == "stockout"
    assert result.candidates[0].element == "electronics"


def test_attribution_mobile_cvr_top1_conversion_drop() -> None:
    result = compute_dimension_contribution(
        metric_definition=_metric("pay_cvr"),
        dimension="device",
        current_rows=[
            {"device": "mobile", "metric_value": 0.03},
            {"device": "desktop", "metric_value": 0.08},
        ],
        baseline_rows=_baseline("device", {"mobile": 0.08, "desktop": 0.08}),
        evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        top_threshold=0.60,
    )

    assert result.ok is True
    assert result.candidates[0].root_cause_type == "conversion_drop"
    assert result.candidates[0].element == "mobile"


def test_attribution_distributed_drop_still_returns_candidates_for_adtributor_ranker() -> None:
    result = compute_dimension_contribution(
        metric_definition=_metric("gmv"),
        dimension="channel",
        current_rows=[
            {"channel": "paid_ads", "metric_value": 60.0},
            {"channel": "social", "metric_value": 70.0},
            {"channel": "organic", "metric_value": 80.0},
        ],
        baseline_rows=_baseline("channel", {"paid_ads": 100.0, "social": 100.0, "organic": 100.0}),
        evidence_ids=["run-1:E1"],
        top_threshold=0.60,
    )

    assert result.ok is True
    assert result.coverage < 0.60
    assert [candidate.element for candidate in result.candidates] == ["paid_ads", "social", "organic"]
    assert {candidate.verdict for candidate in result.candidates} == {"likely"}


def test_attribution_uses_relative_element_severity_when_contribution_is_close() -> None:
    result = compute_dimension_contribution(
        metric_definition=_metric("uv"),
        dimension="channel",
        current_rows=[
            {"channel": "paid_ads", "metric_value": 889.0},
            {"channel": "organic", "metric_value": 403.0},
        ],
        baseline_rows=_baseline("channel", {"paid_ads": 2332.5, "organic": 1748.25}),
        evidence_ids=["run-1:E1"],
        top_threshold=0.60,
    )

    assert result.ok is True
    assert result.candidates[0].element == "organic"
    assert result.candidates[0].signal_severity > result.candidates[1].signal_severity


def test_root_cause_mapping_uses_policy_registry_override() -> None:
    registry = MetricPolicyRegistry(
        signal_policies=(),
        discovery_policy_rules=(),
        factor_graph_policies=(),
        root_cause_policies=(RootCausePolicy(metric_id="gmv", dimension="channel", root_cause_type="custom_channel_rule"),),
    )

    result = compute_dimension_contribution(
        metric_definition=_metric("gmv"),
        dimension="channel",
        current_rows=[
            {"channel": "paid_ads", "metric_value": 20.0},
            {"channel": "organic", "metric_value": 95.0},
        ],
        baseline_rows=_baseline("channel", {"paid_ads": 100.0, "organic": 100.0}),
        evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        policy_registry=registry,
        top_threshold=0.60,
    )

    assert result.ok is True
    assert result.candidates[0].root_cause_type == "custom_channel_rule"


def test_attribution_returns_typed_error_for_missing_root_cause_policy() -> None:
    registry = MetricPolicyRegistry(
        signal_policies=(),
        discovery_policy_rules=(),
        factor_graph_policies=(),
        root_cause_policies=(),
    )

    result = compute_dimension_contribution(
        metric_definition=_metric("uv"),
        dimension="category",
        current_rows=[
            {"category": "electronics", "metric_value": 20.0},
            {"category": "fashion", "metric_value": 100.0},
        ],
        baseline_rows=_baseline("category", {"electronics": 100.0, "fashion": 100.0}),
        evidence_ids=["run-1:E1", "run-1:E2"],
        policy_registry=registry,
        top_threshold=0.60,
    )

    assert result.ok is False
    assert result.error_code == "ROOT_CAUSE_POLICY_MISSING"
    assert result.candidates == []


def test_attribution_refund_rate_uses_increase_direction() -> None:
    result = compute_dimension_contribution(
        metric_definition=_metric("refund_rate", higher_is_better=False),
        dimension="product",
        current_rows=[
            {"product": "1", "metric_value": 0.35},
            {"product": "2", "metric_value": 0.10},
        ],
        baseline_rows=_baseline("product", {"1": 0.10, "2": 0.10}),
        evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        top_threshold=0.60,
    )

    assert result.ok is True
    assert result.candidates[0].root_cause_type == "complaint_or_quality_issue"
    assert result.candidates[0].element == "1"


def test_gmv_decomposition_uses_uv_pay_cvr_aov_not_order_count() -> None:
    result = compute_gmv_decomposition(
        current={"gmv": 510.0, "uv": 100.0, "pay_user_cnt": 10.0},
        baseline={"gmv": 1000.0, "uv": 200.0, "pay_user_cnt": 20.0},
    )

    assert result["current"]["pay_cvr"] == 0.10
    assert result["current"]["aov"] == 51.0
    assert result["current"]["reconstructed_gmv"] == 510.0
    assert result["largest_drop_factor"] == "uv"


def test_net_gmv_components_are_gmv_minus_refund() -> None:
    components = compute_net_gmv_components(gmv=1000.0, refund=125.0)
    assert components == {"gmv": 1000.0, "refund": 125.0, "net_gmv": 875.0}


def test_rank_root_causes_uses_ep_before_v1_formula_for_adtributor_candidates() -> None:
    old_formula_winner = _candidate("channel", "organic", contribution_pct=0.9, eng_confidence=0.6)
    ep_winner = _candidate(
        "channel",
        "paid_ads",
        contribution_pct=0.5,
        eng_confidence=0.2,
        explanatory_power=0.8,
        surprise_js=0.2,
    )

    ranked = rank_root_causes([old_formula_winner, ep_winner])

    assert ranked[0].element == "paid_ads"
    assert ranked[0].eng_confidence == 1.0


def test_empty_rows_do_not_create_candidate() -> None:
    result = compute_dimension_contribution(
        metric_definition=_metric("gmv"),
        dimension="channel",
        current_rows=[],
        baseline_rows=_baseline("channel", {"paid_ads": 100.0}),
        evidence_ids=["run-1:E1"],
    )

    assert result.ok is False
    assert result.error_code == "NO_CURRENT_DATA"
    assert result.candidates == []


def test_missing_baseline_rows_do_not_create_candidate() -> None:
    result = compute_dimension_contribution(
        metric_definition=_metric("gmv"),
        dimension="channel",
        current_rows=[
            {"channel": "paid_ads", "metric_value": 20.0},
        ],
        baseline_rows=[],
        evidence_ids=["run-1:E1"],
    )

    assert result.ok is False
    assert result.error_code == "INSUFFICIENT_BASELINE_DATA"
    assert result.candidates == []


def test_missing_evidence_ids_do_not_create_candidate() -> None:
    result = compute_dimension_contribution(
        metric_definition=_metric("gmv"),
        dimension="channel",
        current_rows=[
            {"channel": "paid_ads", "metric_value": 20.0},
        ],
        baseline_rows=_baseline("channel", {"paid_ads": 100.0}),
        evidence_ids=[],
    )

    assert result.ok is False
    assert result.error_code == "EVIDENCE_MISSING"
    assert result.candidates == []


def _candidate(
    dimension: str,
    element: str,
    *,
    contribution_pct: float,
    eng_confidence: float,
    explanatory_power: float | None = None,
    surprise_js: float | None = None,
):
    return attribution_service.RootCauseCandidate(
        root_cause_type="campaign_traffic_drop",
        dimension=dimension,
        element=element,
        contribution_pct=contribution_pct,
        explanatory_power=explanatory_power,
        surprise_js=surprise_js,
        signal_severity=1.0,
        evidence_support=1.0,
        reflection_factor=1.0,
        eng_confidence=eng_confidence,
        verdict="confirmed",
        evidence_ids=["run-1:E1"],
    )

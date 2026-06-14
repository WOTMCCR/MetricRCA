from __future__ import annotations

import importlib
import math
from pathlib import Path

from metric_rca.services.adtributor_service import (
    AdtributorElement,
    attribute_elements,
    jensen_shannon_surprise,
)


def test_adtributor_additive_ep_sums_to_one_for_dimension() -> None:
    result = attribute_elements(
        metric_id="gmv",
        elements=[
            AdtributorElement(dimension="channel", element="paid_ads", actual=20.0, forecast=100.0),
            AdtributorElement(dimension="channel", element="social", actual=40.0, forecast=100.0),
            AdtributorElement(dimension="channel", element="organic", actual=90.0, forecast=100.0),
        ],
        t_ep=0.67,
        t_eep=0.10,
    )

    assert result.ok is True
    by_element = {row.element: row.explanatory_power for row in result.element_scores}
    assert math.isclose(sum(by_element.values()), 1.0)
    assert math.isclose(by_element["paid_ads"], 80 / 150)
    assert math.isclose(by_element["social"], 60 / 150)
    assert math.isclose(by_element["organic"], 10 / 150)


def test_adtributor_js_is_symmetric_and_bounded_for_hand_values() -> None:
    first = jensen_shannon_surprise(actual=20.0, forecast=100.0, actual_total=150.0, forecast_total=300.0)
    second = jensen_shannon_surprise(actual=100.0, forecast=20.0, actual_total=300.0, forecast_total=150.0)

    assert math.isclose(first, second)
    assert 0.0 <= first <= 1.0
    expected = 0.5 * (
        (100 / 300) * math.log((2 * (100 / 300)) / ((100 / 300) + (20 / 150)))
        + (20 / 150) * math.log((2 * (20 / 150)) / ((100 / 300) + (20 / 150)))
    )
    assert math.isclose(first, expected)


def test_adtributor_greedy_selection_thresholds_change_selected_elements() -> None:
    elements = [
        AdtributorElement(dimension="channel", element="paid_ads", actual=20.0, forecast=100.0),
        AdtributorElement(dimension="channel", element="social", actual=40.0, forecast=100.0),
        AdtributorElement(dimension="channel", element="organic", actual=90.0, forecast=100.0),
    ]

    default = attribute_elements(metric_id="gmv", elements=elements, t_ep=0.67, t_eep=0.10)
    relaxed = attribute_elements(metric_id="gmv", elements=elements, t_ep=0.45, t_eep=0.10)

    assert default.candidates[0].dimension_elements == [("channel", "paid_ads"), ("channel", "social")]
    assert relaxed.candidates[0].dimension_elements == [("channel", "paid_ads")]


def test_adtributor_ratio_without_component_values_is_typed_not_applicable() -> None:
    result = attribute_elements(
        metric_id="pay_cvr",
        elements=[
            AdtributorElement(dimension="device", element="mobile", actual=0.03, forecast=0.08),
        ],
        t_ep=0.67,
        t_eep=0.10,
    )

    assert result.ok is False
    assert result.error_code == "ADTRIBUTOR_NOT_APPLICABLE"
    assert result.candidates == []


def test_adtributor_service_is_pure_no_db_or_repository_imports() -> None:
    module = importlib.import_module("metric_rca.services.adtributor_service")
    assert module.__name__ == "metric_rca.services.adtributor_service"
    source = Path(module.__file__ or "").read_text()
    forbidden = ["sqlalchemy", "MetricRepository", "MetadataRepository", "execute_plan", "anomaly_ground_truth"]
    assert [token for token in forbidden if token in source] == []

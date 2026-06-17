from __future__ import annotations

from datetime import date
import os

import pytest

from metric_rca.domain.models import MetricDefinition
from metric_rca.services.anomaly_service import detect_anomaly_from_rows
from metric_rca.services.metric_service import MetricService, MetricServiceError, ParsedIntent
from metric_rca.config.settings import Settings


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


class FakeMetadataRepository:
    def __init__(self, metrics: list[MetricDefinition]) -> None:
        self.metrics = {metric.metric_id: metric for metric in metrics}
        self.received_dimension_requests: list[str] = []

    def get_metric_definition(self, metric_id: str) -> MetricDefinition:
        metric = self.metrics.get(metric_id)
        if metric is None:
            raise MetricServiceError("METRIC_NOT_FOUND", f"metric not found: {metric_id}")
        return metric

    def get_schema_context(self, metric_id: str) -> dict[str, object]:
        metric = self.get_metric_definition(metric_id)
        return {
            "source_table": metric.source_table,
            "allowed_dimensions": metric.allowed_dimensions,
            "formula": metric.formula,
            "metric_family": metric.metric_family,
        }

    def list_metrics(self) -> list[MetricDefinition]:
        return list(self.metrics.values())

    def list_dimension_values(self, dimension: str) -> list[str]:
        self.received_dimension_requests.append(dimension)
        return {
            "channel": ["paid_ads", "organic"],
            "category": ["electronics", "fashion"],
            "device": ["mobile", "desktop"],
            "product": ["1", "2"],
        }.get(dimension, [])


def _live_settings() -> Settings:
    api_key = os.getenv("METRIC_RCA_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "test-key":
        pytest.skip("real OpenAI credentials are not configured for live intent parsing")
    return Settings(
        db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
        readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
        llm_enabled=True,
        llm_required=False,
        llm_provider="openai",
        llm_model="gpt-5.4-nano",
        llm_api_key=api_key,
    )


def test_parse_uses_real_openai_intent_planner() -> None:
    service = MetricService(
        FakeMetadataRepository([_metric("gmv"), _metric("refund_rate", higher_is_better=False)]),
        settings=_live_settings(),
    )

    parsed = service.parse_question("昨天 paid_ads 渠道 GMV 为什么异常？", business_today=date(2026, 6, 6))
    assert parsed.metric_id == "gmv"
    assert parsed.dimension == "channel"
    assert parsed.element == "paid_ads"
    assert parsed.target_date == date(2026, 6, 5)

    refund = service.parse_question("Why did yesterday refund rate increase?", business_today=date(2026, 6, 6))
    assert refund.metric_id == "refund_rate"
    assert refund.target_date == date(2026, 6, 5)


def test_parse_question_validates_planner_output_against_metadata() -> None:
    service = MetricService(
        FakeMetadataRepository([_metric("gmv"), _metric("pay_cvr")]),
        settings=_live_settings(),
    )

    try:
        service.parse_question("Why did yesterday refund rate increase?", business_today=date(2026, 6, 6))
    except MetricServiceError as exc:
        assert exc.code == "METRIC_NOT_FOUND"
    else:
        raise AssertionError("unsupported planner metric did not fail")


def test_parse_question_returns_planner_typed_errors() -> None:
    service = MetricService(
        FakeMetadataRepository([_metric("gmv")]),
        settings=_live_settings(),
    )

    for question, code in [
        ("Why did yesterday refund rate increase?", "METRIC_NOT_FOUND"),
        ("Why did yesterday channel=unknown_channel GMV drop?", "DIMENSION_NOT_ALLOWED"),
        ("上个月 GMV 为什么下降？", "DATE_RANGE_INVALID"),
        ("帮我随便写一段 SQL", "PARSE_FAILED"),
    ]:
        try:
            service.parse_question(question, business_today=date(2026, 6, 6))
        except MetricServiceError as exc:
            assert exc.code == code
        else:
            raise AssertionError(f"{question!r} did not fail with {code}")


def test_detect_anomaly_paid_ads_flagged() -> None:
    result = detect_anomaly_from_rows(
        current_rows=[{"metric_value": 60.0}],
        baseline_rows=[
            {"business_date": date(2026, 5, 29), "metric_value": 100.0},
            {"business_date": date(2026, 5, 22), "metric_value": 101.0},
            {"business_date": date(2026, 5, 15), "metric_value": 99.0},
            {"business_date": date(2026, 5, 8), "metric_value": 100.0},
        ],
        metric_definition=_metric("gmv", higher_is_better=True),
        thresh_pct=0.15,
        z_thresh=2.0,
    )

    assert result.ok is True
    assert result.is_anomaly is True
    assert result.bad_direction is True
    assert result.baseline.sample_n == 4
    assert result.delta_pct <= -0.39
    assert result.z_score < -2.0


def test_detect_anomaly_no_anomaly_returns_no_anomaly_observation() -> None:
    result = detect_anomaly_from_rows(
        current_rows=[{"metric_value": 98.0}],
        baseline_rows=[
            {"business_date": date(2026, 5, 29), "metric_value": 100.0},
            {"business_date": date(2026, 5, 22), "metric_value": 101.0},
            {"business_date": date(2026, 5, 15), "metric_value": 99.0},
            {"business_date": date(2026, 5, 8), "metric_value": 100.0},
        ],
        metric_definition=_metric(),
        thresh_pct=0.15,
        z_thresh=2.0,
    )

    assert result.ok is True
    assert result.is_anomaly is False
    assert result.error_code == "NO_ANOMALY_DETECTED"


def test_detect_anomaly_flags_positive_magnitude_spike() -> None:
    result = detect_anomaly_from_rows(
        current_rows=[{"metric_value": 250.0}],
        baseline_rows=[
            {"business_date": date(2026, 5, 29), "metric_value": 100.0},
            {"business_date": date(2026, 5, 22), "metric_value": 101.0},
            {"business_date": date(2026, 5, 15), "metric_value": 99.0},
            {"business_date": date(2026, 5, 8), "metric_value": 100.0},
        ],
        metric_definition=_metric("gmv", higher_is_better=True),
        thresh_pct=0.15,
        z_thresh=2.0,
    )

    assert result.ok is True
    assert result.is_anomaly is True
    assert result.bad_direction is False
    assert result.delta_pct > 1.0
    assert result.z_score > 2.0


def test_detect_anomaly_sample_n_lt_3_returns_insufficient_baseline_data() -> None:
    result = detect_anomaly_from_rows(
        current_rows=[{"metric_value": 50.0}],
        baseline_rows=[
            {"business_date": date(2026, 5, 29), "metric_value": 100.0},
            {"business_date": date(2026, 5, 22), "metric_value": 101.0},
        ],
        metric_definition=_metric(),
        thresh_pct=0.15,
        z_thresh=2.0,
    )

    assert result.ok is False
    assert result.error_code == "INSUFFICIENT_BASELINE_DATA"


def test_detect_anomaly_threshold_boundaries_and_refund_direction() -> None:
    boundary = detect_anomaly_from_rows(
        current_rows=[{"metric_value": 85.0}],
        baseline_rows=[
            {"business_date": date(2026, 5, 29), "metric_value": 100.0},
            {"business_date": date(2026, 5, 22), "metric_value": 101.0},
            {"business_date": date(2026, 5, 15), "metric_value": 99.0},
            {"business_date": date(2026, 5, 8), "metric_value": 100.0},
        ],
        metric_definition=_metric(higher_is_better=True),
        thresh_pct=0.15,
        z_thresh=2.0,
    )
    assert boundary.is_anomaly is True

    refund = detect_anomaly_from_rows(
        current_rows=[{"metric_value": 0.30}],
        baseline_rows=[
            {"business_date": date(2026, 5, 29), "metric_value": 0.10},
            {"business_date": date(2026, 5, 22), "metric_value": 0.11},
            {"business_date": date(2026, 5, 15), "metric_value": 0.09},
            {"business_date": date(2026, 5, 8), "metric_value": 0.10},
        ],
        metric_definition=_metric("refund_rate", higher_is_better=False),
        thresh_pct=0.15,
        z_thresh=2.0,
    )
    assert refund.is_anomaly is True
    assert refund.bad_direction is True

from __future__ import annotations

from datetime import date

import pytest

from metric_rca.data.metric_deriver import MetricDerivationError, aggregate_metric, derive_row


def test_metric_deriver_preserves_metric_identities() -> None:
    row = derive_row(
        {
            "business_date": "2026-06-05",
            "channel": "paid_ads",
            "uv": 200.0,
            "sessions": 180.0,
            "orders": 18.0,
            "unit_price": 100.0,
            "promotion_discount": 0.10,
            "refund_amount": 162.0,
            "stockout_hours": 6.0,
            "complaints": 1.8,
            "spend": 300.0,
        }
    )

    assert row["gmv"] == 1620.0
    assert row["net_gmv"] == 1458.0
    assert row["pay_cvr"] == 0.1
    assert row["aov"] == 90.0
    assert row["refund_rate"] == 0.1
    assert row["stockout_rate"] == 0.25
    assert row["complaint_rate"] == 0.1
    assert row["campaign_roi"] == 5.4


def test_metric_aggregation_uses_ratio_of_totals() -> None:
    rows = [
        {
            "business_date": "2026-06-05",
            "channel": "paid_ads",
            "sessions": 100.0,
            "orders": 10.0,
            "gmv": 1000.0,
        },
        {
            "business_date": "2026-06-05",
            "channel": "paid_ads",
            "sessions": 300.0,
            "orders": 15.0,
            "gmv": 3000.0,
        },
    ]

    assert aggregate_metric(
        rows,
        metric_id="pay_cvr",
        business_date=date(2026, 6, 5),
        selector={"channel": ("paid_ads",)},
    ) == 0.0625


def test_metric_deriver_rejects_orders_above_sessions_without_clamping() -> None:
    with pytest.raises(MetricDerivationError) as exc_info:
        derive_row(
            {
                "business_date": "2026-06-05",
                "channel": "paid_ads",
                "uv": 200.0,
                "sessions": 10.0,
                "orders": 18.0,
                "unit_price": 100.0,
                "promotion_discount": 0.10,
                "refund_amount": 100.0,
                "stockout_hours": 6.0,
                "complaints": 1.8,
                "spend": 300.0,
            }
        )

    assert exc_info.value.code == "METRIC_IDENTITY_INVALID"


def test_metric_deriver_rejects_refund_above_gmv_without_clamping() -> None:
    with pytest.raises(MetricDerivationError) as exc_info:
        derive_row(
            {
                "business_date": "2026-06-05",
                "channel": "paid_ads",
                "uv": 200.0,
                "sessions": 180.0,
                "orders": 18.0,
                "unit_price": 100.0,
                "promotion_discount": 0.10,
                "refund_amount": 2000.0,
                "stockout_hours": 6.0,
                "complaints": 1.8,
                "spend": 300.0,
            }
        )

    assert exc_info.value.code == "METRIC_IDENTITY_INVALID"


def test_metric_deriver_rejects_stockout_hours_above_one_day_without_clamping() -> None:
    with pytest.raises(MetricDerivationError) as exc_info:
        derive_row(
            {
                "business_date": "2026-06-05",
                "channel": "paid_ads",
                "uv": 200.0,
                "sessions": 180.0,
                "orders": 18.0,
                "unit_price": 100.0,
                "promotion_discount": 0.10,
                "refund_amount": 100.0,
                "stockout_hours": 25.0,
                "complaints": 1.8,
                "spend": 300.0,
            }
        )

    assert exc_info.value.code == "METRIC_IDENTITY_INVALID"


def test_metric_aggregation_rejects_missing_metric_values_without_zero_default() -> None:
    with pytest.raises(MetricDerivationError) as exc_info:
        aggregate_metric(
            [{"business_date": "2026-06-05", "channel": "paid_ads"}],
            metric_id="gmv",
            business_date=date(2026, 6, 5),
            selector={"channel": ("paid_ads",)},
        )

    assert exc_info.value.code == "METRIC_ROW_INVALID"

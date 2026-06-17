from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from metric_rca.config.settings import get_settings
from metric_rca.data.anomaly_injection import (
    BORDERLINE_DATE,
    INTERACTION_DATE,
    LAGGED_DATE,
    LAGGED_OBSERVE_DATE,
    MULTI_CAUSE_DATE,
    SPIKE_DATE,
    TARGET_DATE,
    campaign_multiplier,
    complaint_count,
    interaction_multiplier,
    lagged_campaign_multiplier,
    multi_cause_stockout_hours,
    multi_cause_traffic_multiplier,
    order_amount_multiplier,
    refund_multiplier,
    stockout_hours,
    support_ticket_count,
    traffic_multiplier,
    weak_signal_multiplier,
)
from metric_rca.data.seed_data import main as seed_main


def test_complex_injection_dates_do_not_overlap_existing_eval_dates() -> None:
    assert {MULTI_CAUSE_DATE, INTERACTION_DATE, LAGGED_DATE, LAGGED_OBSERVE_DATE}.isdisjoint(
        {TARGET_DATE, BORDERLINE_DATE, SPIKE_DATE}
    )


def test_complex_injection_module_has_no_random_or_db_dependencies() -> None:
    tree = ast.parse(Path("metric_rca/data/anomaly_injection.py").read_text())
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", maxsplit=1)[0])

    assert "random" not in imported_roots
    assert "sqlalchemy" not in imported_roots
    assert "pymysql" not in imported_roots


def test_complex_injection_functions_are_deterministic_and_exact() -> None:
    assert multi_cause_traffic_multiplier(
        business_date=MULTI_CAUSE_DATE,
        channel="paid_ads",
        category="home",
    ) == (0.55, 1.0)
    assert multi_cause_traffic_multiplier(
        business_date=MULTI_CAUSE_DATE,
        channel="organic",
        category="electronics",
    ) == (1.0, 1.0)
    assert multi_cause_stockout_hours(business_date=MULTI_CAUSE_DATE, category="electronics") == 12.0
    assert multi_cause_stockout_hours(business_date=MULTI_CAUSE_DATE, category="fashion") is None

    assert interaction_multiplier(
        business_date=INTERACTION_DATE,
        channel="paid_ads",
        category="electronics",
    ) == (0.30, 1.0)
    assert interaction_multiplier(
        business_date=INTERACTION_DATE,
        channel="paid_ads",
        category="fashion",
    ) == (0.95, 1.0)
    assert interaction_multiplier(
        business_date=INTERACTION_DATE,
        channel="organic",
        category="electronics",
    ) == (1.0, 0.97)

    assert lagged_campaign_multiplier(business_date=LAGGED_DATE, channel="social") == (0.15, 0.10, 1.0, 1.0)
    assert lagged_campaign_multiplier(
        business_date=LAGGED_OBSERVE_DATE,
        channel="social",
    ) == (1.0, 1.0, 0.35, 1.0)
    assert lagged_campaign_multiplier(business_date=LAGGED_DATE, channel="paid_ads") == (1.0, 1.0, 1.0, 1.0)

    assert weak_signal_multiplier(business_date=MULTI_CAUSE_DATE, channel="affiliate") == (0.82, 0.85)
    assert weak_signal_multiplier(business_date=MULTI_CAUSE_DATE, channel="paid_ads") == (1.0, 1.0)
    assert weak_signal_multiplier(business_date=MULTI_CAUSE_DATE, channel="affiliate") == (
        weak_signal_multiplier(business_date=MULTI_CAUSE_DATE, channel="affiliate")
    )


def test_existing_injection_date_multipliers_are_unchanged() -> None:
    assert traffic_multiplier(
        business_date=TARGET_DATE,
        channel="paid_ads",
        device="desktop",
        category="home",
        product_id=4,
    ) == (0.38, 0.35)
    assert traffic_multiplier(
        business_date=TARGET_DATE,
        channel="paid_ads",
        device="mobile",
        category="electronics",
        product_id=3,
    ) == pytest.approx((0.38, 0.35 * 0.55 * 0.62 * 0.35))
    assert traffic_multiplier(
        business_date=BORDERLINE_DATE,
        channel="paid_ads",
        device="desktop",
        category="home",
        product_id=4,
    ) == (0.88, 1.15)
    assert traffic_multiplier(
        business_date=SPIKE_DATE,
        channel="paid_ads",
        device="desktop",
        category="home",
        product_id=4,
    ) == (2.5, 2.3)
    assert campaign_multiplier(business_date=TARGET_DATE, channel="paid_ads") == (0.30, 0.35)
    assert campaign_multiplier(business_date=SPIKE_DATE, channel="paid_ads") == (2.8, 2.5)
    assert stockout_hours(business_date=TARGET_DATE, category="electronics", warehouse_index=1) == 16.5
    assert refund_multiplier(business_date=TARGET_DATE, product_id=1, category="electronics") == 0.95
    assert complaint_count(business_date=TARGET_DATE, product_id=1, category="electronics") == 18
    assert support_ticket_count(business_date=TARGET_DATE, product_id=1, category="electronics") == 3
    assert order_amount_multiplier(business_date=TARGET_DATE, category="fashion", product_id=4) == 0.03


def test_seed_applies_complex_injections_only_on_new_dates() -> None:
    seed_main()
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            assert _stockout_values(conn, MULTI_CAUSE_DATE, "electronics") == {12.0}
            assert _stockout_values(conn, TARGET_DATE, "electronics") == {15.5, 16.5}

            paid_ads = _traffic_channel_totals(conn, MULTI_CAUSE_DATE, "paid_ads")
            paid_ads_baseline = _traffic_channel_totals(conn, MULTI_CAUSE_DATE - timedelta(days=7), "paid_ads")
            assert paid_ads["uv"] / paid_ads_baseline["uv"] < 0.65

            interaction_cell = _traffic_channel_category_totals(conn, INTERACTION_DATE, "paid_ads", "electronics")
            paid_ads_fashion = _traffic_channel_category_totals(conn, INTERACTION_DATE, "paid_ads", "fashion")
            assert interaction_cell["uv"] < paid_ads_fashion["uv"] * 0.50

            lagged_campaign = _campaign_channel_totals(conn, LAGGED_DATE, "social")
            lagged_campaign_baseline = _campaign_channel_totals(conn, LAGGED_DATE - timedelta(days=7), "social")
            assert lagged_campaign["clicks"] / lagged_campaign_baseline["clicks"] < 0.20

            social_observed = _traffic_channel_totals(conn, LAGGED_OBSERVE_DATE, "social")
            social_baseline = _traffic_channel_totals(conn, LAGGED_OBSERVE_DATE - timedelta(days=7), "social")
            assert social_observed["uv"] / social_baseline["uv"] < 0.45

            affiliate = _traffic_channel_totals(conn, MULTI_CAUSE_DATE, "affiliate")
            affiliate_baseline = _traffic_channel_totals(conn, MULTI_CAUSE_DATE - timedelta(days=7), "affiliate")
            assert affiliate["uv"] / affiliate_baseline["uv"] < 0.90
            assert _pay_rate(affiliate) / _pay_rate(affiliate_baseline) < 0.95
    finally:
        engine.dispose()


def _stockout_values(conn, business_date, category: str) -> set[float]:
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT fi.stockout_hours
            FROM fact_inventory fi
            INNER JOIN dim_product p ON p.product_id = fi.product_id
            WHERE fi.business_date = :business_date
              AND p.category = :category
            """
        ),
        {"business_date": business_date, "category": category},
    ).mappings().all()
    return {float(row["stockout_hours"]) for row in rows}


def _traffic_channel_totals(conn, business_date, channel: str) -> dict[str, float]:
    row = conn.execute(
        text(
            """
            SELECT SUM(uv) AS uv, SUM(pay_user_cnt) AS pay_user_cnt
            FROM fact_traffic
            WHERE business_date = :business_date
              AND channel = :channel
            """
        ),
        {"business_date": business_date, "channel": channel},
    ).mappings().one()
    return {"uv": float(row["uv"]), "pay_user_cnt": float(row["pay_user_cnt"])}


def _traffic_channel_category_totals(conn, business_date, channel: str, category: str) -> dict[str, float]:
    row = conn.execute(
        text(
            """
            SELECT SUM(t.uv) AS uv, SUM(t.pay_user_cnt) AS pay_user_cnt
            FROM fact_traffic t
            INNER JOIN dim_product p ON p.product_id = t.product_id
            WHERE t.business_date = :business_date
              AND t.channel = :channel
              AND p.category = :category
            """
        ),
        {"business_date": business_date, "channel": channel, "category": category},
    ).mappings().one()
    return {"uv": float(row["uv"]), "pay_user_cnt": float(row["pay_user_cnt"])}


def _campaign_channel_totals(conn, business_date, channel: str) -> dict[str, float]:
    row = conn.execute(
        text(
            """
            SELECT SUM(spend) AS spend, SUM(clicks) AS clicks
            FROM fact_campaign
            WHERE business_date = :business_date
              AND channel = :channel
            """
        ),
        {"business_date": business_date, "channel": channel},
    ).mappings().one()
    return {"spend": float(row["spend"]), "clicks": float(row["clicks"])}


def _pay_rate(row: dict[str, float]) -> float:
    return row["pay_user_cnt"] / row["uv"]

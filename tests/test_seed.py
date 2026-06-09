from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from sqlalchemy import create_engine, text

from metric_rca.config.settings import get_settings
from metric_rca.data.seed_data import DEFAULT_SEED, _resolve_seed, main as seed_main
from metric_rca.domain.models import METRIC_ALLOWED_DIMENSIONS
from metric_rca.guardrails.renderer import METRIC_TEMPLATES


HASH_TABLES = [
    "dim_product",
    "dim_user",
    "fact_order",
    "fact_traffic",
    "fact_inventory",
    "fact_campaign",
    "fact_customer_ticket",
    "metric_definition",
    "anomaly_ground_truth",
]
TARGET_DATE = date(2026, 6, 5)
GMV_NO_ANOMALY_DATE = date(2026, 6, 4)


def test_seed_override_is_explicit_and_typed(monkeypatch) -> None:
    monkeypatch.delenv("METRIC_RCA_DATA_SEED", raising=False)
    assert _resolve_seed() == DEFAULT_SEED

    monkeypatch.setenv("METRIC_RCA_DATA_SEED", "20260610")
    assert _resolve_seed() == 20260610

    monkeypatch.setenv("METRIC_RCA_DATA_SEED", "not-a-number")
    try:
        _resolve_seed()
    except ValueError as exc:
        assert str(exc).startswith("SEED_INVALID")
    else:
        raise AssertionError("invalid seed must fail fast")


def _gmv_anomaly_stats(conn, business_date: date) -> dict[str, float | bool]:
    baseline_dates = [business_date - timedelta(days=7 * i) for i in range(1, 5)]
    row = conn.execute(
        text(
            """
            SELECT
              SUM(CASE WHEN business_date = :business_date THEN daily_gmv ELSE 0 END) AS current_gmv,
              AVG(CASE WHEN business_date <> :business_date THEN daily_gmv END) AS baseline_mean,
              STDDEV_SAMP(CASE WHEN business_date <> :business_date THEN daily_gmv END) AS baseline_std
            FROM (
              SELECT business_date, SUM(order_amount) AS daily_gmv
              FROM fact_order
              WHERE is_paid = 1
                AND business_date IN :dates
              GROUP BY business_date
            ) AS daily
            """
        ),
        {"business_date": business_date, "dates": tuple([*baseline_dates, business_date])},
    ).mappings().one()
    current = float(row["current_gmv"])
    baseline_mean = float(row["baseline_mean"])
    baseline_std = float(row["baseline_std"])
    delta_pct = abs((current - baseline_mean) / baseline_mean)
    z_score = abs((current - baseline_mean) / max(baseline_std, 1e-9))
    return {
        "current": current,
        "baseline_mean": baseline_mean,
        "baseline_std": baseline_std,
        "delta_pct": delta_pct,
        "z_score": z_score,
        "is_anomaly": delta_pct >= 0.15 and z_score >= 2.0,
    }


def _content_hash() -> str:
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    payload: dict[str, list[dict]] = {}
    try:
        with engine.connect() as conn:
            for table in HASH_TABLES:
                rows = conn.execute(text(f"SELECT * FROM {table} ORDER BY 1, 2")).mappings().all()
                payload[table] = [dict(row) for row in rows]
    finally:
        engine.dispose()

    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def test_seed_is_idempotent_and_has_required_calendar() -> None:
    seed_main()
    first = _content_hash()
    seed_main()
    second = _content_hash()
    assert first == second

    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            dates = {
                row.business_date
                for row in conn.execute(
                    text("SELECT DISTINCT business_date FROM fact_order ORDER BY business_date")
                )
            }
            assert len(dates) == 60
            target = date(2026, 6, 5)
            assert {target - timedelta(days=7 * i) for i in range(1, 5)} <= dates
    finally:
        engine.dispose()


def test_seed_metric_definitions_and_ground_truth_cases() -> None:
    seed_main()
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            metrics = {
                row.metric_id: row
                for row in conn.execute(text("SELECT * FROM metric_definition")).mappings()
            }
            assert set(metrics) == set(METRIC_TEMPLATES)
            assert set(metrics) == set(METRIC_ALLOWED_DIMENSIONS)
            assert metrics["gmv"]["source_table"] == "fact_order"
            assert "channel" in metrics["gmv"]["allowed_dimensions"]
            for metric_id, template in METRIC_TEMPLATES.items():
                allowed_dimensions = set(json.loads(metrics[metric_id]["allowed_dimensions"]))
                assert metrics[metric_id]["source_table"] == template.fact_table
                assert allowed_dimensions == METRIC_ALLOWED_DIMENSIONS[metric_id]

            cases = {
                row.case_id: dict(row)
                for row in conn.execute(text("SELECT * FROM anomaly_ground_truth")).mappings()
            }
            assert set(cases) == {
                "gmv_paid_ads_drop",
                "gmv_stockout_electronics",
                "cvr_mobile_drop",
                "refund_rate_product_quality",
                "gmv_no_anomaly",
            }
            assert cases["gmv_paid_ads_drop"]["root_cause_type"] == "campaign_traffic_drop"
            assert cases["gmv_stockout_electronics"]["root_cause_type"] == "stockout"
            assert cases["cvr_mobile_drop"]["root_cause_type"] == "conversion_drop"
            assert (
                cases["refund_rate_product_quality"]["root_cause_type"]
                == "complaint_or_quality_issue"
            )
            assert cases["gmv_no_anomaly"]["expected_anomaly"] == 0
            assert cases["gmv_no_anomaly"]["root_cause_type"] == "no_anomaly"
            assert cases["gmv_no_anomaly"]["business_date"] == GMV_NO_ANOMALY_DATE
            assert cases["gmv_paid_ads_drop"]["business_date"] == TARGET_DATE
            assert cases["gmv_stockout_electronics"]["business_date"] == TARGET_DATE
    finally:
        engine.dispose()


def test_gmv_no_anomaly_label_matches_same_weekday_baseline() -> None:
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            no_anomaly_stats = _gmv_anomaly_stats(conn, GMV_NO_ANOMALY_DATE)
            assert no_anomaly_stats["is_anomaly"] is False

            target_stats = _gmv_anomaly_stats(conn, TARGET_DATE)
            assert target_stats["is_anomaly"] is True
    finally:
        engine.dispose()


def test_paid_ads_injection_below_same_weekday_baseline() -> None:
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                      SUM(CASE WHEN business_date = '2026-06-05' THEN spend ELSE 0 END) AS target_spend,
                      AVG(CASE WHEN business_date <> '2026-06-05' THEN spend END) AS baseline_spend,
                      SUM(CASE WHEN business_date = '2026-06-05' THEN clicks ELSE 0 END) AS target_clicks,
                      AVG(CASE WHEN business_date <> '2026-06-05' THEN clicks END) AS baseline_clicks
                    FROM fact_campaign
                    WHERE channel = 'paid_ads'
                      AND business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29','2026-06-05')
                    """
                )
            ).mappings().one()
            assert float(row["target_spend"]) < float(row["baseline_spend"]) * 0.5
            assert int(row["target_clicks"]) < float(row["baseline_clicks"]) * 0.5

            uv = conn.execute(
                text(
                    """
                    SELECT
                      SUM(CASE WHEN business_date = '2026-06-05' THEN daily_uv ELSE 0 END) AS target_uv,
                      AVG(CASE WHEN business_date <> '2026-06-05' THEN daily_uv END) AS baseline_uv
                    FROM (
                      SELECT business_date, SUM(uv) AS daily_uv
                      FROM fact_traffic
                      WHERE channel = 'paid_ads'
                        AND business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29','2026-06-05')
                      GROUP BY business_date
                    ) AS daily_paid_ads
                    """
                )
            ).mappings().one()
            assert int(uv["target_uv"]) < float(uv["baseline_uv"]) * 0.6
    finally:
        engine.dispose()


def test_seed_injects_stockout_mobile_conversion_and_quality_refund_signals() -> None:
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            stockout = conn.execute(
                text(
                    """
                    SELECT
                      SUM(CASE WHEN business_date = '2026-06-05' THEN daily_stockout ELSE 0 END) AS target_stockout,
                      AVG(CASE WHEN business_date <> '2026-06-05' THEN daily_stockout END) AS baseline_stockout
                    FROM (
                      SELECT fi.business_date, SUM(fi.stockout_hours) AS daily_stockout
                      FROM fact_inventory fi
                      INNER JOIN dim_product dp ON fi.product_id = dp.product_id
                      WHERE dp.category = 'electronics'
                        AND fi.business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29','2026-06-05')
                      GROUP BY fi.business_date
                    ) AS daily
                    """
                )
            ).mappings().one()
            assert float(stockout["target_stockout"]) > float(stockout["baseline_stockout"]) * 10

            cvr = conn.execute(
                text(
                    """
                    SELECT
                      SUM(CASE WHEN business_date = '2026-06-05' THEN daily_pay ELSE 0 END)
                        / NULLIF(SUM(CASE WHEN business_date = '2026-06-05' THEN daily_uv ELSE 0 END), 0) AS target_cvr,
                      AVG(CASE WHEN business_date <> '2026-06-05' THEN daily_cvr END) AS baseline_cvr
                    FROM (
                      SELECT
                        business_date,
                        SUM(pay_user_cnt) AS daily_pay,
                        SUM(uv) AS daily_uv,
                        SUM(pay_user_cnt) / NULLIF(SUM(uv), 0) AS daily_cvr
                      FROM fact_traffic
                      WHERE device = 'mobile'
                        AND business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29','2026-06-05')
                      GROUP BY business_date
                    ) AS daily
                    """
                )
            ).mappings().one()
            assert float(cvr["target_cvr"]) < float(cvr["baseline_cvr"]) * 0.7

            quality = conn.execute(
                text(
                    """
                    SELECT
                      SUM(CASE WHEN o.business_date = '2026-06-05' THEN o.refund_amount ELSE 0 END) AS target_refund,
                      AVG(CASE WHEN o.business_date <> '2026-06-05' THEN o.refund_amount END) AS baseline_refund,
                      SUM(CASE WHEN t.business_date = '2026-06-05' THEN t.is_complaint ELSE 0 END) AS target_complaints
                    FROM fact_order o
                    LEFT JOIN fact_customer_ticket t
                      ON t.product_id = o.product_id
                     AND t.business_date = o.business_date
                    WHERE o.product_id = 1
                      AND o.business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29','2026-06-05')
                    """
                )
            ).mappings().one()
            assert float(quality["target_refund"]) > float(quality["baseline_refund"]) * 5
            assert int(quality["target_complaints"]) >= 18
    finally:
        engine.dispose()

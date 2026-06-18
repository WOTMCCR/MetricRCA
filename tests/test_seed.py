from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
import yaml

from metric_rca.config.settings import get_settings
from metric_rca.data.seed_data import (
    DEFAULT_SEED,
    _assert_destructive_seed_allowed,
    _ground_truth_row_with_metadata,
    _resolve_seed,
    _resolve_seed_profile,
    _seed_profile_config,
    main as seed_main,
)
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
    "memory_record",
]
TARGET_DATE = date(2026, 6, 5)
GMV_NO_ANOMALY_DATE = date(2026, 6, 4)
BORDERLINE_DATE = date(2026, 6, 3)
SPIKE_DATE = date(2026, 6, 2)
MULTI_CAUSE_DATE = date(2026, 6, 1)
INTERACTION_DATE = date(2026, 5, 31)
LAGGED_OBSERVE_DATE = date(2026, 6, 1)
SCENARIO_DIR = Path("metric_rca/data/scenarios")
PUBLIC_CASES_PATH = Path("metric_rca/evals/regression_public_cases.jsonl")
PRIVATE_GROUND_TRUTH_PATH = Path("metric_rca/evals/regression_private_ground_truth.jsonl")
MEMORY_TREATMENT_PUBLIC_CASES_PATH = Path("metric_rca/evals/memory_treatment_public_cases.jsonl")
MEMORY_TREATMENT_PRIVATE_GROUND_TRUTH_PATH = Path("metric_rca/evals/memory_treatment_private_ground_truth.jsonl")
EXPECTED_GROUND_TRUTH = {
    "gmv_paid_ads_drop": ("gmv", 1, "campaign_traffic_drop", "channel", "paid_ads", TARGET_DATE),
    "gmv_stockout_electronics": ("gmv", 1, "stockout", "category", "electronics", TARGET_DATE),
    "cvr_mobile_drop": ("pay_cvr", 1, "conversion_drop", "device", "mobile", TARGET_DATE),
    "refund_rate_product_quality": ("refund_rate", 1, "complaint_or_quality_issue", "product", "1", TARGET_DATE),
    "gmv_no_anomaly": ("gmv", 0, "no_anomaly", None, None, GMV_NO_ANOMALY_DATE),
    "C06_gmv_multi_channel_drop": ("gmv", 1, "campaign_traffic_drop", "channel", "paid_ads", TARGET_DATE),
    "C07_gmv_category_channel_cross": ("gmv", 1, "campaign_traffic_drop", "channel", "paid_ads", TARGET_DATE),
    "C08_gmv_aov_drop": ("gmv", 1, "aov_drop", "product", "2", TARGET_DATE),
    "C09_gmv_uv_organic_drop": ("gmv", 1, "campaign_traffic_drop", "channel", "organic", TARGET_DATE),
    "C10_gmv_price_change": ("gmv", 1, "aov_drop", "category", "fashion", TARGET_DATE),
    "C11_gmv_promo_end_falloff": ("gmv", 1, "campaign_traffic_drop", "channel", "affiliate", TARGET_DATE),
    "C12_gmv_single_sku_stockout": ("gmv", 1, "stockout", "product", "3", TARGET_DATE),
    "C13_net_gmv_refund_spike": ("net_gmv", 1, "complaint_or_quality_issue", "product", "1", TARGET_DATE),
    "C14_net_gmv_gmv_driven": ("net_gmv", 1, "campaign_traffic_drop", "channel", "paid_ads", TARGET_DATE),
    "C15_refund_rate_logistics": ("refund_rate", 1, "complaint_or_quality_issue", "category", "fashion", TARGET_DATE),
    "C16_stockout_rate_warehouse": ("stockout_rate", 1, "stockout", "warehouse", "osaka", TARGET_DATE),
    "C17_complaint_rate_quality": ("complaint_rate", 1, "complaint_or_quality_issue", "category", "electronics", TARGET_DATE),
    "C18_cvr_channel_landing": ("pay_cvr", 1, "conversion_drop", "channel", "affiliate", TARGET_DATE),
    "C19_gmv_seasonal_false_positive": ("gmv", 0, "no_anomaly", None, None, GMV_NO_ANOMALY_DATE),
    "C20_cvr_no_anomaly_noise": ("pay_cvr", 0, "no_anomaly", None, None, GMV_NO_ANOMALY_DATE),
    "C21_cvr_discovery": ("pay_cvr", 1, "conversion_drop", "device", "mobile", TARGET_DATE),
    "C22_gmv_borderline": ("gmv", 0, "no_anomaly", None, None, BORDERLINE_DATE),
    "C23_uv_organic_drop": ("uv", 1, "campaign_traffic_drop", "channel", "organic", TARGET_DATE),
    "C24_gmv_positive_spike": ("gmv", 1, "campaign_traffic_drop", "channel", "paid_ads", SPIKE_DATE),
    "C25_refund_discovery": ("refund_rate", 1, "complaint_or_quality_issue", "product", "1", TARGET_DATE),
    "C26_ambiguous_intent": ("gmv", 1, "campaign_traffic_drop", "channel", "paid_ads", TARGET_DATE),
    "C27_composite_cause": ("gmv", 1, "campaign_traffic_drop", "channel", "paid_ads", TARGET_DATE),
    "C28_multi_day_drift": ("gmv", 1, "campaign_traffic_drop", "channel", "organic", TARGET_DATE),
    "MC01_gmv_multi_cause_overall": ("gmv", 1, "campaign_traffic_drop", "channel", "paid_ads", MULTI_CAUSE_DATE),
    "MC02_uv_multi_channel_drop": ("uv", 1, "campaign_traffic_drop", "channel", "paid_ads", LAGGED_OBSERVE_DATE),
    "MC03_cvr_multi_signal_drop": ("pay_cvr", 1, "conversion_drop", "channel", "affiliate", MULTI_CAUSE_DATE),
    "MC04_gmv_weak_set": ("gmv", 1, "campaign_traffic_drop", "channel", "affiliate", MULTI_CAUSE_DATE),
    "MC05_gmv_lag_stockout_mix": ("gmv", 1, "campaign_traffic_drop", "channel", "social", LAGGED_OBSERVE_DATE),
    "MC06_net_gmv_multi_driver": ("net_gmv", 1, "campaign_traffic_drop", "channel", "paid_ads", MULTI_CAUSE_DATE),
    "MC07_uv_weak_multi_driver": ("uv", 1, "campaign_traffic_drop", "channel", "social", LAGGED_OBSERVE_DATE),
    "MC08_gmv_channel_category_mix": ("gmv", 1, "campaign_traffic_drop", "channel", "social", MULTI_CAUSE_DATE),
    "IX01_gmv_channel_category_interaction": (
        "gmv",
        1,
        "interaction_channel_category",
        "channel",
        "paid_ads",
        INTERACTION_DATE,
    ),
    "IX02_gmv_interaction_discovery": (
        "gmv",
        1,
        "interaction_channel_category",
        "channel",
        "paid_ads",
        INTERACTION_DATE,
    ),
    "IX03_uv_interaction_cell": (
        "uv",
        1,
        "interaction_channel_category",
        "channel",
        "paid_ads",
        INTERACTION_DATE,
    ),
    "IX04_gmv_interaction_no_single_driver": (
        "gmv",
        1,
        "interaction_channel_category",
        "category",
        "electronics",
        INTERACTION_DATE,
    ),
    "LG01_gmv_lagged_social": ("gmv", 1, "campaign_traffic_drop", "channel", "social", LAGGED_OBSERVE_DATE),
    "LG02_uv_lagged_social_discovery": ("uv", 1, "campaign_traffic_drop", "channel", "social", LAGGED_OBSERVE_DATE),
    "WK01_gmv_weak_affiliate_boundary": (
        "gmv",
        1,
        "campaign_traffic_drop",
        "channel",
        "affiliate",
        MULTI_CAUSE_DATE,
    ),
    "WK02_gmv_no_anomaly_weak": ("gmv", 0, "no_anomaly", None, None, GMV_NO_ANOMALY_DATE),
}
EXPECTED_WEIGHTED_ROOT_CAUSES = {
    "C06_gmv_multi_channel_drop": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 1.0},
    ],
    "C07_gmv_category_channel_cross": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 1.0},
    ],
    "C27_composite_cause": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 1.0},
    ],
    "MC01_gmv_multi_cause_overall": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.48},
        {"root_cause_type": "stockout", "dimension": "category", "element": "electronics", "weight": 0.32},
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 0.2},
    ],
    "MC02_uv_multi_channel_drop": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.5},
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 0.35},
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 0.15},
    ],
    "MC03_cvr_multi_signal_drop": [
        {"root_cause_type": "conversion_drop", "dimension": "channel", "element": "affiliate", "weight": 0.65},
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.35},
    ],
    "MC04_gmv_weak_set": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 0.55},
        {"root_cause_type": "conversion_drop", "dimension": "channel", "element": "affiliate", "weight": 0.45},
    ],
    "MC05_gmv_lag_stockout_mix": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 0.45},
        {"root_cause_type": "stockout", "dimension": "category", "element": "electronics", "weight": 0.35},
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.2},
    ],
    "MC06_net_gmv_multi_driver": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.5},
        {"root_cause_type": "stockout", "dimension": "category", "element": "electronics", "weight": 0.3},
        {"root_cause_type": "conversion_drop", "dimension": "channel", "element": "affiliate", "weight": 0.2},
    ],
    "MC07_uv_weak_multi_driver": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 0.45},
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 0.3},
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.25},
    ],
    "MC08_gmv_channel_category_mix": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 0.4},
        {"root_cause_type": "stockout", "dimension": "category", "element": "electronics", "weight": 0.35},
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.25},
    ],
    "IX01_gmv_channel_category_interaction": [
        {"root_cause_type": "interaction_channel_category", "dimension": "channel", "element": "paid_ads", "weight": 1.0},
    ],
    "IX02_gmv_interaction_discovery": [
        {"root_cause_type": "interaction_channel_category", "dimension": "channel", "element": "paid_ads", "weight": 1.0},
    ],
    "IX03_uv_interaction_cell": [
        {"root_cause_type": "interaction_channel_category", "dimension": "channel", "element": "paid_ads", "weight": 1.0},
    ],
    "IX04_gmv_interaction_no_single_driver": [
        {"root_cause_type": "interaction_channel_category", "dimension": "category", "element": "electronics", "weight": 1.0},
    ],
    "LG01_gmv_lagged_social": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 1.0},
    ],
    "LG02_uv_lagged_social_discovery": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 1.0},
    ],
    "WK01_gmv_weak_affiliate_boundary": [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 1.0},
    ],
    "WK02_gmv_no_anomaly_weak": [],
}
EXPECTED_MEMORY_TREATMENT_GROUND_TRUTH = {
    "M01_gmv_memory_product_prior": ("gmv", 1, "aov_drop", "product", "2", TARGET_DATE),
}


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


def test_seed_profile_defaults_to_regression_and_rejects_unknown_profile(monkeypatch) -> None:
    monkeypatch.delenv("METRIC_RCA_SEED_PROFILE", raising=False)
    assert _resolve_seed_profile() == "regression"

    monkeypatch.setenv("METRIC_RCA_SEED_PROFILE", "acceptance")
    assert _resolve_seed_profile() == "acceptance"

    monkeypatch.setenv("METRIC_RCA_SEED_PROFILE", "demo")
    try:
        _resolve_seed_profile()
    except ValueError as exc:
        assert str(exc).startswith("SEED_PROFILE_INVALID")
    else:
        raise AssertionError("invalid seed profile must fail fast")


def test_seed_profile_metadata_files_define_regression_data_slice(monkeypatch) -> None:
    registry = _load_yaml(SCENARIO_DIR / "scenario_registry.yaml")
    profiles = _load_yaml(SCENARIO_DIR / "seed_profiles.yaml")

    assert profiles["default_profile"] == "regression"
    assert set(profiles["profiles"]) == {"smoke", "regression", "acceptance", "stress"}
    for profile_name, profile in profiles["profiles"].items():
        monkeypatch.setenv("METRIC_RCA_SEED_PROFILE", profile_name)
        assert _resolve_seed_profile() == profile_name
        assert profile["scenario_suite"] in registry["suites"]
        assert profile["destructive_reset"] == "local_test_dsn_or_explicit_allow"
    assert profiles["profiles"]["acceptance"]["opt_in"] is True
    assert profiles["profiles"]["stress"]["opt_in"] is True
    assert profiles["profiles"]["acceptance"]["cardinality"] == {
        "products": 200,
        "categories": 20,
        "channels": 8,
        "devices": 4,
        "warehouses": 10,
        "campaigns": 100,
        "users": 10000,
        "history_days": 180,
    }

    regression = registry["suites"]["regression"]
    assert regression["seed_profile"] == "regression"
    assert regression["case_count"] == 44
    assert (SCENARIO_DIR / regression["public_cases_file"]).resolve() == PUBLIC_CASES_PATH.resolve()
    assert (SCENARIO_DIR / regression["private_ground_truth_file"]).resolve() == PRIVATE_GROUND_TRUTH_PATH.resolve()
    assert regression["data_slice"] == {
        "business_today": "2026-06-06",
        "target_date": "2026-06-05",
        "history_days": 60,
    }
    public_rows = _read_jsonl(PUBLIC_CASES_PATH)
    private_rows = _read_jsonl(PRIVATE_GROUND_TRUTH_PATH)
    assert len(public_rows) == regression["case_count"]
    assert len(private_rows) == regression["case_count"]
    assert {row["case_id"] for row in private_rows} == set(EXPECTED_GROUND_TRUTH)
    private_by_id = {row["case_id"]: row for row in private_rows}
    for case_id, (metric_id, expected_anomaly, root_cause, dimension, element, business_date) in EXPECTED_GROUND_TRUTH.items():
        row = private_by_id[case_id]
        assert {
            key: row[key]
            for key in [
                "case_id",
                "expected_metric_id",
                "expected_anomaly",
                "expected_root_cause_type",
                "expected_dimension",
                "expected_element",
                "expected_business_date",
            ]
        } == {
            "case_id": case_id,
            "expected_metric_id": metric_id,
            "expected_anomaly": bool(expected_anomaly),
            "expected_root_cause_type": root_cause,
            "expected_dimension": dimension,
            "expected_element": element,
            "expected_business_date": business_date.isoformat(),
        }
        if case_id in EXPECTED_WEIGHTED_ROOT_CAUSES:
            assert row["root_causes"] == EXPECTED_WEIGHTED_ROOT_CAUSES[case_id]
        else:
            assert "root_causes" not in row

    acceptance = registry["suites"]["acceptance"]
    assert acceptance["seed_profile"] == "acceptance"
    assert acceptance["data_slice"]["products"] == 200
    assert acceptance["data_slice"]["history_days"] == 180

    treatment = registry["suites"]["memory-treatment"]
    assert treatment["seed_profile"] == "regression"
    assert treatment["case_count"] == 1
    assert (SCENARIO_DIR / treatment["public_cases_file"]).resolve() == MEMORY_TREATMENT_PUBLIC_CASES_PATH.resolve()
    assert (
        (SCENARIO_DIR / treatment["private_ground_truth_file"]).resolve()
        == MEMORY_TREATMENT_PRIVATE_GROUND_TRUTH_PATH.resolve()
    )
    treatment_public_rows = _read_jsonl(MEMORY_TREATMENT_PUBLIC_CASES_PATH)
    treatment_private_rows = _read_jsonl(MEMORY_TREATMENT_PRIVATE_GROUND_TRUTH_PATH)
    assert len(treatment_public_rows) == 1
    assert len(treatment_private_rows) == 1
    assert set(treatment_public_rows[0]) == {"case_id", "question", "tags"}
    assert set(treatment_private_rows[0]) == {
        "case_id",
        "expected_metric_id",
        "expected_anomaly",
        "expected_root_cause_type",
        "expected_dimension",
        "expected_element",
        "expected_business_date",
    }
    assert treatment_private_rows[0]["case_id"] == treatment_public_rows[0]["case_id"]
    assert "memory_treatment" in treatment_public_rows[0]["tags"]


def test_seed_profile_config_expands_acceptance_and_stress_entity_scale() -> None:
    regression = _seed_profile_config("regression")
    assert len(regression.products) == 9
    assert len(regression.channels) == 4
    assert len(regression.devices) == 2
    assert len(regression.warehouses) == 2
    assert regression.user_count == 80
    assert regression.history_days == 60

    acceptance = _seed_profile_config("acceptance")
    assert len(acceptance.products) >= 200
    assert len({category for _, _, category, _ in acceptance.products}) >= 20
    assert len(acceptance.channels) >= 8
    assert len(acceptance.devices) >= 4
    assert len(acceptance.warehouses) >= 10
    assert acceptance.campaign_count >= 100
    assert acceptance.user_count >= 10_000
    assert acceptance.history_days >= 180
    assert acceptance.products[:9] == regression.products
    assert acceptance.min_pay_user_per_cell == 0

    stress = _seed_profile_config("stress")
    assert len(stress.products) > len(acceptance.products)
    assert stress.campaign_count > acceptance.campaign_count
    assert stress.history_days > acceptance.history_days
    assert stress.min_pay_user_per_cell == 0


def test_acceptance_ground_truth_projects_broad_merchandise_case_to_category() -> None:
    base = {
        "case_id": "C08_gmv_aov_drop",
        "business_date": TARGET_DATE,
        "metric_id": "gmv",
        "expected_anomaly": 1,
        "root_cause_type": "aov_drop",
        "dimension": "product",
        "element": "2",
    }

    regression = _ground_truth_row_with_metadata(base, seed=DEFAULT_SEED, seed_profile="regression")
    acceptance = _ground_truth_row_with_metadata(base, seed=DEFAULT_SEED, seed_profile="acceptance")

    assert regression["dimension"] == "product"
    assert regression["element"] == "2"
    assert acceptance["dimension"] == "category"
    assert acceptance["element"] == "fashion"
    assert _json_value(acceptance["root_causes"]) == [
        {
            "root_cause_type": "aov_drop",
            "dimension": "category",
            "element": "fashion",
            "weight": 1.0,
        }
    ]


def test_destructive_seed_requires_allow_flag_or_local_dsn(monkeypatch) -> None:
    monkeypatch.delenv("METRIC_RCA_ALLOW_DESTRUCTIVE_SEED", raising=False)
    _assert_destructive_seed_allowed(
        db_dsn="mysql+pymysql://metric_rca_app:metric_rca_app@127.0.0.1:3307/metric_rca",
        seed_profile="regression",
    )

    try:
        _assert_destructive_seed_allowed(
            db_dsn="mysql+pymysql://metric_rca_app:metric_rca_app@prod-db:3306/metric_rca",
            seed_profile="acceptance",
        )
    except RuntimeError as exc:
        assert str(exc).startswith("DESTRUCTIVE_SEED_NOT_ALLOWED")
    else:
        raise AssertionError("non-local destructive seed must require explicit allow")

    monkeypatch.setenv("METRIC_RCA_ALLOW_DESTRUCTIVE_SEED", "true")
    _assert_destructive_seed_allowed(
        db_dsn="mysql+pymysql://metric_rca_app:metric_rca_app@prod-db:3306/metric_rca",
        seed_profile="acceptance",
    )


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


def _load_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text())
    assert isinstance(payload, dict)
    return payload


def _read_jsonl(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert all(isinstance(row, dict) for row in rows)
    return rows


def _json_value(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def test_seed_makes_aov_cases_decomposition_dominant() -> None:
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            product = _gmv_factor_drop_row(conn, "o.product_id = 2")
            category = _gmv_factor_drop_row(conn, "p.category = 'fashion'")

            assert product["largest_drop_factor"] == "aov_drop"
            assert category["largest_drop_factor"] == "aov_drop"
            assert product["aov_drop"] > product["pay_cvr_drop"]
            assert category["aov_drop"] > category["pay_cvr_drop"]
    finally:
        engine.dispose()


def _gmv_factor_drop_row(conn, where_clause: str) -> dict[str, float | str]:
    row = conn.execute(
        text(
            f"""
            WITH order_daily AS (
              SELECT
                o.business_date,
                SUM(CASE WHEN o.is_paid = 1 THEN o.order_amount ELSE 0 END) AS gmv
              FROM fact_order o
              INNER JOIN dim_product p ON o.product_id = p.product_id
              WHERE {where_clause}
                AND o.business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29','2026-06-05')
              GROUP BY o.business_date
            ), traffic_daily AS (
              SELECT
                t.business_date,
                SUM(t.uv) AS uv,
                SUM(t.pay_user_cnt) AS pay_user_cnt
              FROM fact_traffic t
              INNER JOIN dim_product p ON t.product_id = p.product_id
              WHERE {where_clause.replace("o.", "t.")}
                AND t.business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29','2026-06-05')
              GROUP BY t.business_date
            ), daily AS (
              SELECT
                order_daily.business_date,
                order_daily.gmv,
                traffic_daily.uv,
                traffic_daily.pay_user_cnt
              FROM order_daily
              INNER JOIN traffic_daily ON traffic_daily.business_date = order_daily.business_date
            ), factors AS (
              SELECT
                SUM(CASE WHEN business_date = '2026-06-05' THEN uv ELSE 0 END) AS current_uv,
                AVG(CASE WHEN business_date <> '2026-06-05' THEN uv END) AS baseline_uv,
                SUM(CASE WHEN business_date = '2026-06-05' THEN pay_user_cnt ELSE 0 END) AS current_pay,
                AVG(CASE WHEN business_date <> '2026-06-05' THEN pay_user_cnt END) AS baseline_pay,
                SUM(CASE WHEN business_date = '2026-06-05' THEN gmv ELSE 0 END) AS current_gmv,
                AVG(CASE WHEN business_date <> '2026-06-05' THEN gmv END) AS baseline_gmv
              FROM daily
            )
            SELECT
              GREATEST(0, (baseline_uv - current_uv) / baseline_uv) AS uv_drop,
              GREATEST(
                0,
                ((baseline_pay / NULLIF(baseline_uv, 0)) - (current_pay / NULLIF(current_uv, 0)))
                / (baseline_pay / NULLIF(baseline_uv, 0))
              ) AS pay_cvr_drop,
              GREATEST(
                0,
                ((baseline_gmv / NULLIF(baseline_pay, 0)) - (current_gmv / NULLIF(current_pay, 0)))
                / (baseline_gmv / NULLIF(baseline_pay, 0))
              ) AS aov_drop
            FROM factors
            """
        )
    ).mappings().one()
    drops = {key: float(row[key]) for key in ["uv_drop", "pay_cvr_drop", "aov_drop"]}
    largest = max(drops, key=drops.get)
    return {**drops, "largest_drop_factor": largest}


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
                for row in conn.execute(
                    text("SELECT * FROM anomaly_ground_truth WHERE split = 'regression'")
                ).mappings()
            }
            assert set(cases) == set(EXPECTED_GROUND_TRUTH)
            assert len(cases) == 44
            for case_id, (metric_id, expected_anomaly, root_cause, dimension, element, business_date) in EXPECTED_GROUND_TRUTH.items():
                assert cases[case_id]["metric_id"] == metric_id
                assert cases[case_id]["expected_anomaly"] == expected_anomaly
                assert cases[case_id]["root_cause_type"] == root_cause
                assert cases[case_id]["dimension"] == dimension
                assert cases[case_id]["element"] == element
                assert cases[case_id]["business_date"] == business_date
                assert cases[case_id]["scenario_id"] == case_id
                assert cases[case_id]["split"] == "regression"
                assert cases[case_id]["profile"] == "regression"
                root_causes = _json_value(cases[case_id]["root_causes"])
                if case_id in EXPECTED_WEIGHTED_ROOT_CAUSES:
                    assert root_causes == EXPECTED_WEIGHTED_ROOT_CAUSES[case_id]
                elif expected_anomaly:
                    assert root_causes == [
                        {
                            "root_cause_type": root_cause,
                            "dimension": dimension,
                            "element": element,
                            "weight": 1.0,
                        }
                    ]
                else:
                    assert root_causes == []
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
            assert cases["C19_gmv_seasonal_false_positive"]["expected_anomaly"] == 0
            assert cases["C19_gmv_seasonal_false_positive"]["business_date"] == GMV_NO_ANOMALY_DATE
            assert cases["C20_cvr_no_anomaly_noise"]["expected_anomaly"] == 0
            assert cases["C20_cvr_no_anomaly_noise"]["business_date"] == GMV_NO_ANOMALY_DATE
            assert cases["C22_gmv_borderline"]["expected_anomaly"] == 0
            assert cases["C22_gmv_borderline"]["business_date"] == BORDERLINE_DATE
            assert cases["C24_gmv_positive_spike"]["business_date"] == SPIKE_DATE
            assert cases["gmv_paid_ads_drop"]["business_date"] == TARGET_DATE
            assert cases["gmv_stockout_electronics"]["business_date"] == TARGET_DATE

            treatment_cases = {
                row.case_id: dict(row)
                for row in conn.execute(
                    text("SELECT * FROM anomaly_ground_truth WHERE split = 'memory-treatment'")
                ).mappings()
            }
            assert set(treatment_cases) == set(EXPECTED_MEMORY_TREATMENT_GROUND_TRUTH)
            for case_id, (metric_id, expected_anomaly, root_cause, dimension, element, business_date) in (
                EXPECTED_MEMORY_TREATMENT_GROUND_TRUTH.items()
            ):
                assert treatment_cases[case_id]["metric_id"] == metric_id
                assert treatment_cases[case_id]["expected_anomaly"] == expected_anomaly
                assert treatment_cases[case_id]["root_cause_type"] == root_cause
                assert treatment_cases[case_id]["dimension"] == dimension
                assert treatment_cases[case_id]["element"] == element
                assert treatment_cases[case_id]["business_date"] == business_date
                assert treatment_cases[case_id]["scenario_id"] == case_id
                assert treatment_cases[case_id]["profile"] == "regression"
    finally:
        engine.dispose()


def test_seed_writes_semantic_memory_from_metric_definitions() -> None:
    seed_main()
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            metrics = {
                row.metric_id: dict(row)
                for row in conn.execute(text("SELECT * FROM metric_definition")).mappings()
            }
            semantic_rows = {
                row.mem_key: json.loads(row.payload)
                for row in conn.execute(
                    text(
                        """
                        SELECT mem_key, payload
                        FROM memory_record
                        WHERE layer = 'semantic'
                        ORDER BY mem_key
                        """
                    )
                ).mappings()
            }

            assert set(semantic_rows) == {f"{metric_id}|semantic" for metric_id in metrics}
            gmv_payload = semantic_rows["gmv|semantic"]
            assert gmv_payload["metric_id"] == "gmv"
            assert gmv_payload["display_name"] == metrics["gmv"]["display_name"]
            assert gmv_payload["formula"] == metrics["gmv"]["formula"]
            assert {"gmv", metrics["gmv"]["display_name"]} <= set(gmv_payload["aliases"])
            assert gmv_payload["business_rules"] == {
                "higher_is_better": bool(metrics["gmv"]["higher_is_better"]),
                "source_table": metrics["gmv"]["source_table"],
            }
            treatment_memory = conn.execute(
                text(
                    """
                    SELECT payload, confidence, source
                    FROM memory_record
                    WHERE memory_id = 'memory-treatment-gmv-product-prior'
                    """
                )
            ).mappings().one()
            treatment_payload = json.loads(treatment_memory["payload"])
            assert treatment_memory["source"] == "system_verified"
            assert float(treatment_memory["confidence"]) >= 0.70
            assert treatment_payload["eval_suites"] == ["memory-treatment"]
            assert treatment_payload["question_family"] == "gmv_drop"
            assert treatment_payload["analysis_strategy"] == "standard"
            assert treatment_payload["preferred_dimensions"] == ["product"]
            assert treatment_payload["preferred_signal_types"] == ["inventory"]
            assert treatment_payload["prior_root_causes"] == ["aov_drop"]
            assert "expected_element" not in treatment_payload
            assert "expected_root_cause_type" not in treatment_payload
            assert set(gmv_payload["allowed_dimensions"]) == set(
                json.loads(metrics["gmv"]["allowed_dimensions"])
            )
            assert {
                item["dimension"] for item in gmv_payload["dimension_meanings"]
            } == set(json.loads(metrics["gmv"]["allowed_dimensions"]))
    finally:
        engine.dispose()


def test_gmv_no_anomaly_label_matches_same_weekday_baseline() -> None:
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            borderline_stats = _gmv_anomaly_stats(conn, BORDERLINE_DATE)
            no_anomaly_stats = _gmv_anomaly_stats(conn, GMV_NO_ANOMALY_DATE)
            assert borderline_stats["is_anomaly"] is False
            assert borderline_stats["z_score"] < 2.0
            assert no_anomaly_stats["is_anomaly"] is False
            assert no_anomaly_stats["z_score"] < 1.5

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


def test_c09_organic_traffic_signal_is_strongest_channel_drop() -> None:
    seed_main()
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT cur.channel,
                           (base.avg_clicks - cur.clicks) / base.avg_clicks AS click_drop,
                           (traffic_base.avg_uv - traffic_cur.uv) / traffic_base.avg_uv AS uv_drop
                    FROM (
                      SELECT channel, SUM(clicks) AS clicks
                      FROM fact_campaign
                      WHERE business_date = '2026-06-05'
                      GROUP BY channel
                    ) AS cur
                    INNER JOIN (
                      SELECT channel, AVG(clicks) AS avg_clicks
                      FROM (
                        SELECT business_date, channel, SUM(clicks) AS clicks
                        FROM fact_campaign
                        WHERE business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29')
                        GROUP BY business_date, channel
                      ) AS daily_campaign
                      GROUP BY channel
                    ) AS base ON base.channel = cur.channel
                    INNER JOIN (
                      SELECT channel, SUM(uv) AS uv
                      FROM fact_traffic
                      WHERE business_date = '2026-06-05'
                      GROUP BY channel
                    ) AS traffic_cur ON traffic_cur.channel = cur.channel
                    INNER JOIN (
                      SELECT channel, AVG(uv) AS avg_uv
                      FROM (
                        SELECT business_date, channel, SUM(uv) AS uv
                        FROM fact_traffic
                        WHERE business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29')
                        GROUP BY business_date, channel
                      ) AS daily_traffic
                      GROUP BY channel
                    ) AS traffic_base ON traffic_base.channel = cur.channel
                    """
                )
            ).mappings().all()
            click_drops = {row["channel"]: float(row["click_drop"]) for row in rows}
            uv_drops = {row["channel"]: float(row["uv_drop"]) for row in rows}

            assert max(click_drops, key=click_drops.get) == "organic"
            assert max(uv_drops, key=uv_drops.get) == "organic"
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

            product_complaints = conn.execute(
                text(
                    """
                    SELECT
                      SUM(CASE WHEN business_date = '2026-06-05' THEN daily_rate ELSE 0 END) AS target_rate,
                      AVG(CASE WHEN business_date <> '2026-06-05' THEN daily_rate END) AS baseline_rate
                    FROM (
                      SELECT business_date, SUM(is_complaint) / NULLIF(COUNT(ticket_id), 0) AS daily_rate
                      FROM fact_customer_ticket
                      WHERE product_id = 1
                        AND business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29','2026-06-05')
                      GROUP BY business_date
                    ) AS daily
                    """
                )
            ).mappings().one()
            assert float(product_complaints["target_rate"]) > float(product_complaints["baseline_rate"]) * 3

            category_complaints = conn.execute(
                text(
                    """
                    SELECT
                      SUM(CASE WHEN business_date = '2026-06-05' THEN daily_rate ELSE 0 END) AS target_rate,
                      AVG(CASE WHEN business_date <> '2026-06-05' THEN daily_rate END) AS baseline_rate
                    FROM (
                      SELECT t.business_date, SUM(t.is_complaint) / NULLIF(COUNT(t.ticket_id), 0) AS daily_rate
                      FROM fact_customer_ticket t
                      INNER JOIN dim_product p ON t.product_id = p.product_id
                      WHERE p.category = 'electronics'
                        AND t.business_date IN ('2026-05-08','2026-05-15','2026-05-22','2026-05-29','2026-06-05')
                      GROUP BY t.business_date
                    ) AS daily
                    """
                )
            ).mappings().one()
            assert float(category_complaints["target_rate"]) > float(category_complaints["baseline_rate"]) * 2
    finally:
        engine.dispose()

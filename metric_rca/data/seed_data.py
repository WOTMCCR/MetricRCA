"""种子数据生成器：可配置种子 + 固定业务日，幂等重建 60 天确定性数据 + 5 个 ground truth。

设计要点：
  - 完全可复现：默认 seed 与业务日固定，`make seed SEED=...` 可切换确定性数据版本。
  - 真实感：周内效应 + 季节性 + 渠道/类目/设备分布，叠加投放/库存/投诉退款的异常注入。
  - 可评估：写入 regression 28-case 与独立 memory-treatment ground truth，使后续
    eval 能用"真因"逐 case 打分，而非靠人读。

对应 docs/COMPLIANCE_MATRIX.md 第 5 行；docs/MetricRCA.md §10。
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from metric_rca.config.settings import get_settings
from metric_rca.data.anomaly_injection import (
    BORDERLINE_DATE,
    INTERACTION_DATE,
    LAGGED_DATE,
    LAGGED_OBSERVE_DATE,
    MULTI_CAUSE_CVR_DATE,
    MULTI_CAUSE_DATE,
    RESIDUAL_DATE,
    SPIKE_DATE,
    TARGET_DATE,
    campaign_multiplier,
    complaint_count,
    interaction_multiplier,
    lagged_campaign_multiplier,
    multi_cause_cvr_suppressor,
    multi_cause_stockout_hours,
    multi_cause_traffic_multiplier,
    order_amount_multiplier,
    refund_multiplier,
    residual_traffic_multiplier,
    stockout_hours,
    support_ticket_count,
    traffic_multiplier,
    weak_signal_multiplier,
)


DEFAULT_SEED = 20260606  # 默认随机种子：保证无参数时可复现
SEED_PROFILES = frozenset({"smoke", "regression", "acceptance", "stress"})
DEFAULT_SEED_PROFILE = "regression"
BUSINESS_TODAY = date(2026, 6, 6)
HISTORY_DAYS = 60
# 无异常 case 放在 TARGET_DATE 之外的另一天，避免与"目标日异常"在同指标同日冲突。
GMV_NO_ANOMALY_DATE = date(2026, 6, 4)
COMPLEX_INJECTION_DATES = frozenset(
    {MULTI_CAUSE_DATE, MULTI_CAUSE_CVR_DATE, RESIDUAL_DATE,
     INTERACTION_DATE, LAGGED_DATE, LAGGED_OBSERVE_DATE}
)

# 9 个 canonical 商品横跨 electronics/fashion/home 三个类目，商品 1 为质量问题商品。
BASE_PRODUCTS = [
    (1, "Wireless Earbuds", "electronics", Decimal("129.00")),
    (2, "Smart Watch", "electronics", Decimal("219.00")),
    (3, "USB-C Hub", "electronics", Decimal("69.00")),
    (4, "Running Shoes", "fashion", Decimal("99.00")),
    (5, "Canvas Jacket", "fashion", Decimal("149.00")),
    (6, "Travel Backpack", "fashion", Decimal("89.00")),
    (7, "Air Fryer", "home", Decimal("119.00")),
    (8, "Desk Lamp", "home", Decimal("49.00")),
    (9, "Coffee Grinder", "home", Decimal("79.00")),
]
PRODUCTS = BASE_PRODUCTS
BASE_CHANNELS = ("organic", "paid_ads", "social", "affiliate")
BASE_DEVICES = ("mobile", "desktop")
BASE_WAREHOUSES = ("tokyo", "osaka")
CHANNELS = list(BASE_CHANNELS)
DEVICES = list(BASE_DEVICES)
WAREHOUSES = list(BASE_WAREHOUSES)
ACCEPTANCE_CHANNELS = (
    "organic",
    "paid_ads",
    "social",
    "affiliate",
    "email",
    "referral",
    "marketplace",
    "influencer",
)
ACCEPTANCE_DEVICES = ("mobile", "desktop", "tablet", "app")
ACCEPTANCE_WAREHOUSES = (
    "tokyo",
    "osaka",
    "nagoya",
    "fukuoka",
    "sapporo",
    "sendai",
    "hiroshima",
    "kyoto",
    "kobe",
    "yokohama",
)


@dataclass(frozen=True)
class SeedProfileConfig:
    name: str
    products: tuple[tuple[int, str, str, Decimal], ...]
    channels: tuple[str, ...]
    devices: tuple[str, ...]
    warehouses: tuple[str, ...]
    user_count: int
    history_days: int
    campaign_count: int
    traffic_base: int
    min_pay_user_per_cell: int
    batch_size: int = 5_000

# 幂等关键：每次 seed 先按"子表 → 父表"逆序清空，再重建，避免主键/外键冲突与残留。
TABLES_TO_CLEAR = [
    "eval_case_result",
    "eval_run",
    "memory_record",
    "operation_task",
    "sql_audit",
    "evidence",
    "trace_step",
    "agent_run",
    "anomaly_ground_truth",
    "metric_definition",
    "fact_customer_ticket",
    "fact_campaign",
    "fact_inventory",
    "fact_traffic",
    "fact_order",
    "dim_user",
    "dim_product",
]


def main() -> None:
    """入口：连接应用账号 DB，在单事务内清空并重建全部种子数据。"""
    started_at = time.perf_counter()
    settings = get_settings()
    seed_profile = _resolve_seed_profile()
    profile = _seed_profile_config(seed_profile)
    _assert_destructive_seed_allowed(db_dsn=str(settings.db_dsn), seed_profile=seed_profile)
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    _wait_for_mysql(engine)  # 容器刚起时 MySQL 可能尚未就绪，重试等待
    seed = _resolve_seed()
    rng = random.Random(seed)  # 局部 RNG，确定性来源
    row_counts: dict[str, int] = {}
    try:
        with engine.begin() as conn:  # 单事务：要么全部重建成功，要么回滚
            for table in TABLES_TO_CLEAR:
                conn.execute(text(f"DELETE FROM {table}"))
            _ensure_p6_schema(conn)
            row_counts.update(_insert_dimensions(conn, profile=profile))
            _insert_metric_definitions(conn)
            _insert_semantic_memory(conn)
            _insert_memory_treatment_memory(conn)
            row_counts.update(_insert_business_facts(conn, rng, profile=profile))
            _insert_ground_truth(conn, seed=seed, seed_profile=seed_profile)
    finally:
        engine.dispose()  # 释放连接池
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    print(
        json.dumps(
            {
                "seed_profile": seed_profile,
                "seed": seed,
                "duration_ms": elapsed_ms,
                "entity_counts": {
                    "products": len(profile.products),
                    "categories": len({category for _, _, category, _ in profile.products}),
                    "channels": len(profile.channels),
                    "devices": len(profile.devices),
                    "warehouses": len(profile.warehouses),
                    "campaigns": profile.campaign_count,
                    "users": profile.user_count,
                    "history_days": profile.history_days,
                },
                "row_counts": row_counts,
            },
            sort_keys=True,
        )
    )


def _resolve_seed() -> int:
    """读取 seed override；非法值显式失败，避免悄悄回落到默认数据。"""
    raw = os.getenv("METRIC_RCA_DATA_SEED")
    if raw is None or raw == "":
        return DEFAULT_SEED
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError("SEED_INVALID: METRIC_RCA_DATA_SEED must be an integer") from exc


def _resolve_seed_profile() -> str:
    raw = os.getenv("METRIC_RCA_SEED_PROFILE") or DEFAULT_SEED_PROFILE
    profile = raw.strip().lower()
    if profile not in SEED_PROFILES:
        raise ValueError(f"SEED_PROFILE_INVALID: METRIC_RCA_SEED_PROFILE must be one of {sorted(SEED_PROFILES)}")
    return profile


def _seed_profile_config(seed_profile: str) -> SeedProfileConfig:
    if seed_profile == "smoke":
        return SeedProfileConfig(
            name="smoke",
            products=tuple(BASE_PRODUCTS),
            channels=BASE_CHANNELS,
            devices=BASE_DEVICES,
            warehouses=BASE_WAREHOUSES,
            user_count=80,
            history_days=HISTORY_DAYS,
            campaign_count=len(BASE_CHANNELS),
            traffic_base=82,
            min_pay_user_per_cell=0,
        )
    if seed_profile == "regression":
        return SeedProfileConfig(
            name="regression",
            products=tuple(BASE_PRODUCTS),
            channels=BASE_CHANNELS,
            devices=BASE_DEVICES,
            warehouses=BASE_WAREHOUSES,
            user_count=80,
            history_days=HISTORY_DAYS,
            campaign_count=len(BASE_CHANNELS),
            traffic_base=82,
            min_pay_user_per_cell=0,
        )
    if seed_profile == "acceptance":
        return SeedProfileConfig(
            name="acceptance",
            products=_expanded_products(product_count=200, category_count=20),
            channels=ACCEPTANCE_CHANNELS,
            devices=ACCEPTANCE_DEVICES,
            warehouses=ACCEPTANCE_WAREHOUSES,
            user_count=10_000,
            history_days=180,
            campaign_count=100,
            traffic_base=10,
            min_pay_user_per_cell=0,
        )
    if seed_profile == "stress":
        return SeedProfileConfig(
            name="stress",
            products=_expanded_products(product_count=240, category_count=24),
            channels=(*ACCEPTANCE_CHANNELS, "retargeting", "partner"),
            devices=ACCEPTANCE_DEVICES,
            warehouses=(*ACCEPTANCE_WAREHOUSES, "chiba", "nara"),
            user_count=12_000,
            history_days=210,
            campaign_count=120,
            traffic_base=8,
            min_pay_user_per_cell=0,
        )
    raise ValueError(f"SEED_PROFILE_INVALID: unsupported seed profile {seed_profile}")


def _expanded_products(*, product_count: int, category_count: int) -> tuple[tuple[int, str, str, Decimal], ...]:
    categories = ("electronics", "fashion", "home", *[f"category_{index:02d}" for index in range(4, category_count + 1)])
    products: list[tuple[int, str, str, Decimal]] = [*BASE_PRODUCTS]
    for product_id in range(len(BASE_PRODUCTS) + 1, product_count + 1):
        category = categories[(product_id - 1) % len(categories)]
        cents = (product_id * 37) % 100
        price = Decimal(25 + (product_id * 17) % 275) + (Decimal(cents) / Decimal("100"))
        products.append((product_id, f"Catalog Product {product_id:03d}", category, price.quantize(Decimal("0.01"))))
    return tuple(products)


def _assert_destructive_seed_allowed(*, db_dsn: str, seed_profile: str) -> None:
    raw_allow = os.getenv("METRIC_RCA_ALLOW_DESTRUCTIVE_SEED", "")
    if raw_allow.lower() == "true":
        return
    if seed_profile in {"smoke", "regression"} and _is_local_seed_dsn(db_dsn):
        return
    raise RuntimeError(
        "DESTRUCTIVE_SEED_NOT_ALLOWED: set METRIC_RCA_ALLOW_DESTRUCTIVE_SEED=true "
        "or use the local test database allowlist"
    )


def _is_local_seed_dsn(db_dsn: str) -> bool:
    return (
        "127.0.0.1:" in db_dsn
        or "localhost:" in db_dsn
        or "@127.0.0.1/" in db_dsn
        or "@localhost/" in db_dsn
    )


def _wait_for_mysql(engine) -> None:
    """最多重试 30 次（每次间隔 1s）等待 MySQL 可连；始终失败则抛出最后一次错误。"""
    last_error: OperationalError | None = None
    for _ in range(30):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            last_error = exc
            time.sleep(1)
    if last_error is not None:
        raise last_error


def _ensure_p6_schema(conn) -> None:
    """Apply additive local schema upgrades before deterministic seeding."""
    _ensure_evidence_identity_schema(conn)
    _ensure_column(
        conn,
        table="trace_step",
        column="token_usage",
        ddl="ALTER TABLE trace_step ADD COLUMN token_usage JSON NULL AFTER latency_ms",
    )
    _ensure_column(
        conn,
        table="agent_run",
        column="runtime_version",
        ddl="ALTER TABLE agent_run ADD COLUMN runtime_version INT NOT NULL DEFAULT 3 AFTER error_code",
    )
    _ensure_column(
        conn,
        table="anomaly_ground_truth",
        column="scenario_id",
        ddl="ALTER TABLE anomaly_ground_truth ADD COLUMN scenario_id VARCHAR(96) NULL AFTER case_id",
    )
    _ensure_column(
        conn,
        table="anomaly_ground_truth",
        column="split",
        ddl="ALTER TABLE anomaly_ground_truth ADD COLUMN split VARCHAR(32) NOT NULL DEFAULT 'regression' AFTER scenario_id",
    )
    _ensure_column(
        conn,
        table="anomaly_ground_truth",
        column="seed",
        ddl="ALTER TABLE anomaly_ground_truth ADD COLUMN seed INT NULL AFTER split",
    )
    _ensure_column(
        conn,
        table="anomaly_ground_truth",
        column="profile",
        ddl="ALTER TABLE anomaly_ground_truth ADD COLUMN profile VARCHAR(32) NOT NULL DEFAULT 'regression' AFTER seed",
    )
    _ensure_column(
        conn,
        table="anomaly_ground_truth",
        column="root_causes",
        ddl="ALTER TABLE anomaly_ground_truth ADD COLUMN root_causes JSON NULL AFTER element",
    )
    _ensure_column(
        conn,
        table="anomaly_ground_truth",
        column="confounders",
        ddl="ALTER TABLE anomaly_ground_truth ADD COLUMN confounders JSON NULL AFTER root_causes",
    )
    _ensure_column(
        conn,
        table="anomaly_ground_truth",
        column="expected_behavior",
        ddl="ALTER TABLE anomaly_ground_truth ADD COLUMN expected_behavior JSON NULL AFTER confounders",
    )
    _ensure_column(
        conn,
        table="agent_run",
        column="total_tokens",
        ddl="ALTER TABLE agent_run ADD COLUMN total_tokens INT NULL AFTER error_code",
    )
    _ensure_column(
        conn,
        table="agent_run",
        column="total_latency_ms",
        ddl="ALTER TABLE agent_run ADD COLUMN total_latency_ms INT NULL AFTER total_tokens",
    )
    _ensure_column(
        conn,
        table="agent_run",
        column="token_breakdown",
        ddl="ALTER TABLE agent_run ADD COLUMN token_breakdown JSON NULL AFTER total_latency_ms",
    )
    _ensure_column(
        conn,
        table="metric_definition",
        column="metric_family",
        ddl=(
            "ALTER TABLE metric_definition ADD COLUMN metric_family VARCHAR(32) "
            "NOT NULL AFTER formula"
        ),
    )
    _drop_column_default(conn, table="metric_definition", column="metric_family")
    _ensure_column(
        conn,
        table="sql_audit",
        column="audit_key",
        ddl="ALTER TABLE sql_audit ADD COLUMN audit_key VARCHAR(64) NULL AFTER audit_id",
    )
    _ensure_index(
        conn,
        table="sql_audit",
        index="uq_audit_key",
        ddl="ALTER TABLE sql_audit ADD UNIQUE KEY uq_audit_key (audit_key)",
    )
    _ensure_index(
        conn,
        table="eval_case_result",
        index="uq_eval_case",
        ddl="ALTER TABLE eval_case_result ADD UNIQUE KEY uq_eval_case (eval_id, case_id)",
    )
    _ensure_check_constraint(
        conn,
        table="eval_case_result",
        constraint="chk_eval_case_result_intent_ok",
        ddl="ALTER TABLE eval_case_result ADD CONSTRAINT chk_eval_case_result_intent_ok CHECK (intent_ok IN (0, 1))",
    )
    _ensure_check_constraint(
        conn,
        table="eval_case_result",
        constraint="chk_eval_case_result_anomaly_ok",
        ddl="ALTER TABLE eval_case_result ADD CONSTRAINT chk_eval_case_result_anomaly_ok CHECK (anomaly_ok IN (0, 1))",
    )
    _ensure_check_constraint(
        conn,
        table="eval_case_result",
        constraint="chk_eval_case_result_top1_ok",
        ddl="ALTER TABLE eval_case_result ADD CONSTRAINT chk_eval_case_result_top1_ok CHECK (top1_ok IN (0, 1))",
    )
    _ensure_check_constraint(
        conn,
        table="eval_case_result",
        constraint="chk_eval_case_result_top3_ok",
        ddl="ALTER TABLE eval_case_result ADD CONSTRAINT chk_eval_case_result_top3_ok CHECK (top3_ok IN (0, 1))",
    )
    _ensure_check_constraint(
        conn,
        table="eval_case_result",
        constraint="chk_eval_case_result_sql_safe",
        ddl="ALTER TABLE eval_case_result ADD CONSTRAINT chk_eval_case_result_sql_safe CHECK (sql_safe IN (0, 1))",
    )
    _ensure_check_constraint(
        conn,
        table="eval_case_result",
        constraint="chk_eval_case_result_reflection_repair_ok",
        ddl=(
            "ALTER TABLE eval_case_result ADD CONSTRAINT chk_eval_case_result_reflection_repair_ok "
            "CHECK (reflection_repair_ok IN (0, 1))"
        ),
    )
    _ensure_check_constraint(
        conn,
        table="eval_case_result",
        constraint="chk_eval_case_result_evidence_coverage",
        ddl=(
            "ALTER TABLE eval_case_result ADD CONSTRAINT chk_eval_case_result_evidence_coverage "
            "CHECK (evidence_coverage >= 0 AND evidence_coverage <= 1)"
        ),
    )


def _ensure_evidence_identity_schema(conn) -> None:
    """Upgrade the empty local evidence table used by `make seed`.

    `main()` clears evidence before this function runs. Existing non-empty
    databases must use the explicit production migration instead.
    """

    has_evidence_pk = _schema_column_exists(conn, table="evidence", column="evidence_pk")
    has_alias = _schema_column_exists(conn, table="evidence", column="alias")
    if has_evidence_pk and has_alias:
        _ensure_index(
            conn,
            table="evidence",
            index="uq_evidence_id",
            ddl="ALTER TABLE evidence ADD UNIQUE KEY uq_evidence_id (evidence_id)",
        )
        _ensure_index(
            conn,
            table="evidence",
            index="uq_evidence_run_alias",
            ddl="ALTER TABLE evidence ADD UNIQUE KEY uq_evidence_run_alias (run_id, alias)",
        )
        return
    if has_evidence_pk != has_alias:
        raise RuntimeError("EVIDENCE_SCHEMA_PARTIAL")

    conn.execute(
        text(
            "ALTER TABLE evidence "
            "MODIFY COLUMN evidence_id VARCHAR(192) NOT NULL, "
            "DROP PRIMARY KEY, "
            "ADD COLUMN evidence_pk BIGINT UNSIGNED NOT NULL AUTO_INCREMENT FIRST, "
            "ADD COLUMN alias VARCHAR(96) NOT NULL AFTER run_id, "
            "ADD PRIMARY KEY (evidence_pk), "
            "ADD UNIQUE KEY uq_evidence_id (evidence_id), "
            "ADD UNIQUE KEY uq_evidence_run_alias (run_id, alias)"
        )
    )


def _schema_column_exists(conn, *, table: str, column: str) -> bool:
    exists = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND COLUMN_NAME = :column
            """
        ),
        {"table": table, "column": column},
    ).scalar_one()
    return int(exists) != 0


def _ensure_column(conn, *, table: str, column: str, ddl: str) -> None:
    exists = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND COLUMN_NAME = :column
            """
        ),
        {"table": table, "column": column},
    ).scalar_one()
    if int(exists) == 0:
        conn.execute(text(ddl))


def _drop_column_default(conn, *, table: str, column: str) -> None:
    column_default = conn.execute(
        text(
            """
            SELECT COLUMN_DEFAULT
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND COLUMN_NAME = :column
            LIMIT 1
            """
        ),
        {"table": table, "column": column},
    ).scalar_one_or_none()
    if column_default is not None:
        conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT"))


def _ensure_index(conn, *, table: str, index: str, ddl: str) -> None:
    exists = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND INDEX_NAME = :index
            """
        ),
        {"table": table, "index": index},
    ).scalar_one()
    if int(exists) == 0:
        conn.execute(text(ddl))


def _ensure_check_constraint(conn, *, table: str, constraint: str, ddl: str) -> None:
    exists = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND CONSTRAINT_NAME = :constraint
              AND CONSTRAINT_TYPE = 'CHECK'
            """
        ),
        {"table": table, "constraint": constraint},
    ).scalar_one()
    if int(exists) == 0:
        conn.execute(text(ddl))


def _insert_dimensions(conn, *, profile: SeedProfileConfig) -> dict[str, int]:
    """写维度表：dim_product（9 商品）与 dim_user（80 用户，注册日/城市确定性派生）。"""
    conn.execute(
        text(
            """
            INSERT INTO dim_product (product_id, product_name, category, price)
            VALUES (:product_id, :product_name, :category, :price)
            """
        ),
        [
            {
                "product_id": product_id,
                "product_name": name,
                "category": category,
                "price": price,
            }
            for product_id, name, category, price in profile.products
        ],
    )
    conn.execute(
        text(
            """
            INSERT INTO dim_user (user_id, reg_date, city)
            VALUES (:user_id, :reg_date, :city)
            """
        ),
        [
            {
                "user_id": user_id,
                "reg_date": date(2025, 1, 1) + timedelta(days=user_id % 365),
                "city": ["Tokyo", "Osaka", "Nagoya", "Fukuoka", "Sapporo", "Sendai"][user_id % 6],
            }
            for user_id in range(1, profile.user_count + 1)
        ],
    )
    return {"dim_product": len(profile.products), "dim_user": profile.user_count}


def _insert_metric_definitions(conn) -> None:
    """写 metric_definition：所有 renderer 支持指标的口径。"""
    metric_rows = [
        {
            "metric_id": "gmv",
            "display_name": "GMV",
            "formula": "sum(order_amount where is_paid=1)",
            "metric_family": "gmv_family",
            "numerator_sql_fragment": "SUM(order_amount)",
            "denominator_sql_fragment": None,
            "higher_is_better": 1,
            "source_table": "fact_order",
            "allowed_dimensions": json.dumps(["channel", "category", "device", "product"]),
        },
        {
            "metric_id": "net_gmv",
            "display_name": "Net GMV",
            "formula": "sum(order_amount-refund_amount where is_paid=1)",
            "metric_family": "gmv_family",
            "numerator_sql_fragment": "SUM(order_amount-refund_amount)",
            "denominator_sql_fragment": None,
            "higher_is_better": 1,
            "source_table": "fact_order",
            "allowed_dimensions": json.dumps(["channel", "category", "device", "product"]),
        },
        {
            "metric_id": "pay_cvr",
            "display_name": "Pay CVR",
            "formula": "sum(pay_user_cnt)/sum(uv)",
            "metric_family": "rate_family",
            "numerator_sql_fragment": "SUM(pay_user_cnt)",
            "denominator_sql_fragment": "SUM(uv)",
            "higher_is_better": 1,
            "source_table": "fact_traffic",
            "allowed_dimensions": json.dumps(["channel", "category", "device", "product"]),
        },
        {
            "metric_id": "refund_rate",
            "display_name": "Refund Rate",
            "formula": "sum(refund_amount)/sum(order_amount)",
            "metric_family": "rate_family",
            "numerator_sql_fragment": "SUM(refund_amount)",
            "denominator_sql_fragment": "SUM(order_amount)",
            "higher_is_better": 0,  # 退款率越低越好
            "source_table": "fact_order",
            "allowed_dimensions": json.dumps(["channel", "category", "device", "product"]),
        },
        {
            "metric_id": "uv",
            "display_name": "UV",
            "formula": "sum(uv)",
            "metric_family": "gmv_family",
            "numerator_sql_fragment": "SUM(uv)",
            "denominator_sql_fragment": None,
            "higher_is_better": 1,
            "source_table": "fact_traffic",
            "allowed_dimensions": json.dumps(["channel", "category", "device", "product"]),
        },
        {
            "metric_id": "aov",
            "display_name": "AOV",
            "formula": "sum(order_amount where is_paid=1)/count(paid orders)",
            "metric_family": "gmv_family",
            "numerator_sql_fragment": "SUM(order_amount)",
            "denominator_sql_fragment": "COUNT(order_id)",
            "higher_is_better": 1,
            "source_table": "fact_order",
            "allowed_dimensions": json.dumps(["channel", "category", "device", "product"]),
        },
        {
            "metric_id": "stockout_rate",
            "display_name": "Stockout Rate",
            "formula": "sum(stockout_hours)/sum(avail_hours)",
            "metric_family": "rate_family",
            "numerator_sql_fragment": "SUM(stockout_hours)",
            "denominator_sql_fragment": "SUM(avail_hours)",
            "higher_is_better": 0,
            "source_table": "fact_inventory",
            "allowed_dimensions": json.dumps(["category", "warehouse", "product"]),
        },
        {
            "metric_id": "complaint_rate",
            "display_name": "Complaint Rate",
            "formula": "sum(is_complaint)/count(ticket_id)",
            "metric_family": "rate_family",
            "numerator_sql_fragment": "SUM(is_complaint)",
            "denominator_sql_fragment": "COUNT(ticket_id)",
            "higher_is_better": 0,
            "source_table": "fact_customer_ticket",
            "allowed_dimensions": json.dumps(["category", "product"]),
        },
    ]
    conn.execute(
        text(
            """
            INSERT INTO metric_definition (
              metric_id, display_name, formula, metric_family, numerator_sql_fragment,
              denominator_sql_fragment, higher_is_better, source_table,
              allowed_dimensions
            )
            VALUES (
              :metric_id, :display_name, :formula, :metric_family, :numerator_sql_fragment,
              :denominator_sql_fragment, :higher_is_better, :source_table,
              :allowed_dimensions
            )
            """
        ),
        metric_rows,
    )


def _insert_semantic_memory(conn) -> None:
    rows = conn.execute(
        text(
            """
            SELECT metric_id, display_name, formula, metric_family, numerator_sql_fragment,
                   denominator_sql_fragment, higher_is_better, source_table,
                   allowed_dimensions
            FROM metric_definition
            ORDER BY metric_id
            """
        )
    ).mappings().all()
    conn.execute(
        text(
            """
            INSERT INTO memory_record (
              memory_id, layer, mem_key, payload, confidence, source,
              version, ttl_days, created_at
            )
            VALUES (
              :memory_id, :layer, :mem_key, :payload, :confidence, :source,
              :version, :ttl_days, :created_at
            )
            """
        ),
        [
            {
                "memory_id": f"semantic-{row['metric_id']}",
                "layer": "semantic",
                "mem_key": f"{row['metric_id']}|semantic",
                "payload": json.dumps(
                    {
                        "metric_id": row["metric_id"],
                        "display_name": row["display_name"],
                        "aliases": _metric_aliases(row),
                        "formula": row["formula"],
                        "metric_family": row["metric_family"],
                        "numerator_sql_fragment": row["numerator_sql_fragment"],
                        "denominator_sql_fragment": row["denominator_sql_fragment"],
                        "business_rules": {
                            "higher_is_better": bool(row["higher_is_better"]),
                            "source_table": row["source_table"],
                        },
                        "higher_is_better": bool(row["higher_is_better"]),
                        "source_table": row["source_table"],
                        "allowed_dimensions": json.loads(row["allowed_dimensions"]),
                        "dimension_meanings": [
                            {
                                "dimension": dimension,
                                "source_column": dimension,
                                "meaning": f"{dimension} breakdown for {row['metric_id']}",
                            }
                            for dimension in json.loads(row["allowed_dimensions"])
                        ],
                    },
                    sort_keys=True,
                ),
                "confidence": 1.0,
                "source": "system_verified",
                "version": 1,
                "ttl_days": None,
                "created_at": datetime_for_seed(),
            }
            for row in rows
        ],
    )


def _insert_memory_treatment_memory(conn) -> None:
    payload = {
        "metric_id": "gmv",
        "question_family": "gmv_drop",
        "analysis_strategy": "standard",
        "eval_suites": ["memory-treatment"],
        "preferred_dimensions": ["product"],
        "preferred_signal_types": ["inventory"],
        "prior_root_causes": ["aov_drop"],
        "note": "Planning prior for memory-treatment eval; final answer must still be verified by current-run evidence.",
    }
    conn.execute(
        text(
            """
            INSERT INTO memory_record (
              memory_id, layer, mem_key, payload, confidence, source,
              version, ttl_days, created_at
            )
            VALUES (
              :memory_id, :layer, :mem_key, :payload, :confidence, :source,
              :version, :ttl_days, :created_at
            )
            """
        ),
        {
            "memory_id": "memory-treatment-gmv-product-prior",
            "layer": "case",
            "mem_key": "gmv|run",
            "payload": json.dumps(payload, sort_keys=True),
            "confidence": 0.95,
            "source": "system_verified",
            "version": 1,
            "ttl_days": None,
            "created_at": datetime_for_seed(),
        },
    )


def _metric_aliases(row) -> list[str]:
    aliases = {
        str(row["metric_id"]),
        str(row["display_name"]),
        str(row["display_name"]).lower(),
    }
    return sorted(alias for alias in aliases if alias)


def datetime_for_seed():
    return datetime(2026, 6, 6)


def _insert_business_facts(conn, rng: random.Random, *, profile: SeedProfileConfig) -> dict[str, int]:
    """生成业务事实；acceptance/stress profile 使用同一确定性生成器扩大实体规模。"""
    traffic_rows: list[dict[str, object]] = []
    inventory_rows: list[dict[str, object]] = []
    campaign_rows: list[dict[str, object]] = []
    order_rows: list[dict[str, object]] = []
    ticket_rows: list[dict[str, object]] = []
    row_counts = {
        "fact_campaign": 0,
        "fact_inventory": 0,
        "fact_traffic": 0,
        "fact_order": 0,
        "fact_customer_ticket": 0,
    }
    order_id = 1
    ticket_id = 1
    # 数据窗口：以 TARGET_DATE 为最后一天，往前 60 天（保证前 4 个同星期几基线都存在）。
    start_date = TARGET_DATE - timedelta(days=profile.history_days - 1)

    for offset in range(profile.history_days):
        business_date = start_date + timedelta(days=offset)
        weekday_factor = 1.10 if business_date.weekday() < 5 else 0.86  # 工作日高、周末低
        seasonal_factor = 1.0 + (offset % 14) * 0.006  # 轻微的双周季节波动

        # —— 投放（fact_campaign）：目标日 paid_ads/social 投放骤降 ——
        for campaign_offset in range(profile.campaign_count):
            channel = profile.channels[campaign_offset % len(profile.channels)]
            campaign_index = 1001 + campaign_offset
            spend_mult, click_mult = campaign_multiplier(
                business_date=business_date, channel=channel
            )
            if business_date in COMPLEX_INJECTION_DATES:
                lagged_spend_mult, lagged_click_mult, _, _ = lagged_campaign_multiplier(
                    business_date=business_date,
                    channel=channel,
                )
                spend_mult *= lagged_spend_mult
                click_mult *= lagged_click_mult
            channel_spend_factor = _channel_spend_factor(channel)
            base_spend = Decimal("900.00") * Decimal(str(weekday_factor * seasonal_factor * channel_spend_factor))
            base_clicks = int(4200 * weekday_factor * seasonal_factor * channel_spend_factor)
            campaign_rows.append(
                {
                    "business_date": business_date,
                    "campaign_id": campaign_index,
                    "channel": channel,
                    "spend": (base_spend * Decimal(str(spend_mult))).quantize(Decimal("0.01")),
                    "clicks": int(base_clicks * click_mult),
                    "impressions": int(base_clicks * click_mult * 8),
                }
            )
        row_counts["fact_campaign"] += len(campaign_rows)
        _flush_campaign_rows(conn, campaign_rows)

        for product_id, _, category, price in profile.products:
            # —— 库存（fact_inventory）：每商品 × 每仓库；目标日 electronics 缺货 ——
            for warehouse_index, warehouse in enumerate(profile.warehouses):
                stockout_value = stockout_hours(
                    business_date=business_date,
                    category=category,
                    warehouse_index=warehouse_index,
                )
                if business_date in COMPLEX_INJECTION_DATES:
                    stockout_override = multi_cause_stockout_hours(
                        business_date=business_date,
                        category=category,
                    )
                    if stockout_override is not None:
                        stockout_value = stockout_override
                inventory_rows.append(
                    {
                        "business_date": business_date,
                        "product_id": product_id,
                        "warehouse": warehouse,
                        "stockout_hours": Decimal(str(stockout_value)).quantize(Decimal("0.01")),
                        "avail_hours": Decimal("24.00"),
                    }
                )
                if len(inventory_rows) >= profile.batch_size:
                    row_counts["fact_inventory"] += len(inventory_rows)
                    _flush_inventory_rows(conn, inventory_rows)

            # —— 流量（fact_traffic）+ 订单（fact_order）：渠道 × 设备 ——
            for channel in profile.channels:
                channel_factor = _channel_traffic_factor(channel)
                for device in profile.devices:
                    device_factor = _device_factor(device)
                    category_factor = _category_factor(category)
                    # UV 基数 = 各结构因子连乘；再乘异常倍率，并加少量随机抖动。
                    uv_base = (
                        profile.traffic_base
                        * weekday_factor
                        * seasonal_factor
                        * channel_factor
                        * device_factor
                        * category_factor
                    )
                    uv_mult, pay_mult = traffic_multiplier(
                        business_date=business_date,
                        channel=channel,
                        device=device,
                        category=category,
                        product_id=product_id,
                    )
                    if business_date in COMPLEX_INJECTION_DATES:
                        multi_uv_mult, multi_pay_mult = multi_cause_traffic_multiplier(
                            business_date=business_date,
                            channel=channel,
                            category=category,
                        )
                        interaction_uv_mult, interaction_pay_mult = interaction_multiplier(
                            business_date=business_date,
                            channel=channel,
                            category=category,
                        )
                        _, _, lagged_uv_mult, lagged_pay_mult = lagged_campaign_multiplier(
                            business_date=business_date,
                            channel=channel,
                        )
                        weak_uv_mult, weak_pay_mult = weak_signal_multiplier(
                            business_date=business_date,
                            channel=channel,
                        )
                        cvr_suppressor = multi_cause_cvr_suppressor(
                            business_date=business_date,
                            channel=channel,
                        )
                        residual_uv_mult, residual_pay_mult = residual_traffic_multiplier(
                            business_date=business_date,
                            channel=channel,
                        )
                        uv_mult *= multi_uv_mult * interaction_uv_mult * lagged_uv_mult * weak_uv_mult * residual_uv_mult
                        pay_mult *= multi_pay_mult * interaction_pay_mult * lagged_pay_mult * weak_pay_mult * cvr_suppressor * residual_pay_mult
                    uv = max(1, int(uv_base * uv_mult + rng.randint(0, 6)))
                    base_cvr = 0.082 if device == "desktop" else 0.069
                    pay_user_cnt = max(profile.min_pay_user_per_cell, int(uv * base_cvr * pay_mult))
                    if business_date == TARGET_DATE and (category == "electronics" or product_id in {2, 3}):
                        pay_user_cnt = max(1, pay_user_cnt)
                    traffic_rows.append(
                        {
                            "business_date": business_date,
                            "channel": channel,
                            "device": device,
                            "product_id": product_id,
                            "uv": uv,
                            "pv": uv * 3 + rng.randint(0, 20),
                            "add_cart_cnt": max(0, int(uv * 0.18)),
                            "pay_user_cnt": pay_user_cnt,
                        }
                    )
                    if len(traffic_rows) >= profile.batch_size:
                        row_counts["fact_traffic"] += len(traffic_rows)
                        _flush_traffic_rows(conn, traffic_rows)
                    # 每个支付用户落一条订单；目标日问题商品退款概率飙升。
                    for order_index in range(pay_user_cnt):
                        refund_rate = refund_multiplier(
                            business_date=business_date, product_id=product_id, category=category
                        )
                        is_refunded = 1 if rng.random() < refund_rate else 0
                        order_amount = (price * Decimal(str(order_amount_multiplier(
                            business_date=business_date,
                            category=category,
                            product_id=product_id,
                        )))).quantize(Decimal("0.01"))
                        refund_amount = order_amount if is_refunded else Decimal("0.00")
                        order_rows.append(
                            {
                                "order_id": order_id,
                                "business_date": business_date,
                                "user_id": ((order_id + order_index) % profile.user_count) + 1,
                                "product_id": product_id,
                                "channel": channel,
                                "device": device,
                                "order_amount": order_amount,
                                "is_paid": 1,
                                "is_refunded": is_refunded,
                                "refund_amount": refund_amount,
                            }
                        )
                        order_id += 1
                        if len(order_rows) >= profile.batch_size:
                            row_counts["fact_order"] += len(order_rows)
                            _flush_order_rows(conn, order_rows)

            # —— 工单（fact_customer_ticket）：投诉数与非投诉支持工单分开，complaint_rate 才能真实波动。 ——
            complaint_total = complaint_count(
                business_date=business_date,
                product_id=product_id,
                category=category,
            )
            support_total = support_ticket_count(
                business_date=business_date,
                product_id=product_id,
                category=category,
            )
            for ticket_index in range(complaint_total + support_total):
                is_complaint = 1 if ticket_index < complaint_total else 0
                ticket_rows.append(
                    {
                        "ticket_id": ticket_id,
                        "business_date": business_date,
                        "product_id": product_id,
                        "ticket_type": "quality" if is_complaint else "support",
                        "is_complaint": is_complaint,
                    }
                )
                ticket_id += 1
                if len(ticket_rows) >= profile.batch_size:
                    row_counts["fact_customer_ticket"] += len(ticket_rows)
                    _flush_ticket_rows(conn, ticket_rows)

    row_counts["fact_campaign"] += len(campaign_rows)
    _flush_campaign_rows(conn, campaign_rows)
    row_counts["fact_inventory"] += len(inventory_rows)
    _flush_inventory_rows(conn, inventory_rows)
    row_counts["fact_traffic"] += len(traffic_rows)
    _flush_traffic_rows(conn, traffic_rows)
    row_counts["fact_order"] += len(order_rows)
    _flush_order_rows(conn, order_rows)
    row_counts["fact_customer_ticket"] += len(ticket_rows)
    _flush_ticket_rows(conn, ticket_rows)
    return row_counts


def _channel_spend_factor(channel: str) -> float:
    return {
        "organic": 0.18,
        "paid_ads": 1.0,
        "social": 0.65,
        "affiliate": 0.45,
        "email": 0.35,
        "referral": 0.30,
        "marketplace": 0.80,
        "influencer": 0.55,
        "retargeting": 0.62,
        "partner": 0.42,
    }[channel]


def _channel_traffic_factor(channel: str) -> float:
    return {
        "organic": 1.0,
        "paid_ads": 1.35,
        "social": 0.92,
        "affiliate": 0.72,
        "email": 0.68,
        "referral": 0.58,
        "marketplace": 1.12,
        "influencer": 0.74,
        "retargeting": 0.82,
        "partner": 0.64,
    }[channel]


def _device_factor(device: str) -> float:
    return {
        "mobile": 1.25,
        "desktop": 0.78,
        "tablet": 0.48,
        "app": 0.86,
    }[device]


def _category_factor(category: str) -> float:
    canonical = {"electronics": 1.18, "fashion": 0.95, "home": 0.84}
    if category in canonical:
        return canonical[category]
    suffix = int(category.rsplit("_", maxsplit=1)[1])
    return 0.62 + (suffix % 9) * 0.035


def _flush_campaign_rows(conn, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    conn.execute(
        text(
            """
            INSERT INTO fact_campaign
              (business_date, campaign_id, channel, spend, clicks, impressions)
            VALUES (:business_date, :campaign_id, :channel, :spend, :clicks, :impressions)
            """
        ),
        rows,
    )
    rows.clear()


def _flush_inventory_rows(conn, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    conn.execute(
        text(
            """
            INSERT INTO fact_inventory
              (business_date, product_id, warehouse, stockout_hours, avail_hours)
            VALUES (:business_date, :product_id, :warehouse, :stockout_hours, :avail_hours)
            """
        ),
        rows,
    )
    rows.clear()


def _flush_traffic_rows(conn, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    conn.execute(
        text(
            """
            INSERT INTO fact_traffic
              (business_date, channel, device, product_id, uv, pv, add_cart_cnt, pay_user_cnt)
            VALUES
              (:business_date, :channel, :device, :product_id, :uv, :pv, :add_cart_cnt, :pay_user_cnt)
            """
        ),
        rows,
    )
    rows.clear()


def _flush_order_rows(conn, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    conn.execute(
        text(
            """
            INSERT INTO fact_order
              (order_id, business_date, user_id, product_id, channel, device,
               order_amount, is_paid, is_refunded, refund_amount)
            VALUES
              (:order_id, :business_date, :user_id, :product_id, :channel, :device,
               :order_amount, :is_paid, :is_refunded, :refund_amount)
            """
        ),
        rows,
    )
    rows.clear()


def _flush_ticket_rows(conn, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    conn.execute(
        text(
            """
            INSERT INTO fact_customer_ticket
              (ticket_id, business_date, product_id, ticket_type, is_complaint)
            VALUES (:ticket_id, :business_date, :product_id, :ticket_type, :is_complaint)
            """
        ),
        rows,
    )
    rows.clear()


def _insert_ground_truth(conn, *, seed: int, seed_profile: str) -> None:
    """写 anomaly_ground_truth：regression 与 memory-treatment eval 真值，固定日期且幂等重建。"""
    rows = [
        {
            "case_id": "gmv_paid_ads_drop",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
        },
        {
            "case_id": "gmv_stockout_electronics",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "stockout",
            "dimension": "category",
            "element": "electronics",
        },
        {
            "case_id": "cvr_mobile_drop",
            "business_date": TARGET_DATE,
            "metric_id": "pay_cvr",
            "expected_anomaly": 1,
            "root_cause_type": "conversion_drop",
            "dimension": "device",
            "element": "mobile",
        },
        {
            "case_id": "refund_rate_product_quality",
            "business_date": TARGET_DATE,
            "metric_id": "refund_rate",
            "expected_anomaly": 1,
            "root_cause_type": "complaint_or_quality_issue",
            "dimension": "product",
            "element": "1",
        },
        {
            # 无异常 case：放在 6-04，期望判定为无异常、不建运营任务。
            "case_id": "gmv_no_anomaly",
            "business_date": GMV_NO_ANOMALY_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 0,
            "root_cause_type": "no_anomaly",
            "dimension": None,
            "element": None,
        },
        {
            "case_id": "C06_gmv_multi_channel_drop",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
        },
        {
            "case_id": "C07_gmv_category_channel_cross",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
        },
        {
            "case_id": "C08_gmv_aov_drop",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "aov_drop",
            "dimension": "product",
            "element": "2",
        },
        {
            "case_id": "C09_gmv_uv_organic_drop",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "organic",
        },
        {
            "case_id": "C10_gmv_price_change",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "aov_drop",
            "dimension": "category",
            "element": "fashion",
        },
        {
            "case_id": "C11_gmv_promo_end_falloff",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "affiliate",
        },
        {
            "case_id": "C12_gmv_single_sku_stockout",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "stockout",
            "dimension": "product",
            "element": "3",
        },
        {
            "case_id": "C13_net_gmv_refund_spike",
            "business_date": TARGET_DATE,
            "metric_id": "net_gmv",
            "expected_anomaly": 1,
            "root_cause_type": "complaint_or_quality_issue",
            "dimension": "product",
            "element": "1",
        },
        {
            "case_id": "C14_net_gmv_gmv_driven",
            "business_date": TARGET_DATE,
            "metric_id": "net_gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
        },
        {
            "case_id": "C15_refund_rate_logistics",
            "business_date": TARGET_DATE,
            "metric_id": "refund_rate",
            "expected_anomaly": 1,
            "root_cause_type": "complaint_or_quality_issue",
            "dimension": "category",
            "element": "fashion",
        },
        {
            "case_id": "C16_stockout_rate_warehouse",
            "business_date": TARGET_DATE,
            "metric_id": "stockout_rate",
            "expected_anomaly": 1,
            "root_cause_type": "stockout",
            "dimension": "warehouse",
            "element": "osaka",
        },
        {
            "case_id": "C17_complaint_rate_quality",
            "business_date": TARGET_DATE,
            "metric_id": "complaint_rate",
            "expected_anomaly": 1,
            "root_cause_type": "complaint_or_quality_issue",
            "dimension": "category",
            "element": "electronics",
        },
        {
            "case_id": "C18_cvr_channel_landing",
            "business_date": TARGET_DATE,
            "metric_id": "pay_cvr",
            "expected_anomaly": 1,
            "root_cause_type": "conversion_drop",
            "dimension": "channel",
            "element": "affiliate",
        },
        {
            "case_id": "C19_gmv_seasonal_false_positive",
            "business_date": GMV_NO_ANOMALY_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 0,
            "root_cause_type": "no_anomaly",
            "dimension": None,
            "element": None,
        },
        {
            "case_id": "C20_cvr_no_anomaly_noise",
            "business_date": GMV_NO_ANOMALY_DATE,
            "metric_id": "pay_cvr",
            "expected_anomaly": 0,
            "root_cause_type": "no_anomaly",
            "dimension": None,
            "element": None,
        },
        {
            "case_id": "C21_cvr_discovery",
            "business_date": TARGET_DATE,
            "metric_id": "pay_cvr",
            "expected_anomaly": 1,
            "root_cause_type": "conversion_drop",
            "dimension": "device",
            "element": "mobile",
        },
        {
            "case_id": "C22_gmv_borderline",
            "business_date": BORDERLINE_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 0,
            "root_cause_type": "no_anomaly",
            "dimension": None,
            "element": None,
        },
        {
            "case_id": "C23_uv_organic_drop",
            "business_date": TARGET_DATE,
            "metric_id": "uv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "organic",
        },
        {
            "case_id": "C24_gmv_positive_spike",
            "business_date": SPIKE_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
        },
        {
            "case_id": "C25_refund_discovery",
            "business_date": TARGET_DATE,
            "metric_id": "refund_rate",
            "expected_anomaly": 1,
            "root_cause_type": "complaint_or_quality_issue",
            "dimension": "product",
            "element": "1",
        },
        {
            "case_id": "C26_ambiguous_intent",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
        },
        {
            "case_id": "C27_composite_cause",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
        },
        {
            "case_id": "C28_multi_day_drift",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "organic",
        },
        {
            "case_id": "MC01_gmv_multi_cause_overall",
            "business_date": MULTI_CAUSE_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.48},
                {"root_cause_type": "stockout", "dimension": "category", "element": "electronics", "weight": 0.32},
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 0.20},
            ],
        },
        {
            "case_id": "MC02_uv_multi_channel_drop",
            "business_date": LAGGED_OBSERVE_DATE,
            "metric_id": "uv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.50},
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 0.35},
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 0.15},
            ],
        },
        {
            "case_id": "MC03_cvr_multi_signal_drop",
            "business_date": MULTI_CAUSE_CVR_DATE,
            "metric_id": "pay_cvr",
            "expected_anomaly": 1,
            "root_cause_type": "conversion_drop",
            "dimension": "channel",
            "element": "social",
            "root_causes": [
                {"root_cause_type": "conversion_drop", "dimension": "channel", "element": "social", "weight": 0.67},
                {"root_cause_type": "conversion_drop", "dimension": "channel", "element": "organic", "weight": 0.33},
            ],
        },
        {
            "case_id": "MC04_gmv_weak_set",
            "business_date": MULTI_CAUSE_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "affiliate",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 0.55},
                {"root_cause_type": "conversion_drop", "dimension": "channel", "element": "affiliate", "weight": 0.45},
            ],
        },
        {
            "case_id": "MC05_gmv_lag_stockout_mix",
            "business_date": LAGGED_OBSERVE_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "social",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 0.45},
                {"root_cause_type": "stockout", "dimension": "category", "element": "electronics", "weight": 0.35},
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.20},
            ],
        },
        {
            "case_id": "MC06_net_gmv_multi_driver",
            "business_date": MULTI_CAUSE_DATE,
            "metric_id": "net_gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.50},
                {"root_cause_type": "stockout", "dimension": "category", "element": "electronics", "weight": 0.30},
                {"root_cause_type": "conversion_drop", "dimension": "channel", "element": "affiliate", "weight": 0.20},
            ],
        },
        {
            "case_id": "MC07_uv_weak_multi_driver",
            "business_date": LAGGED_OBSERVE_DATE,
            "metric_id": "uv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "social",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 0.45},
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 0.30},
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.25},
            ],
        },
        {
            "case_id": "MC08_gmv_channel_category_mix",
            "business_date": MULTI_CAUSE_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.45},
                {"root_cause_type": "stockout", "dimension": "category", "element": "electronics", "weight": 0.35},
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 0.20},
            ],
        },
        {
            "case_id": "IX01_gmv_channel_category_interaction",
            "business_date": INTERACTION_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "interaction_channel_category",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": [
                {"root_cause_type": "interaction_channel_category", "dimension": "channel", "element": "paid_ads", "weight": 1.0},
            ],
        },
        {
            "case_id": "IX02_gmv_interaction_discovery",
            "business_date": INTERACTION_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "interaction_channel_category",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": [
                {"root_cause_type": "interaction_channel_category", "dimension": "channel", "element": "paid_ads", "weight": 1.0},
            ],
        },
        {
            "case_id": "IX03_uv_interaction_cell",
            "business_date": INTERACTION_DATE,
            "metric_id": "uv",
            "expected_anomaly": 1,
            "root_cause_type": "interaction_channel_category",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": [
                {"root_cause_type": "interaction_channel_category", "dimension": "channel", "element": "paid_ads", "weight": 1.0},
            ],
        },
        {
            "case_id": "IX04_gmv_interaction_no_single_driver",
            "business_date": INTERACTION_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "interaction_channel_category",
            "dimension": "category",
            "element": "electronics",
            "root_causes": [
                {"root_cause_type": "interaction_channel_category", "dimension": "category", "element": "electronics", "weight": 1.0},
            ],
        },
        {
            "case_id": "LG01_gmv_lagged_social",
            "business_date": LAGGED_OBSERVE_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "social",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 1.0},
            ],
        },
        {
            "case_id": "LG02_uv_lagged_social_discovery",
            "business_date": LAGGED_OBSERVE_DATE,
            "metric_id": "uv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "social",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "social", "weight": 1.0},
            ],
        },
        {
            "case_id": "WK01_gmv_weak_affiliate_boundary",
            "business_date": MULTI_CAUSE_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "affiliate",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "affiliate", "weight": 1.0},
            ],
        },
        {
            "case_id": "WK02_gmv_no_anomaly_weak",
            "business_date": GMV_NO_ANOMALY_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 0,
            "root_cause_type": "no_anomaly",
            "dimension": None,
            "element": None,
            "root_causes": [],
        },
        {
            "case_id": "RS01_gmv_residual_dual_mechanism",
            "business_date": RESIDUAL_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.58},
                {"root_cause_type": "aov_drop", "dimension": "category", "element": "fashion", "weight": 0.42},
            ],
        },
        {
            "case_id": "RS02_gmv_residual_discovery",
            "business_date": RESIDUAL_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.58},
                {"root_cause_type": "aov_drop", "dimension": "category", "element": "fashion", "weight": 0.42},
            ],
        },
        {
            "case_id": "M01_gmv_memory_product_prior",
            "split": "memory-treatment",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "aov_drop",
            "dimension": "product",
            "element": "2",
        },
    ]
    rows = [_ground_truth_row_with_metadata(row, seed=seed, seed_profile=seed_profile) for row in rows]
    conn.execute(
        text(
            """
            INSERT INTO anomaly_ground_truth
              (case_id, scenario_id, split, seed, profile, business_date, metric_id, expected_anomaly,
               root_cause_type, dimension, element, root_causes, confounders, expected_behavior)
            VALUES
              (:case_id, :scenario_id, :split, :seed, :profile, :business_date, :metric_id, :expected_anomaly,
               :root_cause_type, :dimension, :element, :root_causes, :confounders, :expected_behavior)
            """
        ),
        rows,
    )


def _ground_truth_row_with_metadata(row: dict[str, object], *, seed: int, seed_profile: str) -> dict[str, object]:
    row = _project_ground_truth_for_profile(row=row, seed_profile=seed_profile)
    expected_anomaly = bool(row["expected_anomaly"])
    root_causes: list[dict[str, object]] = []
    explicit_root_causes = row.get("root_causes")
    if explicit_root_causes is not None:
        if not isinstance(explicit_root_causes, list) or not all(isinstance(item, dict) for item in explicit_root_causes):
            raise ValueError("GROUND_TRUTH_ROOT_CAUSES_INVALID")
        root_causes = explicit_root_causes
    elif expected_anomaly:
        root_causes = [
            {
                "root_cause_type": row["root_cause_type"],
                "dimension": row["dimension"],
                "element": row["element"],
                "weight": 1.0,
            }
        ]
    return {
        **row,
        "scenario_id": row["case_id"],
        "split": row.get("split", "regression"),
        "seed": seed,
        "profile": seed_profile,
        "root_causes": json.dumps(root_causes),
        "confounders": json.dumps([]),
        "expected_behavior": json.dumps(
            {
                "expected_anomaly": expected_anomaly,
                "top1_policy": "dominant_effect" if expected_anomaly else "no_anomaly",
                "allow_top3": expected_anomaly,
            }
        ),
    }


def _project_ground_truth_for_profile(*, row: dict[str, object], seed_profile: str) -> dict[str, object]:
    projected = dict(row)
    if seed_profile in {"acceptance", "stress"} and projected.get("case_id") == "C08_gmv_aov_drop":
        projected["dimension"] = "category"
        projected["element"] = "fashion"
    return projected


if __name__ == "__main__":
    main()

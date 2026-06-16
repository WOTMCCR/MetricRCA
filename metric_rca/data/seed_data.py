"""种子数据生成器：可配置种子 + 固定业务日，幂等重建 60 天确定性数据 + 5 个 ground truth。

设计要点：
  - 完全可复现：默认 seed 与业务日固定，`make seed SEED=...` 可切换确定性数据版本。
  - 真实感：周内效应 + 季节性 + 渠道/类目/设备分布，叠加投放/库存/投诉退款的异常注入。
  - 可评估：写入 anomaly_ground_truth（4 个异常 case 在 TARGET_DATE，1 个无异常 case 在另一天），
    使后续 eval 能用"真因"逐 case 打分，而非靠人读。

对应 docs/COMPLIANCE_MATRIX.md 第 5 行；docs/MetricRCA.md §10。
"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from metric_rca.config.settings import get_settings
from metric_rca.data.anomaly_injection import (
    TARGET_DATE,
    campaign_multiplier,
    complaint_count,
    order_amount_multiplier,
    refund_multiplier,
    stockout_hours,
    support_ticket_count,
    traffic_multiplier,
)


DEFAULT_SEED = 20260606  # 默认随机种子：保证无参数时可复现
BUSINESS_TODAY = date(2026, 6, 6)
HISTORY_DAYS = 60
# 无异常 case 放在 TARGET_DATE 之外的另一天，避免与"目标日异常"在同指标同日冲突。
GMV_NO_ANOMALY_DATE = date(2026, 6, 4)

# 9 个商品横跨 electronics/fashion/home 三个类目，商品 1 为质量问题商品。
PRODUCTS = [
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
CHANNELS = ["organic", "paid_ads", "social", "affiliate"]
DEVICES = ["mobile", "desktop"]
WAREHOUSES = ["tokyo", "osaka"]

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
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    _wait_for_mysql(engine)  # 容器刚起时 MySQL 可能尚未就绪，重试等待
    rng = random.Random(_resolve_seed())  # 局部 RNG，确定性来源
    try:
        with engine.begin() as conn:  # 单事务：要么全部重建成功，要么回滚
            for table in TABLES_TO_CLEAR:
                conn.execute(text(f"DELETE FROM {table}"))
            _ensure_p6_schema(conn)
            _insert_dimensions(conn)
            _insert_metric_definitions(conn)
            _insert_semantic_memory(conn)
            _insert_business_facts(conn, rng)
            _insert_ground_truth(conn)
    finally:
        engine.dispose()  # 释放连接池


def _resolve_seed() -> int:
    """读取 seed override；非法值显式失败，避免悄悄回落到默认数据。"""
    raw = os.getenv("METRIC_RCA_DATA_SEED")
    if raw is None or raw == "":
        return DEFAULT_SEED
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError("SEED_INVALID: METRIC_RCA_DATA_SEED must be an integer") from exc


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
    """Apply the P6 trace token usage column to existing local databases."""
    _ensure_column(
        conn,
        table="trace_step",
        column="token_usage",
        ddl="ALTER TABLE trace_step ADD COLUMN token_usage JSON NULL AFTER latency_ms",
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


def _insert_dimensions(conn) -> None:
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
            for product_id, name, category, price in PRODUCTS
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
                "city": ["Tokyo", "Osaka", "Nagoya", "Fukuoka"][user_id % 4],
            }
            for user_id in range(1, 81)
        ],
    )


def _insert_metric_definitions(conn) -> None:
    """写 metric_definition：所有 renderer 支持指标的口径。"""
    metric_rows = [
        {
            "metric_id": "gmv",
            "display_name": "GMV",
            "formula": "sum(order_amount where is_paid=1)",
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
              metric_id, display_name, formula, numerator_sql_fragment,
              denominator_sql_fragment, higher_is_better, source_table,
              allowed_dimensions
            )
            VALUES (
              :metric_id, :display_name, :formula, :numerator_sql_fragment,
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
            SELECT metric_id, display_name, formula, numerator_sql_fragment,
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


def _metric_aliases(row) -> list[str]:
    aliases = {
        str(row["metric_id"]),
        str(row["display_name"]),
        str(row["display_name"]).lower(),
    }
    return sorted(alias for alias in aliases if alias)


def datetime_for_seed():
    return datetime(2026, 6, 6)


def _insert_business_facts(conn, rng: random.Random) -> None:
    """生成 60 天业务事实（投放/库存/流量/订单/工单），目标日叠加异常注入。"""
    traffic_rows = []
    inventory_rows = []
    campaign_rows = []
    order_rows = []
    ticket_rows = []
    order_id = 1
    ticket_id = 1
    # 数据窗口：以 TARGET_DATE 为最后一天，往前 60 天（保证前 4 个同星期几基线都存在）。
    start_date = TARGET_DATE - timedelta(days=HISTORY_DAYS - 1)

    for offset in range(HISTORY_DAYS):
        business_date = start_date + timedelta(days=offset)
        weekday_factor = 1.10 if business_date.weekday() < 5 else 0.86  # 工作日高、周末低
        seasonal_factor = 1.0 + (offset % 14) * 0.006  # 轻微的双周季节波动

        # —— 投放（fact_campaign）：目标日 paid_ads/social 投放骤降 ——
        for campaign_index, channel in enumerate(CHANNELS, start=1001):
            spend_mult, click_mult = campaign_multiplier(
                business_date=business_date, channel=channel
            )
            channel_spend_factor = {"organic": 0.18, "paid_ads": 1.0, "social": 0.65, "affiliate": 0.45}[channel]
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

        for product_id, _, category, price in PRODUCTS:
            # —— 库存（fact_inventory）：每商品 × 每仓库；目标日 electronics 缺货 ——
            for warehouse_index, warehouse in enumerate(WAREHOUSES):
                inventory_rows.append(
                    {
                        "business_date": business_date,
                        "product_id": product_id,
                        "warehouse": warehouse,
                        "stockout_hours": Decimal(
                            str(
                                stockout_hours(
                                    business_date=business_date,
                                    category=category,
                                    warehouse_index=warehouse_index,
                                )
                            )
                        ).quantize(Decimal("0.01")),
                        "avail_hours": Decimal("24.00"),
                    }
                )

            # —— 流量（fact_traffic）+ 订单（fact_order）：渠道 × 设备 ——
            for channel in CHANNELS:
                channel_factor = {"organic": 1.0, "paid_ads": 1.35, "social": 0.92, "affiliate": 0.72}[channel]
                for device in DEVICES:
                    device_factor = 1.25 if device == "mobile" else 0.78
                    category_factor = {"electronics": 1.18, "fashion": 0.95, "home": 0.84}[
                        category
                    ]
                    # UV 基数 = 各结构因子连乘；再乘异常倍率，并加少量随机抖动。
                    uv_base = (
                        82
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
                    uv = max(1, int(uv_base * uv_mult + rng.randint(0, 6)))
                    base_cvr = 0.082 if device == "desktop" else 0.069
                    pay_user_cnt = max(0, int(uv * base_cvr * pay_mult))
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
                                "user_id": ((order_id + order_index) % 80) + 1,
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

    # 批量写入各事实表（executemany）。
    conn.execute(
        text(
            """
            INSERT INTO fact_campaign
              (business_date, campaign_id, channel, spend, clicks, impressions)
            VALUES (:business_date, :campaign_id, :channel, :spend, :clicks, :impressions)
            """
        ),
        campaign_rows,
    )
    conn.execute(
        text(
            """
            INSERT INTO fact_inventory
              (business_date, product_id, warehouse, stockout_hours, avail_hours)
            VALUES (:business_date, :product_id, :warehouse, :stockout_hours, :avail_hours)
            """
        ),
        inventory_rows,
    )
    conn.execute(
        text(
            """
            INSERT INTO fact_traffic
              (business_date, channel, device, product_id, uv, pv, add_cart_cnt, pay_user_cnt)
            VALUES
              (:business_date, :channel, :device, :product_id, :uv, :pv, :add_cart_cnt, :pay_user_cnt)
            """
        ),
        traffic_rows,
    )
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
        order_rows,
    )
    conn.execute(
        text(
            """
            INSERT INTO fact_customer_ticket
              (ticket_id, business_date, product_id, ticket_type, is_complaint)
            VALUES (:ticket_id, :business_date, :product_id, :ticket_type, :is_complaint)
            """
        ),
        ticket_rows,
    )


def _insert_ground_truth(conn) -> None:
    """写 anomaly_ground_truth：P7 20-case library，固定日期且幂等重建。"""
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
    ]
    conn.execute(
        text(
            """
            INSERT INTO anomaly_ground_truth
              (case_id, business_date, metric_id, expected_anomaly,
               root_cause_type, dimension, element)
            VALUES
              (:case_id, :business_date, :metric_id, :expected_anomaly,
               :root_cause_type, :dimension, :element)
            """
        ),
        rows,
    )


if __name__ == "__main__":
    main()

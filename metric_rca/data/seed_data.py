from __future__ import annotations

import json
import random
import time
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from metric_rca.config.settings import get_settings
from metric_rca.data.anomaly_injection import (
    TARGET_DATE,
    campaign_multiplier,
    complaint_count,
    refund_multiplier,
    stockout_hours,
    traffic_multiplier,
)


SEED = 20260606
BUSINESS_TODAY = date(2026, 6, 6)
HISTORY_DAYS = 60

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
CHANNELS = ["organic", "paid_ads", "affiliate"]
DEVICES = ["mobile", "desktop"]
WAREHOUSES = ["tokyo", "osaka"]

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
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    _wait_for_mysql(engine)
    rng = random.Random(SEED)
    try:
        with engine.begin() as conn:
            for table in TABLES_TO_CLEAR:
                conn.execute(text(f"DELETE FROM {table}"))
            _insert_dimensions(conn)
            _insert_metric_definitions(conn)
            _insert_business_facts(conn, rng)
            _insert_ground_truth(conn)
    finally:
        engine.dispose()


def _wait_for_mysql(engine) -> None:
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


def _insert_dimensions(conn) -> None:
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
            "higher_is_better": 0,
            "source_table": "fact_order",
            "allowed_dimensions": json.dumps(["channel", "category", "device", "product"]),
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


def _insert_business_facts(conn, rng: random.Random) -> None:
    traffic_rows = []
    inventory_rows = []
    campaign_rows = []
    order_rows = []
    ticket_rows = []
    order_id = 1
    ticket_id = 1
    start_date = TARGET_DATE - timedelta(days=HISTORY_DAYS - 1)

    for offset in range(HISTORY_DAYS):
        business_date = start_date + timedelta(days=offset)
        weekday_factor = 1.10 if business_date.weekday() < 5 else 0.86
        seasonal_factor = 1.0 + (offset % 14) * 0.006

        spend_mult, click_mult = campaign_multiplier(
            business_date=business_date, channel="paid_ads"
        )
        base_spend = Decimal("900.00") * Decimal(str(weekday_factor * seasonal_factor))
        base_clicks = int(4200 * weekday_factor * seasonal_factor)
        campaign_rows.append(
            {
                "business_date": business_date,
                "campaign_id": 1001,
                "channel": "paid_ads",
                "spend": (base_spend * Decimal(str(spend_mult))).quantize(Decimal("0.01")),
                "clicks": int(base_clicks * click_mult),
                "impressions": int(base_clicks * click_mult * 8),
            }
        )

        for product_id, _, category, price in PRODUCTS:
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

            for channel in CHANNELS:
                channel_factor = {"organic": 1.0, "paid_ads": 1.35, "affiliate": 0.72}[channel]
                for device in DEVICES:
                    device_factor = 1.25 if device == "mobile" else 0.78
                    category_factor = {"electronics": 1.18, "fashion": 0.95, "home": 0.84}[
                        category
                    ]
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
                    )
                    uv = max(1, int(uv_base * uv_mult + rng.randint(0, 6)))
                    base_cvr = 0.082 if device == "desktop" else 0.069
                    pay_user_cnt = max(0, int(uv * base_cvr * pay_mult))
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
                    for order_index in range(pay_user_cnt):
                        refund_rate = refund_multiplier(
                            business_date=business_date, product_id=product_id
                        )
                        is_refunded = 1 if rng.random() < refund_rate else 0
                        refund_amount = price if is_refunded else Decimal("0.00")
                        order_rows.append(
                            {
                                "order_id": order_id,
                                "business_date": business_date,
                                "user_id": ((order_id + order_index) % 80) + 1,
                                "product_id": product_id,
                                "channel": channel,
                                "device": device,
                                "order_amount": price,
                                "is_paid": 1,
                                "is_refunded": is_refunded,
                                "refund_amount": refund_amount,
                            }
                        )
                        order_id += 1

            for ticket_index in range(complaint_count(business_date=business_date, product_id=product_id)):
                ticket_rows.append(
                    {
                        "ticket_id": ticket_id,
                        "business_date": business_date,
                        "product_id": product_id,
                        "ticket_type": "quality" if product_id == 1 else "logistics",
                        "is_complaint": 1 if ticket_index % 2 == 0 or product_id == 1 else 0,
                    }
                )
                ticket_id += 1

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
            "case_id": "gmv_no_anomaly",
            "business_date": TARGET_DATE,
            "metric_id": "gmv",
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

from __future__ import annotations

from datetime import date


TARGET_DATE = date(2026, 6, 5)
QUALITY_PRODUCT_ID = 1


def traffic_multiplier(
    *,
    business_date: date,
    channel: str,
    device: str,
    category: str,
) -> tuple[float, float]:
    uv_multiplier = 1.0
    pay_user_multiplier = 1.0
    if business_date == TARGET_DATE and channel == "paid_ads":
        uv_multiplier *= 0.38
        pay_user_multiplier *= 0.35
    if business_date == TARGET_DATE and device == "mobile":
        pay_user_multiplier *= 0.55
    if business_date == TARGET_DATE and category == "electronics":
        pay_user_multiplier *= 0.62
    return uv_multiplier, pay_user_multiplier


def campaign_multiplier(*, business_date: date, channel: str) -> tuple[float, float]:
    if business_date == TARGET_DATE and channel == "paid_ads":
        return 0.30, 0.35
    return 1.0, 1.0


def stockout_hours(*, business_date: date, category: str, warehouse_index: int) -> float:
    if business_date == TARGET_DATE and category == "electronics":
        return 15.5 + warehouse_index
    return 0.5 + warehouse_index * 0.1


def refund_multiplier(*, business_date: date, product_id: int) -> float:
    if business_date == TARGET_DATE and product_id == QUALITY_PRODUCT_ID:
        return 0.75
    return 0.04


def complaint_count(*, business_date: date, product_id: int) -> int:
    if business_date == TARGET_DATE and product_id == QUALITY_PRODUCT_ID:
        return 18
    return 2 if product_id == QUALITY_PRODUCT_ID else 1

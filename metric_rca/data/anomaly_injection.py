"""异常注入：把"异常长什么样"与"数据怎么生成"解耦的一组纯函数。

每个函数只在 business_date == TARGET_DATE(2026-06-05) 时返回异常倍率/数值，其余日期返回正常值。
seed_data.py 在生成 60 天数据时调用它们，从而在目标日精确叠加 4 个"异常" ground truth case
的异常，并保证完全确定性（无随机）。无异常 case 走另一天（见 seed_data.GMV_NO_ANOMALY_DATE），
因此目标日与"无异常日"互不干扰。这样异常检测/归因才有"真因"可对照。

对应 docs/COMPLIANCE_MATRIX.md 第 5 行；docs/MetricRCA.md §10。
"""

from __future__ import annotations

from datetime import date


TARGET_DATE = date(2026, 6, 5)  # 固定目标日（昨天），4 个异常 case 注入于此
QUALITY_PRODUCT_ID = 1  # refund_rate_product_quality case 注入的问题商品


def traffic_multiplier(
    *,
    business_date: date,
    channel: str,
    device: str,
    category: str,
    product_id: int | None = None,
) -> tuple[float, float]:
    """返回 (uv 倍率, 支付人数倍率)，仅在目标日对特定切片注入下跌。

    - paid_ads：uv 与支付双降（gmv_paid_ads_drop）。
    - mobile：支付人数下降（cvr_mobile_drop）。
    - electronics：支付人数下降（配合缺货 → gmv_stockout_electronics）。
    """
    uv_multiplier = 1.0
    pay_user_multiplier = 1.0
    if business_date == TARGET_DATE and channel == "paid_ads":
        uv_multiplier *= 0.38
        pay_user_multiplier *= 0.35
    if business_date == TARGET_DATE and channel == "social":
        uv_multiplier *= 0.42
        pay_user_multiplier *= 0.40
    if business_date == TARGET_DATE and channel == "organic":
        uv_multiplier *= 0.22
    if business_date == TARGET_DATE and device == "mobile":
        pay_user_multiplier *= 0.55
    if business_date == TARGET_DATE and category == "electronics":
        pay_user_multiplier *= 0.62
    if business_date == TARGET_DATE and channel == "affiliate":
        pay_user_multiplier *= 0.45
    if business_date == TARGET_DATE and product_id == 3:
        pay_user_multiplier *= 0.35
    return uv_multiplier, pay_user_multiplier


def campaign_multiplier(*, business_date: date, channel: str) -> tuple[float, float]:
    """返回 (spend 倍率, clicks 倍率)；目标日 paid_ads 投放骤降（campaign_traffic_drop 的信号面）。"""
    if business_date == TARGET_DATE and channel == "paid_ads":
        return 0.30, 0.35
    if business_date == TARGET_DATE and channel == "social":
        return 0.35, 0.38
    if business_date == TARGET_DATE and channel == "organic":
        return 0.18, 0.12
    return 1.0, 1.0


def stockout_hours(*, business_date: date, category: str, warehouse_index: int) -> float:
    """库存缺货小时数；目标日 electronics 大幅缺货（gmv_stockout_electronics）。"""
    if business_date == TARGET_DATE and category == "electronics":
        return 15.5 + warehouse_index
    if business_date == TARGET_DATE and warehouse_index == 1:
        return 13.0
    return 0.5 + warehouse_index * 0.1


def refund_multiplier(*, business_date: date, product_id: int, category: str | None = None) -> float:
    """单笔退款概率；目标日问题商品退款率飙升（refund_rate_product_quality）。"""
    if business_date == TARGET_DATE and product_id == QUALITY_PRODUCT_ID:
        return 0.75
    if business_date == TARGET_DATE and category == "fashion":
        return 0.42
    return 0.04


def complaint_count(*, business_date: date, product_id: int, category: str | None = None) -> int:
    """投诉工单条数；目标日问题商品投诉激增（佐证质量问题）。"""
    if business_date == TARGET_DATE and product_id == QUALITY_PRODUCT_ID:
        return 18
    if business_date == TARGET_DATE and category == "electronics":
        return 10
    if business_date == TARGET_DATE and category == "fashion":
        return 8
    return 2 if product_id == QUALITY_PRODUCT_ID else 1


def support_ticket_count(*, business_date: date, product_id: int, category: str | None = None) -> int:
    """非投诉支持工单条数；让 complaint_rate 反映投诉占比而不是恒等于 1。"""
    if business_date == TARGET_DATE and product_id == QUALITY_PRODUCT_ID:
        return 3
    if business_date == TARGET_DATE and category in {"electronics", "fashion"}:
        return 4
    return 10 if product_id == QUALITY_PRODUCT_ID else 8


def order_amount_multiplier(*, business_date: date, category: str, product_id: int) -> float:
    """目标日价格/AOV 类异常：只改变事实订单金额，不改变 DDL 或指标口径。"""
    if business_date == TARGET_DATE and category == "fashion":
        return 0.18
    if business_date == TARGET_DATE and product_id == 2:
        return 0.18
    return 1.0

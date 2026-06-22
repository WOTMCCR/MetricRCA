"""异常注入：把"异常长什么样"与"数据怎么生成"解耦的一组纯函数。

seed_data.py 在生成 60 天数据时调用这些纯函数，从而在固定业务日期精确叠加
eval ground truth 所需的异常/边界场景，并保证完全确定性（无随机）。

对应 docs/COMPLIANCE_MATRIX.md 第 5 行；docs/MetricRCA.md §10。
"""

from __future__ import annotations

from datetime import date


TARGET_DATE = date(2026, 6, 5)  # 固定目标日（昨天），4 个异常 case 注入于此
BORDERLINE_DATE = date(2026, 6, 3)  # 弱 paid_ads 波动，预期不触发异常
SPIKE_DATE = date(2026, 6, 2)  # paid_ads 正向 spike，用于正向异常 eval
MULTI_CAUSE_DATE = date(2026, 5, 29)  # GMV 多主因（与 LAGGED_OBSERVE_DATE 解耦）
MULTI_CAUSE_CVR_DATE = date(2026, 5, 28)  # CVR 多主因（独立日期避免 GMV 污染）
RESIDUAL_DATE = date(2026, 5, 27)  # 残差解释：top1 + 显著残差归因
INTERACTION_DATE = date(2026, 5, 31)
LAGGED_DATE = date(2026, 5, 30)
LAGGED_OBSERVE_DATE = date(2026, 6, 1)
QUALITY_PRODUCT_ID = 1  # refund_rate_product_quality case 注入的问题商品


def traffic_multiplier(
    *,
    business_date: date,
    channel: str,
    device: str,
    category: str,
    product_id: int | None = None,
) -> tuple[float, float]:
    """返回 (uv 倍率, 支付人数倍率)，按固定 eval 日期对特定切片注入波动。

    - TARGET_DATE：覆盖原 20-case 的下跌/转化/缺货场景。
    - BORDERLINE_DATE：paid_ads 弱 UV 波动，用于 no-anomaly 边界。
    - SPIKE_DATE：paid_ads 正向流量/转化 spike。
    - TARGET_DATE 前：organic 轻度 drift，不应破坏 no-anomaly trap。
    """
    uv_multiplier = 1.0
    pay_user_multiplier = 1.0
    if business_date == BORDERLINE_DATE and channel == "paid_ads":
        uv_multiplier *= 0.88
        # Conversion compensation keeps this date a GMV no-anomaly while preserving a weak UV dip.
        pay_user_multiplier *= 1.15
    if business_date == SPIKE_DATE and channel == "paid_ads":
        uv_multiplier *= 2.5
        pay_user_multiplier *= 2.3
    if channel == "organic" and business_date == date(2026, 6, 3):
        uv_multiplier *= 0.95
    if channel == "organic" and business_date == date(2026, 6, 4):
        uv_multiplier *= 0.945
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
    """返回 (spend 倍率, clicks 倍率)，覆盖目标日下跌与 spike 日正向投放信号。"""
    if business_date == SPIKE_DATE and channel == "paid_ads":
        return 2.8, 2.5
    if business_date == TARGET_DATE and channel == "paid_ads":
        return 0.30, 0.35
    if business_date == TARGET_DATE and channel == "social":
        return 0.35, 0.38
    if business_date == TARGET_DATE and channel == "organic":
        return 0.18, 0.12
    if business_date == INTERACTION_DATE and channel == "paid_ads":
        return 0.40, 0.45
    if business_date == RESIDUAL_DATE and channel == "paid_ads":
        return 0.20, 0.25
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
        return 0.95
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
        return 0.03
    if business_date == TARGET_DATE and product_id == 2:
        return 0.03
    if business_date == RESIDUAL_DATE and category == "fashion":
        return 0.50
    return 1.0


def multi_cause_traffic_multiplier(*, business_date: date, channel: str, category: str) -> tuple[float, float]:
    if business_date in {MULTI_CAUSE_DATE, LAGGED_OBSERVE_DATE} and channel == "paid_ads":
        return 0.55, 1.0
    return 1.0, 1.0


def multi_cause_stockout_hours(*, business_date: date, category: str) -> float | None:
    if business_date in {MULTI_CAUSE_DATE, LAGGED_OBSERVE_DATE} and category == "electronics":
        return 12.0
    return None


def interaction_multiplier(*, business_date: date, channel: str, category: str) -> tuple[float, float]:
    """Interaction effect: paid_ads × electronics cell drops far beyond marginal product.

    Marginals are strong enough to trigger overall anomaly detection, but the
    cross-cell (0.02 × 0.02) is orders of magnitude worse than the marginal
    product (0.50 × 0.80 = 0.40), proving a genuine interaction effect.
    """
    if business_date != INTERACTION_DATE:
        return 1.0, 1.0
    if channel == "paid_ads" and category == "electronics":
        return 0.02, 0.02
    uv_multiplier = 0.50 if channel == "paid_ads" else 1.0
    pay_user_multiplier = 0.80 if category == "electronics" else 1.0
    return uv_multiplier, pay_user_multiplier


def lagged_campaign_multiplier(*, business_date: date, channel: str) -> tuple[float, float, float, float]:
    """Return (spend, clicks, observe-day UV, observe-day pay-user) multipliers.

    The observe-date UV multiplier is a same-day campaign signal proxy for the
    lagged effect. It lets current deterministic RCA tools see the manifested
    traffic drop; it is not a lag scan or lagged causal detector.
    """
    if channel != "social":
        return 1.0, 1.0, 1.0, 1.0
    if business_date == LAGGED_DATE:
        return 0.15, 0.10, 1.0, 1.0
    if business_date == LAGGED_OBSERVE_DATE:
        return 1.0, 1.0, 0.35, 1.0
    return 1.0, 1.0, 1.0, 1.0


def weak_signal_multiplier(*, business_date: date, channel: str) -> tuple[float, float]:
    if business_date in {MULTI_CAUSE_DATE, LAGGED_OBSERVE_DATE} and channel == "affiliate":
        return 0.82, 0.85
    return 1.0, 1.0


def multi_cause_cvr_suppressor(*, business_date: date, channel: str) -> float:
    """Pay-user suppressor for multi-cause CVR scenario (MULTI_CAUSE_CVR_DATE).

    Only suppresses pay_mult (not UV), so CVR = base_cvr × pay_mult drops while
    UV stays normal — producing a pure conversion drop signal.
    """
    if business_date != MULTI_CAUSE_CVR_DATE:
        return 1.0
    if channel == "social":
        return 0.35
    if channel == "organic":
        return 0.70
    return 1.0


def residual_traffic_multiplier(*, business_date: date, channel: str) -> tuple[float, float]:
    """Primary GMV driver for the residual scenario: paid_ads traffic collapse."""
    if business_date == RESIDUAL_DATE and channel == "paid_ads":
        return 0.25, 1.0
    return 1.0, 1.0

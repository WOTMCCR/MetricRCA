"""Derive business metrics and baseline comparisons from synthetic warehouse rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from metric_rca.data.scenario_spec import BaselineComparison, Selector
from metric_rca.data.shock_composer import selector_matches


class MetricDerivationError(ValueError):
    def __init__(self, code: str, message: str, *, context: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.context = dict(context or {})
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class MetricComparison:
    metric_id: str
    selector: dict[str, list[str]]
    target_date: str
    target_value: float
    baseline_dates: tuple[str, ...]
    baseline_value: float
    delta: float
    delta_ratio: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "selector": self.selector,
            "target_date": self.target_date,
            "target_value": self.target_value,
            "baseline_dates": list(self.baseline_dates),
            "baseline_value": self.baseline_value,
            "delta": self.delta,
            "delta_ratio": self.delta_ratio,
        }


def derive_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [derive_row(row) for row in rows]


def derive_row(source: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(source)
    uv = _nonnegative(row, "uv")
    sessions = _nonnegative(row, "sessions")
    orders = _nonnegative(row, "orders")
    unit_price = _nonnegative(row, "unit_price")
    promotion_discount = _nonnegative(row, "promotion_discount")
    refund_amount = _nonnegative(row, "refund_amount")
    stockout_hours = _nonnegative(row, "stockout_hours")
    complaints = _nonnegative(row, "complaints")
    spend = _nonnegative(row, "spend")
    if promotion_discount >= 1.0:
        raise MetricDerivationError("METRIC_IDENTITY_INVALID", "promotion_discount must be < 1")
    if orders > sessions + 1e-9:
        raise MetricDerivationError(
            "METRIC_IDENTITY_INVALID",
            "orders must not exceed sessions",
            context={"orders": orders, "sessions": sessions, "row_id": row.get("row_id")},
        )
    gmv = orders * unit_price * (1.0 - promotion_discount)
    if refund_amount > gmv:
        raise MetricDerivationError(
            "METRIC_IDENTITY_INVALID",
            "refund_amount must not exceed GMV",
            context={"refund_amount": refund_amount, "gmv": gmv, "row_id": row.get("row_id")},
        )
    if stockout_hours > 24.0:
        raise MetricDerivationError(
            "METRIC_IDENTITY_INVALID",
            "stockout_hours must not exceed 24",
            context={"stockout_hours": stockout_hours, "row_id": row.get("row_id")},
        )
    net_gmv = gmv - refund_amount
    row.update(
        {
            "gmv": round(gmv, 6),
            "net_gmv": round(net_gmv, 6),
            "pay_cvr": round(orders / sessions if sessions else 0.0, 9),
            "aov": round(gmv / orders if orders else 0.0, 6),
            "refund_rate": round(refund_amount / gmv if gmv else 0.0, 9),
            "stockout_rate": round(stockout_hours / 24.0, 9),
            "complaint_rate": round(complaints / orders if orders else 0.0, 9),
            "campaign_roi": round(gmv / spend if spend else 0.0, 9),
        }
    )
    return row


def aggregate_metric(
    rows: Iterable[Mapping[str, Any]],
    *,
    metric_id: str,
    business_date: date,
    selector: Selector,
) -> float:
    selected = [
        row
        for row in rows
        if str(row.get("business_date")) == business_date.isoformat() and selector_matches(row, selector)
    ]
    if not selected:
        raise MetricDerivationError(
            "METRIC_AGGREGATION_EMPTY",
            "metric aggregation selected no rows",
            context={"metric_id": metric_id, "business_date": business_date.isoformat(), "selector": selector},
        )
    if metric_id in {"gmv", "net_gmv", "uv", "orders", "spend", "complaints"}:
        return round(sum(_numeric_field(row, metric_id) for row in selected), 9)
    if metric_id == "pay_cvr":
        sessions = sum(_numeric_field(row, "sessions") for row in selected)
        orders = sum(_numeric_field(row, "orders") for row in selected)
        return round(orders / sessions if sessions else 0.0, 9)
    if metric_id == "aov":
        orders = sum(_numeric_field(row, "orders") for row in selected)
        gmv = sum(_numeric_field(row, "gmv") for row in selected)
        return round(gmv / orders if orders else 0.0, 9)
    if metric_id == "refund_rate":
        gmv = sum(_numeric_field(row, "gmv") for row in selected)
        refund_amount = sum(_numeric_field(row, "refund_amount") for row in selected)
        return round(refund_amount / gmv if gmv else 0.0, 9)
    if metric_id == "stockout_rate":
        return round(sum(_numeric_field(row, "stockout_rate") for row in selected) / len(selected), 9)
    if metric_id == "complaint_rate":
        orders = sum(_numeric_field(row, "orders") for row in selected)
        complaints = sum(_numeric_field(row, "complaints") for row in selected)
        return round(complaints / orders if orders else 0.0, 9)
    if metric_id == "campaign_roi":
        spend = sum(_numeric_field(row, "spend") for row in selected)
        gmv = sum(_numeric_field(row, "gmv") for row in selected)
        return round(gmv / spend if spend else 0.0, 9)
    raise MetricDerivationError("METRIC_ID_UNKNOWN", "unknown metric id", context={"metric_id": metric_id})


def compare_to_baseline(
    rows: Iterable[Mapping[str, Any]],
    *,
    metric_id: str,
    target_date: date,
    baseline: BaselineComparison,
) -> MetricComparison:
    materialized = list(rows)
    target_value = aggregate_metric(
        materialized,
        metric_id=metric_id,
        business_date=target_date,
        selector=baseline.selector,
    )
    baseline_dates = tuple(target_date + timedelta(days=offset) for offset in baseline.offset_days)
    baseline_values = [
        aggregate_metric(
            materialized,
            metric_id=metric_id,
            business_date=baseline_date,
            selector=baseline.selector,
        )
        for baseline_date in baseline_dates
    ]
    baseline_value = sum(baseline_values) / len(baseline_values)
    delta = target_value - baseline_value
    delta_ratio = delta / abs(baseline_value) if baseline_value else 0.0
    return MetricComparison(
        metric_id=metric_id,
        selector={key: list(value) for key, value in baseline.selector.items()},
        target_date=target_date.isoformat(),
        target_value=round(target_value, 9),
        baseline_dates=tuple(value.isoformat() for value in baseline_dates),
        baseline_value=round(baseline_value, 9),
        delta=round(delta, 9),
        delta_ratio=round(delta_ratio, 9),
    )


def _nonnegative(row: Mapping[str, Any], field: str) -> float:
    value = row.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or float(value) < 0.0:
        raise MetricDerivationError("METRIC_ROW_INVALID", f"{field} must be non-negative numeric")
    return float(value)


def _numeric_field(row: Mapping[str, Any], field: str) -> float:
    value = row.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise MetricDerivationError(
            "METRIC_ROW_INVALID",
            f"{field} must be numeric",
            context={"field": field, "row_id": row.get("row_id")},
        )
    return float(value)

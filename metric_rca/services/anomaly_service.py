"""Pure deterministic anomaly detection over already-fetched metric rows."""

from __future__ import annotations

from datetime import date
from statistics import mean, pstdev
from typing import Any

from pydantic import Field

from metric_rca.domain.models import Baseline, MetricDefinition, StrictModel


class AnomalyResult(StrictModel):
    ok: bool
    metric_id: str
    current_value: float | None = None
    baseline: Baseline | None = None
    delta: float | None = None
    delta_pct: float | None = None
    z_score: float | None = None
    bad_direction: bool = False
    is_anomaly: bool = False
    error_code: str | None = None
    result_summary: dict[str, Any] = Field(default_factory=dict)


def detect_anomaly_from_rows(
    *,
    current_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    metric_definition: MetricDefinition,
    thresh_pct: float,
    z_thresh: float,
    eps: float = 1e-9,
) -> AnomalyResult:
    current_values = [_metric_value(row) for row in current_rows]
    baseline_values = [_metric_value(row) for row in baseline_rows]
    baseline_dates = [_business_date(row) for row in baseline_rows]

    if not current_values:
        return AnomalyResult(
            ok=False,
            metric_id=metric_definition.metric_id,
            error_code="NO_CURRENT_DATA",
        )
    if len(baseline_values) < 3:
        return AnomalyResult(
            ok=False,
            metric_id=metric_definition.metric_id,
            error_code="INSUFFICIENT_BASELINE_DATA",
        )

    current_value = current_values[0]
    baseline_mean = mean(baseline_values)
    baseline_std = pstdev(baseline_values)
    delta = current_value - baseline_mean
    delta_pct = delta / baseline_mean if baseline_mean != 0 else 0.0
    z_score = delta / max(baseline_std, eps)
    bad_direction = delta < 0 if metric_definition.higher_is_better else delta > 0
    threshold_passed = abs(delta_pct) >= thresh_pct and abs(z_score) >= z_thresh
    is_anomaly = threshold_passed and bad_direction
    baseline = Baseline(
        baseline_dates=baseline_dates,
        baseline_mean=baseline_mean,
        baseline_std=baseline_std,
        sample_n=len(baseline_values),
    )
    summary = {
        "metric_id": metric_definition.metric_id,
        "current": current_value,
        "baseline_mean": baseline_mean,
        "baseline_std": baseline_std,
        "baseline_dates": [item.isoformat() for item in baseline_dates],
        "sample_n": len(baseline_values),
        "delta": delta,
        "delta_pct": delta_pct,
        "z_score": z_score,
        "bad_direction": bad_direction,
        "is_anomaly": is_anomaly,
    }
    return AnomalyResult(
        ok=True,
        metric_id=metric_definition.metric_id,
        current_value=current_value,
        baseline=baseline,
        delta=delta,
        delta_pct=delta_pct,
        z_score=z_score,
        bad_direction=bad_direction,
        is_anomaly=is_anomaly,
        error_code=None if is_anomaly else "NO_ANOMALY_DETECTED",
        result_summary=summary,
    )


def _metric_value(row: dict[str, Any]) -> float:
    if "metric_value" not in row or row["metric_value"] is None:
        raise ValueError("METRIC_VALUE_MISSING")
    return float(row["metric_value"])


def _business_date(row: dict[str, Any]) -> date:
    value = row.get("business_date")
    if isinstance(value, date):
        return value
    raise ValueError("BASELINE_DATE_MISSING")

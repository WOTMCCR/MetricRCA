"""Pure deterministic Adtributor EP/JS attribution.

This module intentionally has no repository, SQL, or settings dependency. Tool
code supplies already-fetched per-element evidence and threshold values.
"""

from __future__ import annotations

from collections import defaultdict
import math

from pydantic import Field

from metric_rca.domain.models import StrictModel


ADDITIVE_METRICS = frozenset({"gmv", "net_gmv", "uv"})
RATIO_METRICS = frozenset({"pay_cvr", "refund_rate", "stockout_rate", "complaint_rate"})


class AdtributorElement(StrictModel):
    dimension: str
    element: str
    actual: float
    forecast: float
    numerator_actual: float | None = None
    numerator_forecast: float | None = None
    denominator_actual: float | None = None
    denominator_forecast: float | None = None


class AdtributorElementScore(StrictModel):
    dimension: str
    element: str
    explanatory_power: float
    surprise_js: float
    actual: float
    forecast: float


class AdtributorCandidate(StrictModel):
    dimension_elements: list[tuple[str, str]]
    explanatory_power: float
    surprise_js: float
    element_scores: list[AdtributorElementScore]


class AdtributorResult(StrictModel):
    ok: bool
    candidates: list[AdtributorCandidate] = Field(default_factory=list)
    element_scores: list[AdtributorElementScore] = Field(default_factory=list)
    error_code: str | None = None


def attribute_elements(
    *,
    metric_id: str,
    elements: list[AdtributorElement],
    t_ep: float,
    t_eep: float,
) -> AdtributorResult:
    if metric_id in RATIO_METRICS and not _has_ratio_components(elements):
        return AdtributorResult(ok=False, error_code="ADTRIBUTOR_NOT_APPLICABLE")
    if metric_id not in ADDITIVE_METRICS:
        return AdtributorResult(ok=False, error_code="ADTRIBUTOR_NOT_APPLICABLE")
    if not elements:
        return AdtributorResult(ok=False, error_code="ADTRIBUTOR_NOT_APPLICABLE")

    grouped: dict[str, list[AdtributorElement]] = defaultdict(list)
    for element in elements:
        grouped[element.dimension].append(element)

    all_scores: list[AdtributorElementScore] = []
    candidates: list[AdtributorCandidate] = []
    for dimension, rows in grouped.items():
        actual_total = sum(row.actual for row in rows)
        forecast_total = sum(row.forecast for row in rows)
        total_delta = actual_total - forecast_total
        if total_delta == 0:
            continue
        dimension_scores = [
            AdtributorElementScore(
                dimension=dimension,
                element=row.element,
                explanatory_power=(row.actual - row.forecast) / total_delta,
                surprise_js=jensen_shannon_surprise(
                    actual=row.actual,
                    forecast=row.forecast,
                    actual_total=actual_total,
                    forecast_total=forecast_total,
                ),
                actual=row.actual,
                forecast=row.forecast,
            )
            for row in rows
        ]
        all_scores.extend(dimension_scores)
        selected = _greedy_select(dimension_scores, t_ep=t_ep, t_eep=t_eep)
        if selected:
            candidates.append(
                AdtributorCandidate(
                    dimension_elements=[(score.dimension, score.element) for score in selected],
                    explanatory_power=sum(score.explanatory_power for score in selected),
                    surprise_js=sum(score.surprise_js for score in selected),
                    element_scores=selected,
                )
            )

    ranked = sorted(candidates, key=lambda item: item.surprise_js, reverse=True)[:3]
    if not ranked:
        return AdtributorResult(ok=False, element_scores=all_scores, error_code="ADTRIBUTOR_NOT_APPLICABLE")
    return AdtributorResult(ok=True, candidates=ranked, element_scores=all_scores)


def jensen_shannon_surprise(
    *,
    actual: float,
    forecast: float,
    actual_total: float,
    forecast_total: float,
) -> float:
    if actual_total == 0 or forecast_total == 0:
        return 0.0
    p = forecast / forecast_total
    q = actual / actual_total
    return 0.5 * (_js_term(p, q) + _js_term(q, p))


def _js_term(first: float, second: float) -> float:
    if first == 0:
        return 0.0
    return first * math.log((2 * first) / (first + second))


def _greedy_select(
    scores: list[AdtributorElementScore],
    *,
    t_ep: float,
    t_eep: float,
) -> list[AdtributorElementScore]:
    selected: list[AdtributorElementScore] = []
    cumulative_ep = 0.0
    for score in sorted(scores, key=lambda item: item.surprise_js, reverse=True):
        if score.explanatory_power <= t_eep:
            continue
        selected.append(score)
        cumulative_ep += score.explanatory_power
        if cumulative_ep > t_ep:
            break
    return selected


def _has_ratio_components(elements: list[AdtributorElement]) -> bool:
    return all(
        element.numerator_actual is not None
        and element.numerator_forecast is not None
        and element.denominator_actual is not None
        and element.denominator_forecast is not None
        for element in elements
    )

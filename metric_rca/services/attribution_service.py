"""Pure deterministic attribution and decomposition logic for Phase 2."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from pydantic import Field

from metric_rca.config.settings import get_settings
from metric_rca.domain.enums import EvidenceVerdict, RootCauseType
from metric_rca.domain.models import MetricDefinition, RootCauseCandidate, StrictModel


class AttributionResult(StrictModel):
    ok: bool
    candidates: list[RootCauseCandidate] = Field(default_factory=list)
    error_code: str | None = None
    coverage: float = 0.0


def compute_dimension_contribution(
    *,
    metric_definition: MetricDefinition,
    dimension: str,
    current_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    evidence_ids: list[str],
    top_threshold: float = 0.60,
) -> AttributionResult:
    if not evidence_ids:
        return AttributionResult(ok=False, error_code="EVIDENCE_MISSING")
    if not current_rows or not baseline_rows:
        return AttributionResult(ok=False, error_code="ATTRIBUTION_COVERAGE_LOW")

    current_by_element = _values_by_element(current_rows, dimension)
    baseline_by_element = _baseline_means_by_element(baseline_rows, dimension)
    bad_delta_by_element: dict[str, float] = {}
    for element, baseline_value in baseline_by_element.items():
        current_value = current_by_element.get(element)
        if current_value is not None:
            if metric_definition.higher_is_better:
                bad_delta = max(0.0, baseline_value - current_value)
            else:
                bad_delta = max(0.0, current_value - baseline_value)
            bad_delta_by_element[element] = bad_delta

    total_bad_delta = sum(bad_delta_by_element.values())
    if total_bad_delta <= 0:
        return AttributionResult(ok=False, error_code="ATTRIBUTION_COVERAGE_LOW")

    raw_candidates: list[RootCauseCandidate] = []
    for element, bad_delta in bad_delta_by_element.items():
        if bad_delta <= 0:
            pass
        else:
            contribution_pct = bad_delta / total_bad_delta
            signal_severity = min(1.0, contribution_pct)
            evidence_support = 1.0 if evidence_ids else 0.0
            score = contribution_pct * signal_severity * evidence_support
            raw_candidates.append(
                RootCauseCandidate(
                    root_cause_type=_root_cause_type(metric_definition.metric_id, dimension, element),
                    dimension=dimension,
                    element=element,
                    contribution_pct=contribution_pct,
                    signal_severity=signal_severity,
                    evidence_support=evidence_support,
                    reflection_factor=1.0,
                    eng_confidence=score,
                    verdict=EvidenceVerdict.CONFIRMED.value if contribution_pct >= top_threshold else EvidenceVerdict.LIKELY.value,
                    evidence_ids=evidence_ids,
                )
            )

    ranked = rank_root_causes(raw_candidates)
    if not ranked or ranked[0].contribution_pct < top_threshold:
        return AttributionResult(ok=False, error_code="ATTRIBUTION_COVERAGE_LOW")
    return AttributionResult(ok=True, candidates=ranked, coverage=ranked[0].contribution_pct)


def compute_gmv_decomposition(
    *,
    current: dict[str, float],
    baseline: dict[str, float],
) -> dict[str, Any]:
    current_factors = _gmv_factors(current)
    baseline_factors = _gmv_factors(baseline)
    drops: dict[str, float] = {}
    for factor in ["uv", "pay_cvr", "aov"]:
        base = baseline_factors[factor]
        current_value = current_factors[factor]
        drops[factor] = max(0.0, (base - current_value) / base) if base else 0.0
    largest = max(drops, key=lambda key: drops[key])
    return {
        "current": current_factors,
        "baseline": baseline_factors,
        "relative_drops": drops,
        "largest_drop_factor": largest,
    }


def compute_net_gmv_components(*, gmv: float, refund: float) -> dict[str, float]:
    return {"gmv": gmv, "refund": refund, "net_gmv": gmv - refund}


def rank_root_causes(candidates: list[RootCauseCandidate]) -> list[RootCauseCandidate]:
    if not candidates:
        return []
    max_score = max(candidate.eng_confidence for candidate in candidates)
    normalized: list[RootCauseCandidate] = []
    for candidate in candidates:
        confidence = candidate.eng_confidence / max_score if max_score > 0 else 0.0
        normalized.append(candidate.model_copy(update={"eng_confidence": confidence}))
    return sorted(normalized, key=lambda item: item.eng_confidence, reverse=True)


def _values_by_element(rows: list[dict[str, Any]], dimension: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for row in rows:
        element = _element(row, dimension)
        values[element] = float(row["metric_value"])
    return values


def _baseline_means_by_element(rows: list[dict[str, Any]], dimension: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        element = _element(row, dimension)
        grouped[element].append(float(row["metric_value"]))
    return {element: mean(values) for element, values in grouped.items()}


def _element(row: dict[str, Any], dimension: str) -> str:
    if dimension not in row or row[dimension] is None:
        raise ValueError("DIMENSION_VALUE_MISSING")
    return str(row[dimension])


def _gmv_factors(row: dict[str, float]) -> dict[str, float]:
    uv = float(row["uv"])
    pay_user_cnt = float(row["pay_user_cnt"])
    gmv = float(row["gmv"])
    pay_cvr = pay_user_cnt / uv if uv else 0.0
    aov = gmv / pay_user_cnt if pay_user_cnt else 0.0
    return {
        "gmv": gmv,
        "uv": uv,
        "pay_user_cnt": pay_user_cnt,
        "pay_cvr": pay_cvr,
        "aov": aov,
        "reconstructed_gmv": uv * pay_cvr * aov,
    }


def _root_cause_type(metric_id: str, dimension: str, element: str) -> str:
    settings = get_settings()
    metric_rule = settings.root_cause_type_by_metric.get(metric_id)
    if metric_rule is not None:
        return metric_rule
    dimension_rule = settings.root_cause_type_by_dimension.get(dimension)
    if dimension_rule is not None:
        return dimension_rule
    element_rule = settings.root_cause_type_by_dimension_element.get(f"{dimension}:{element}")
    if element_rule is not None:
        return element_rule
    return RootCauseType.AOV_DROP.value

"""Pure deterministic attribution and decomposition logic for Phase 2."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from pydantic import Field

from metric_rca.business.policy_registry import (
    DEFAULT_POLICY_REGISTRY,
    MetricPolicyRegistry,
    PolicyRegistryError,
    root_cause_type_for_metric_dimension,
)
from metric_rca.domain.enums import EvidenceVerdict
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
    anomaly_direction: str | None = None,
    policy_registry: MetricPolicyRegistry = DEFAULT_POLICY_REGISTRY,
    top_threshold: float = 0.60,
) -> AttributionResult:
    if not evidence_ids:
        return AttributionResult(ok=False, error_code="EVIDENCE_MISSING")
    if not current_rows:
        return AttributionResult(ok=False, error_code="NO_CURRENT_DATA")
    if not baseline_rows:
        return AttributionResult(ok=False, error_code="INSUFFICIENT_BASELINE_DATA")

    current_by_element = _values_by_element(current_rows, dimension)
    baseline_by_element = _baseline_means_by_element(baseline_rows, dimension)
    movement_direction = _movement_direction(
        metric_definition=metric_definition,
        anomaly_direction=anomaly_direction,
    )
    bad_delta_by_element: dict[str, float] = {}
    severity_by_element: dict[str, float] = {}
    for element, baseline_value in baseline_by_element.items():
        current_value = current_by_element.get(element)
        if current_value is not None:
            if movement_direction == "decrease":
                bad_delta = max(0.0, baseline_value - current_value)
            else:
                bad_delta = max(0.0, current_value - baseline_value)
            bad_delta_by_element[element] = bad_delta
            severity_by_element[element] = min(1.0, bad_delta / baseline_value) if baseline_value else 0.0

    total_bad_delta = sum(bad_delta_by_element.values())
    if total_bad_delta <= 0:
        return AttributionResult(ok=False, error_code="ATTRIBUTION_COVERAGE_LOW")

    try:
        root_cause_type = _root_cause_type(
            metric_definition.metric_id,
            dimension,
            registry=policy_registry,
        )
    except PolicyRegistryError as exc:
        return AttributionResult(ok=False, error_code=exc.code)

    raw_candidates: list[RootCauseCandidate] = []
    for element, bad_delta in bad_delta_by_element.items():
        if bad_delta <= 0:
            pass
        else:
            contribution_pct = bad_delta / total_bad_delta
            signal_severity = severity_by_element.get(element, min(1.0, contribution_pct))
            evidence_support = 1.0 if evidence_ids else 0.0
            score = contribution_pct * signal_severity * evidence_support
            raw_candidates.append(
                RootCauseCandidate(
                    root_cause_type=root_cause_type,
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
    if not ranked:
        return AttributionResult(ok=False, error_code="ATTRIBUTION_COVERAGE_LOW")
    return AttributionResult(ok=True, candidates=ranked, coverage=ranked[0].contribution_pct)


def _movement_direction(*, metric_definition: MetricDefinition, anomaly_direction: str | None) -> str:
    if anomaly_direction in {"increase", "decrease"}:
        return anomaly_direction
    return "decrease" if metric_definition.higher_is_better else "increase"


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
    raw_scores = [_candidate_score(candidate) for candidate in candidates]
    max_score = max(raw_scores)
    normalized: list[RootCauseCandidate] = []
    for candidate, raw_score in zip(candidates, raw_scores, strict=True):
        confidence = raw_score / max_score if max_score > 0 else 0.0
        normalized.append(candidate.model_copy(update={"eng_confidence": confidence}))
    return sorted(
        normalized,
        key=lambda item: (
            item.eng_confidence,
            item.surprise_js if item.surprise_js is not None else -1.0,
        ),
        reverse=True,
    )


def _candidate_score(candidate: RootCauseCandidate) -> float:
    if candidate.explanatory_power is None:
        return candidate.eng_confidence
    return (
        candidate.explanatory_power
        * candidate.signal_severity
        * candidate.evidence_support
        * candidate.reflection_factor
    )


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


def _root_cause_type(metric_id: str, dimension: str, *, registry: MetricPolicyRegistry) -> str:
    return root_cause_type_for_metric_dimension(metric_id=metric_id, dimension=dimension, registry=registry)

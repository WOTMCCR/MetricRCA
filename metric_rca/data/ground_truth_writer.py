"""Build private, evidence-bearing ground truth from declarative scenarios."""

from __future__ import annotations

from typing import Any, Mapping

from metric_rca.data.metric_deriver import MetricComparison
from metric_rca.data.scenario_spec import ScenarioSpec


def build_ground_truth(
    *,
    scenario: ScenarioSpec,
    seed: int,
    profile: str,
    comparison: MetricComparison,
    shock_match_counts: Mapping[str, int],
    case_id: str,
) -> dict[str, Any]:
    if not case_id.startswith("GC") or not case_id[2:].isdigit():
        raise ValueError(f"GROUND_TRUTH_CASE_ID_INVALID: {case_id}")
    return {
        "schema_version": "metricrca-generated-ground-truth-v1",
        "case_id": case_id,
        "scenario_id": scenario.scenario_id,
        "business_date": scenario.target_date.isoformat(),
        "metric_id": scenario.metric_id,
        "expected_anomaly": scenario.expected_anomaly,
        "anomaly_direction": scenario.anomaly_direction,
        "root_causes": [
            {
                "root_cause_type": cause.root_cause_type,
                "dimension": cause.dimension,
                "element": cause.element,
                "weight": cause.weight,
                "expected_evidence_chain": list(cause.expected_evidence_chain),
            }
            for cause in scenario.expected_causes
        ],
        "negative_controls": [
            {
                "name": control.name,
                "selector": {key: list(values) for key, values in control.selector.items()},
                "metric_id": control.metric_id,
                "max_abs_delta_ratio": control.max_abs_delta_ratio,
            }
            for control in scenario.negative_controls
        ],
        "baseline_comparison": comparison.as_dict(),
        "shock_manifest": [
            {
                "shock_id": shock.shock_id,
                "shock_type": shock.shock_type.value,
                "selector": {key: list(values) for key, values in shock.selector.items()},
                "operation_count": len(shock.operations),
                "matched_row_operations": int(shock_match_counts.get(shock.shock_id, 0)),
            }
            for shock in scenario.shocks
        ],
        "seed": seed,
        "profile": profile,
        "tags": list(scenario.tags),
    }

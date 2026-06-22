from __future__ import annotations

from pathlib import Path

import pytest

from metric_rca.data.baseline_generator import BaselineConfig, BaselineGenerator
from metric_rca.data.dimension_catalog import DimensionCatalog
from metric_rca.data.scenario_spec import OperationType, ScenarioSet
from metric_rca.data.shock_composer import ShockCompositionError, compose_scenario


DATA_DIR = Path(__file__).resolve().parents[1] / "metric_rca" / "data" / "scenarios"


def _loaded():
    catalog = DimensionCatalog.load(DATA_DIR / "catalog.yaml")
    scenario_set = ScenarioSet.load(DATA_DIR / "phase_c_full.yaml", catalog=catalog)
    generator = BaselineGenerator(
        catalog=catalog,
        config=BaselineConfig.from_scenarios(seed=20260606, scenarios=scenario_set.scenarios),
    )
    return scenario_set, generator.generate()


def test_shock_composer_applies_declared_selector_without_case_branches() -> None:
    scenario_set, baseline = _loaded()
    scenario = next(row for row in scenario_set.scenarios if row.scenario_id == "DG01_paid_ads_traffic_drop")
    baseline_by_id = {str(row["row_id"]): row for row in baseline}

    composed, match_counts = compose_scenario(baseline_rows=baseline, scenario=scenario)
    paid_target = [
        row
        for row in composed
        if row["business_date"] == scenario.target_date.isoformat() and row["channel"] == "paid_ads"
    ]
    organic_target = [
        row
        for row in composed
        if row["business_date"] == scenario.target_date.isoformat() and row["channel"] == "organic"
    ]

    assert match_counts == {"paid-traffic": len(paid_target) * 3}
    assert paid_target
    assert all(float(row["uv"]) < float(baseline_by_id[str(row["row_id"])]["uv"]) for row in paid_target)
    assert all(float(row["uv"]) == float(baseline_by_id[str(row["row_id"])]["uv"]) for row in organic_target)


def test_target_scope_does_not_modify_baseline_dates() -> None:
    scenario_set, baseline = _loaded()
    scenario = next(row for row in scenario_set.scenarios if row.scenario_id == "DG01_paid_ads_traffic_drop")
    composed, _ = compose_scenario(baseline_rows=baseline, scenario=scenario)
    baseline_by_id = {str(row["row_id"]): row for row in baseline}
    target = scenario.target_date.isoformat()

    assert all(
        row.get("_applied_shocks", []) == []
        and float(row["uv"]) == float(baseline_by_id[str(row["row_id"])]["uv"])
        for row in composed
        if row["business_date"] != target
    )


def test_shock_composer_rejects_negative_results_without_clamping() -> None:
    scenario_set, baseline = _loaded()
    scenario = next(row for row in scenario_set.scenarios if row.scenario_id == "DG01_paid_ads_traffic_drop")
    first_shock = scenario.shocks[0]
    operation = first_shock.operations[0]
    invalid_operation = operation.__class__(
        field=operation.field,
        operation=OperationType.ADD,
        value=-1_000_000.0,
        day_offset=operation.day_offset,
        scope=operation.scope,
    )
    invalid_shock = first_shock.__class__(
        shock_id=first_shock.shock_id,
        shock_type=first_shock.shock_type,
        selector=first_shock.selector,
        operations=(invalid_operation,),
    )
    invalid_scenario = scenario.__class__(
        scenario_id=scenario.scenario_id,
        question=scenario.question,
        target_date=scenario.target_date,
        metric_id=scenario.metric_id,
        anomaly_direction=scenario.anomaly_direction,
        expected_anomaly=scenario.expected_anomaly,
        tags=scenario.tags,
        shocks=(invalid_shock,),
        expected_causes=scenario.expected_causes,
        negative_controls=scenario.negative_controls,
        baseline_comparison=scenario.baseline_comparison,
    )

    with pytest.raises(ShockCompositionError) as exc_info:
        compose_scenario(baseline_rows=baseline, scenario=invalid_scenario)

    assert exc_info.value.code == "SHOCK_RESULT_INVALID"

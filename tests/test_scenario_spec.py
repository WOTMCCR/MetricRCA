from __future__ import annotations

import json
from pathlib import Path

import pytest

from metric_rca.data.dimension_catalog import DimensionCatalog, REQUIRED_DIMENSIONS
from metric_rca.data.scenario_spec import ScenarioSet, ScenarioSpecError, ShockType


DATA_DIR = Path(__file__).resolve().parents[1] / "metric_rca" / "data" / "scenarios"


def test_phase_c_scenario_set_covers_required_dimensions_and_shocks() -> None:
    catalog = DimensionCatalog.load(DATA_DIR / "catalog.yaml")
    scenario_set = ScenarioSet.load(DATA_DIR / "phase_c_full.yaml", catalog=catalog)

    assert set(REQUIRED_DIMENSIONS).issubset(catalog.dimensions)
    assert len(scenario_set.scenarios) == len(ShockType)
    assert {
        shock.shock_type
        for scenario in scenario_set.scenarios
        for shock in scenario.shocks
    } == set(ShockType)
    assert all(scenario.negative_controls for scenario in scenario_set.scenarios)
    assert all(scenario.baseline_comparison.offset_days == (-7, -14, -21, -28) for scenario in scenario_set.scenarios)


def test_scenario_loader_rejects_invalid_root_cause_weights(tmp_path: Path) -> None:
    catalog = DimensionCatalog.load(DATA_DIR / "catalog.yaml")
    payload = json.loads((DATA_DIR / "phase_c_full.yaml").read_text(encoding="utf-8"))
    residual = next(row for row in payload["scenarios"] if row["scenario_id"] == "DG21_residual_dual_mechanism")
    residual["expected_causes"][0]["weight"] = 0.9
    invalid_path = tmp_path / "invalid.yaml"
    invalid_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ScenarioSpecError) as exc_info:
        ScenarioSet.load(invalid_path, catalog=catalog)

    assert exc_info.value.code == "CAUSE_WEIGHT_INVALID"


def test_scenario_loader_rejects_coerced_expected_anomaly(tmp_path: Path) -> None:
    catalog = DimensionCatalog.load(DATA_DIR / "catalog.yaml")
    payload = json.loads((DATA_DIR / "phase_c_full.yaml").read_text(encoding="utf-8"))
    payload["scenarios"][0]["expected_anomaly"] = "false"
    invalid_path = tmp_path / "invalid.yaml"
    invalid_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ScenarioSpecError) as exc_info:
        ScenarioSet.load(invalid_path, catalog=catalog)

    assert exc_info.value.code == "SCENARIO_FIELD_INVALID"


def test_scenario_loader_rejects_missing_required_numeric_fields(tmp_path: Path) -> None:
    catalog = DimensionCatalog.load(DATA_DIR / "catalog.yaml")
    payload = json.loads((DATA_DIR / "phase_c_full.yaml").read_text(encoding="utf-8"))
    del payload["scenarios"][0]["shocks"][0]["operations"][0]["day_offset"]
    invalid_path = tmp_path / "invalid.yaml"
    invalid_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ScenarioSpecError) as exc_info:
        ScenarioSet.load(invalid_path, catalog=catalog)

    assert exc_info.value.code == "SCENARIO_FIELD_REQUIRED"

from __future__ import annotations

from pathlib import Path

from metric_rca.data.baseline_generator import BaselineConfig, BaselineGenerator
from metric_rca.data.data_quality_validator import assert_reproducible, validate_generated_dataset
from metric_rca.data.dimension_catalog import DimensionCatalog
from metric_rca.data.eval_case_manifest import build_public_cases
from metric_rca.data.ground_truth_writer import build_ground_truth
from metric_rca.data.metric_deriver import compare_to_baseline, derive_rows
from metric_rca.data.scenario_spec import ScenarioSet
from metric_rca.data.shock_composer import compose_scenario


DATA_DIR = Path(__file__).resolve().parents[1] / "metric_rca" / "data" / "scenarios"


def test_full_generated_dataset_passes_quality_contract() -> None:
    catalog = DimensionCatalog.load(DATA_DIR / "catalog.yaml")
    scenario_set = ScenarioSet.load(DATA_DIR / "phase_c_full.yaml", catalog=catalog)
    config = BaselineConfig.from_scenarios(seed=20260606, scenarios=scenario_set.scenarios)
    first = BaselineGenerator(catalog=catalog, config=config).generate()
    second = BaselineGenerator(catalog=catalog, config=config).generate()
    assert_reproducible(first, second)

    rows_by_scenario = {}
    truth = []
    public_cases = build_public_cases(scenario_set.scenarios)
    case_id_by_scenario = {
        scenario.scenario_id: str(public_cases[index]["case_id"])
        for index, scenario in enumerate(scenario_set.scenarios)
    }
    for scenario in scenario_set.scenarios:
        composed, counts = compose_scenario(baseline_rows=first, scenario=scenario)
        derived = derive_rows(composed)
        rows_by_scenario[scenario.scenario_id] = derived
        comparison = compare_to_baseline(
            derived,
            metric_id=scenario.metric_id,
            target_date=scenario.target_date,
            baseline=scenario.baseline_comparison,
        )
        truth.append(
            build_ground_truth(
                scenario=scenario,
                seed=20260606,
                profile="scenario",
                comparison=comparison,
                shock_match_counts=counts,
                case_id=case_id_by_scenario[scenario.scenario_id],
            )
        )

    report = validate_generated_dataset(
        catalog=catalog,
        scenario_set=scenario_set,
        scenario_rows=rows_by_scenario,
        ground_truth=truth,
        public_cases=public_cases,
    )

    assert report.valid is True
    assert report.issue_count == 0
    assert report.scenario_count == 21
    assert len(report.covered_shocks) == 21


def test_quality_validator_rejects_public_private_case_id_drift() -> None:
    catalog = DimensionCatalog.load(DATA_DIR / "catalog.yaml")
    scenario_set = ScenarioSet.load(DATA_DIR / "phase_c_full.yaml", catalog=catalog)
    public_cases = build_public_cases(scenario_set.scenarios)
    public_ids = [str(row["case_id"]) for row in public_cases]
    truth = [
        {"case_id": public_ids[index], "scenario_id": scenario.scenario_id}
        for index, scenario in enumerate(scenario_set.scenarios)
    ]
    truth[-1]["case_id"] = "DG21_residual_dual_mechanism"

    report = validate_generated_dataset(
        catalog=catalog,
        scenario_set=scenario_set,
        scenario_rows={},
        ground_truth=truth,
        public_cases=public_cases,
    )

    assert report.valid is False
    assert any(issue.code == "EVAL_CASE_PRIVATE_PUBLIC_MISMATCH" for issue in report.issues)

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metric_rca.data.eval_case_manifest import validate_public_cases
from metric_rca.data.scenario_seed import compile_scenario_dataset, main as scenario_seed_main


DATA_DIR = Path(__file__).resolve().parents[1] / "metric_rca" / "data" / "scenarios"
FORBIDDEN_PUBLIC_TERMS = (
    "budget",
    "stockout",
    "inventory",
    "price_increase",
    "price increase",
    "promotion_end",
    "promotion end",
    "delivery",
    "simultaneous",
    "multi_cause",
    "residual",
    "aov",
    "campaign_budget_cut",
)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_scenario_seed_writes_separated_reproducible_eval_artifacts(tmp_path: Path) -> None:
    output_dir = compile_scenario_dataset(
        catalog_path=DATA_DIR / "catalog.yaml",
        scenario_path=DATA_DIR / "phase_c_full.yaml",
        output_root=tmp_path,
        seed=20260606,
        profile="scenario",
    )
    public_cases = _read_jsonl(output_dir / "eval_cases_public.jsonl")
    truth = _read_jsonl(output_dir / "eval_cases_private_ground_truth.jsonl")
    manifest = json.loads((output_dir / "eval_case_manifest.json").read_text(encoding="utf-8"))
    quality = json.loads((output_dir / "data_quality_report.json").read_text(encoding="utf-8"))

    validate_public_cases(public_cases)
    for row in public_cases:
        serialized = json.dumps(row, ensure_ascii=False, sort_keys=True).lower()
        assert not any(term in serialized for term in FORBIDDEN_PUBLIC_TERMS)
    assert manifest["ground_truth_separated"] is True
    assert manifest["case_count"] == 21
    assert quality["valid"] is True
    assert {row["case_id"] for row in public_cases} == {row["case_id"] for row in truth}
    assert all(str(row["case_id"]).startswith("GC") for row in public_cases)

    for row in truth:
        assert row["case_id"] != row["scenario_id"]
        assert row["negative_controls"]
        assert row["baseline_comparison"]["baseline_dates"]
        if row["expected_anomaly"]:
            assert abs(sum(float(cause["weight"]) for cause in row["root_causes"]) - 1.0) < 1e-9
            assert all(cause["expected_evidence_chain"][0] == "E1" for cause in row["root_causes"])
            assert all(cause["expected_evidence_chain"][-1] == "E_rank" for cause in row["root_causes"])
        else:
            assert row["root_causes"] == []


def test_scenario_seed_cli_preserves_typed_compile_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = json.loads((DATA_DIR / "phase_c_full.yaml").read_text(encoding="utf-8"))
    payload["scenarios"][0]["expected_anomaly"] = "false"
    scenario_path = tmp_path / "invalid.yaml"
    scenario_path.write_text(json.dumps(payload), encoding="utf-8")

    result = scenario_seed_main(
        [
            "--catalog",
            str(DATA_DIR / "catalog.yaml"),
            "--scenario-set",
            str(scenario_path),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert result == 2
    assert error["error_code"] == "SCENARIO_FIELD_INVALID"
    assert error["context"]["field"] == "expected_anomaly"


def test_scenario_seed_cli_rejects_invalid_env_seed_with_typed_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("METRIC_RCA_DATA_SEED", "not-an-int")

    result = scenario_seed_main(
        [
            "--catalog",
            str(DATA_DIR / "catalog.yaml"),
            "--scenario-set",
            str(DATA_DIR / "phase_c_full.yaml"),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert result == 2
    assert error["error_code"] == "SCENARIO_SEED_INVALID"

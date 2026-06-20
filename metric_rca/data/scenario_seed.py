"""CLI that compiles declarative scenarios into reproducible warehouse artifacts."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

from metric_rca.data.baseline_generator import BaselineConfig, BaselineGenerator
from metric_rca.data.data_quality_validator import assert_reproducible, validate_generated_dataset
from metric_rca.data.dimension_catalog import DimensionCatalog
from metric_rca.data.eval_case_manifest import build_case_manifest, build_public_cases
from metric_rca.data.ground_truth_writer import build_ground_truth
from metric_rca.data.metric_deriver import compare_to_baseline, derive_rows
from metric_rca.data.scenario_spec import ScenarioSet
from metric_rca.data.shock_composer import compose_scenario
from metric_rca.data.warehouse_writer import (
    build_file_manifest,
    write_json_atomic,
    write_jsonl_atomic,
)


class ScenarioSeedError(RuntimeError):
    def __init__(self, code: str, message: str, *, context: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.context = dict(context or {})
        super().__init__(f"{code}: {message}")

    def as_dict(self) -> dict[str, Any]:
        return {"error_code": self.code, "message": self.message, "context": self.context}


def compile_scenario_dataset(
    *,
    catalog_path: Path,
    scenario_path: Path,
    output_root: Path,
    seed: int,
    profile: str,
) -> Path:
    if not profile.strip():
        raise ScenarioSeedError("SCENARIO_PROFILE_INVALID", "profile must not be empty")
    catalog = DimensionCatalog.load(catalog_path)
    scenario_set = ScenarioSet.load(scenario_path, catalog=catalog)
    config = BaselineConfig.from_scenarios(seed=seed, scenarios=scenario_set.scenarios)
    baseline_generator = BaselineGenerator(catalog=catalog, config=config)
    baseline_raw = baseline_generator.generate()
    reproducibility_probe = BaselineGenerator(catalog=catalog, config=config).generate()
    assert_reproducible(baseline_raw, reproducibility_probe)
    baseline_rows = derive_rows(baseline_raw)
    public_cases = build_public_cases(scenario_set.scenarios)
    public_case_by_scenario = {
        scenario.scenario_id: str(public_cases[index]["case_id"])
        for index, scenario in enumerate(scenario_set.scenarios)
    }

    output_dir = output_root / profile / scenario_set.scenario_set_id
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl_atomic(output_dir / "baseline.jsonl", baseline_rows)
    write_json_atomic(
        output_dir / "catalog_snapshot.json",
        json.loads(catalog_path.read_text(encoding="utf-8")),
    )
    write_json_atomic(
        output_dir / "scenario_spec_snapshot.json",
        json.loads(scenario_path.read_text(encoding="utf-8")),
    )

    scenario_rows: dict[str, list[dict[str, Any]]] = {}
    ground_truth: list[dict[str, Any]] = []
    scenario_manifest_rows: list[dict[str, Any]] = []
    for scenario in scenario_set.scenarios:
        composed, match_counts = compose_scenario(baseline_rows=baseline_raw, scenario=scenario)
        derived = derive_rows(composed)
        scenario_rows[scenario.scenario_id] = derived
        comparison = compare_to_baseline(
            derived,
            metric_id=scenario.metric_id,
            target_date=scenario.target_date,
            baseline=scenario.baseline_comparison,
        )
        truth = build_ground_truth(
            scenario=scenario,
            seed=seed,
            profile=profile,
            comparison=comparison,
            shock_match_counts=match_counts,
            case_id=public_case_by_scenario[scenario.scenario_id],
        )
        ground_truth.append(truth)
        scenario_dir = output_dir / "scenarios" / scenario.scenario_id
        write_jsonl_atomic(scenario_dir / "observations.jsonl", derived)
        write_json_atomic(scenario_dir / "ground_truth.json", truth)
        scenario_manifest_rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "row_count": len(derived),
                "relevant_dates": [value.isoformat() for value in scenario.relevant_dates()],
                "shock_match_counts": match_counts,
                "comparison": comparison.as_dict(),
            }
        )

    write_jsonl_atomic(output_dir / "eval_cases_public.jsonl", public_cases)
    write_jsonl_atomic(output_dir / "eval_cases_private_ground_truth.jsonl", ground_truth)
    case_manifest = build_case_manifest(
        scenario_set_id=scenario_set.scenario_set_id,
        public_cases=public_cases,
        private_ground_truth=ground_truth,
    )
    write_json_atomic(output_dir / "eval_case_manifest.json", case_manifest)

    quality = validate_generated_dataset(
        catalog=catalog,
        scenario_set=scenario_set,
        scenario_rows=scenario_rows,
        ground_truth=ground_truth,
        public_cases=public_cases,
    )
    write_json_atomic(output_dir / "data_quality_report.json", quality.as_dict())
    if not quality.valid:
        raise ScenarioSeedError(
            "SCENARIO_DATA_QUALITY_FAILED",
            "generated scenario data failed quality validation",
            context={"issue_count": quality.issue_count, "issues": quality.as_dict()["issues"][:20]},
        )

    manifest = {
        "schema_version": "metricrca-generated-scenario-manifest-v1",
        "scenario_set_id": scenario_set.scenario_set_id,
        "profile": profile,
        "seed": seed,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "baseline_start_date": config.start_date.isoformat(),
        "baseline_end_date": config.end_date.isoformat(),
        "baseline_row_count": len(baseline_rows),
        "scenario_count": len(scenario_set.scenarios),
        "scenarios": scenario_manifest_rows,
        "case_manifest": case_manifest,
        "data_quality": quality.as_dict(),
        "files": build_file_manifest(output_dir, exclude={"manifest.json"}),
    }
    write_json_atomic(output_dir / "manifest.json", manifest)
    return output_dir


def _parser() -> argparse.ArgumentParser:
    default_dir = Path(__file__).resolve().parent / "scenarios"
    parser = argparse.ArgumentParser(description="Compile deterministic MetricRCA business scenarios")
    parser.add_argument("--catalog", type=Path, default=default_dir / "catalog.yaml")
    parser.add_argument("--scenario-set", type=Path, default=default_dir / "phase_c_full.yaml")
    parser.add_argument("--output-dir", type=Path, default=Path("eval_out/generated_data"))
    parser.add_argument("--seed", default=os.environ.get("METRIC_RCA_DATA_SEED", "20260606"))
    parser.add_argument("--profile", default=os.environ.get("METRIC_RCA_SEED_PROFILE", "scenario"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output_dir = compile_scenario_dataset(
            catalog_path=args.catalog,
            scenario_path=args.scenario_set,
            output_root=args.output_dir,
            seed=_parse_seed(args.seed),
            profile=args.profile,
        )
    except (ScenarioSeedError, ValueError) as exc:
        payload = _error_payload(exc)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"output_dir": str(output_dir)}, ensure_ascii=False, sort_keys=True))
    return 0


def _error_payload(exc: ScenarioSeedError | ValueError) -> dict[str, Any]:
    if isinstance(exc, ScenarioSeedError):
        return exc.as_dict()
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return {
            "error_code": code,
            "message": str(exc),
            "context": dict(getattr(exc, "context", {}) or {}),
        }
    return {
        "error_code": "SCENARIO_COMPILE_INVALID",
        "message": str(exc),
        "context": {},
    }


def _parse_seed(value: object) -> int:
    try:
        return int(str(value))
    except ValueError as exc:
        raise ScenarioSeedError(
            "SCENARIO_SEED_INVALID",
            "seed must be an integer",
            context={"value": str(value)},
        ) from exc


if __name__ == "__main__":
    raise SystemExit(main())

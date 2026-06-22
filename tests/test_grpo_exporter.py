from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from metric_rca.evals.grpo_exporter import export_cycle
from metric_rca.evals.grpo_exporter import GrpoExportError
from metric_rca.evals.ptv_artifacts import read_json, read_jsonl, write_json_atomic, write_jsonl_atomic


def test_exporter_writes_three_layers_links_diff_and_excludes_overfit_positive(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "MetricRCA Test")
    target_file = repo / "metric_rca" / "runtime" / "plan_compiler.py"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    first_commit = _git(repo, "rev-parse", "HEAD").strip()
    target_file.write_text("VALUE = 2\n", encoding="utf-8")
    test_file = repo / "tests" / "test_runtime_plan.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_value(): assert True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fix residual discovery")
    second_commit = _git(repo, "rev-parse", "HEAD").strip()

    cycle_dir = tmp_path / "cycle-20260620-1200"
    write_json_atomic(
        cycle_dir / "meta.json",
        {"cycle_id": "cycle-20260620-1200", "base_commit": first_commit},
    )
    _write_round(cycle_dir / "round-01", round_number=1, commit=first_commit, passed=False, divergence="overfit")
    _write_round(cycle_dir / "round-02", round_number=2, commit=second_commit, passed=True, divergence="correct")

    paths = export_cycle(cycle_dir=cycle_dir, repo_root=repo)
    layer1 = read_jsonl(paths["layer1_controller"])
    layer2 = read_jsonl(paths["layer2_sub_agent"])
    layer3 = read_jsonl(paths["layer3_coding_fix"])
    positives = read_jsonl(paths["positive_records"])
    manifest = read_json(paths["manifest"])

    assert len(layer1) == 2
    assert len(layer3) == 1
    assert "diff --git" in layer3[0]["trajectory"]["git_diff"]
    assert layer3[0]["reward"]["fix_effective"] is True
    assert manifest["overfit_positive_count"] == 0
    overfit_ids = {
        row["trajectory_id"]
        for row in layer2
        if row["metadata"].get("prediction_divergence") == "overfit"
    }
    positive_ids = {row["trajectory_id"] for row in positives}
    assert overfit_ids.isdisjoint(positive_ids)


def test_exporter_rejects_task_trajectory_missing_required_artifact_fields(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "MetricRCA Test")
    target_file = repo / "metric_rca" / "runtime" / "plan_compiler.py"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    commit = _git(repo, "rev-parse", "HEAD").strip()

    cycle_dir = tmp_path / "cycle-20260620-1200"
    write_json_atomic(cycle_dir / "meta.json", {"cycle_id": "cycle-20260620-1200", "base_commit": commit})
    round_dir = cycle_dir / "round-01"
    _write_round(round_dir, round_number=1, commit=commit, passed=True, divergence="correct")
    task_path = round_dir / "eval-1" / "grpo_dataset" / "trajectories.jsonl"
    write_jsonl_atomic(task_path, [{"eval_id": "eval-1", "phase": "baseline"}])

    with pytest.raises(GrpoExportError) as exc_info:
        export_cycle(cycle_dir=cycle_dir, repo_root=repo)

    assert exc_info.value.code == "GRPO_TASK_TRAJECTORY_INVALID"


def test_exporter_requires_task_trajectory_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "MetricRCA Test")
    target_file = repo / "metric_rca" / "runtime" / "plan_compiler.py"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    commit = _git(repo, "rev-parse", "HEAD").strip()

    cycle_dir = tmp_path / "cycle-20260620-1200"
    write_json_atomic(cycle_dir / "meta.json", {"cycle_id": "cycle-20260620-1200", "base_commit": commit})
    round_dir = cycle_dir / "round-01"
    _write_round(round_dir, round_number=1, commit=commit, passed=True, divergence="correct")
    (round_dir / "eval-1" / "grpo_dataset" / "manifest.json").unlink()

    with pytest.raises(GrpoExportError) as exc_info:
        export_cycle(cycle_dir=cycle_dir, repo_root=repo)

    assert exc_info.value.code == "GRPO_TASK_TRAJECTORY_MANIFEST_INVALID"


def test_exporter_rejects_fix_round_without_actionable_prior_diagnosis(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "MetricRCA Test")
    target_file = repo / "metric_rca" / "runtime" / "plan_compiler.py"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    first_commit = _git(repo, "rev-parse", "HEAD").strip()
    target_file.write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fix")
    second_commit = _git(repo, "rev-parse", "HEAD").strip()

    cycle_dir = tmp_path / "cycle-20260620-1200"
    write_json_atomic(cycle_dir / "meta.json", {"cycle_id": "cycle-20260620-1200", "base_commit": first_commit})
    _write_round(cycle_dir / "round-01", round_number=1, commit=first_commit, passed=False, divergence="design_flaw")
    write_jsonl_atomic(cycle_dir / "round-01" / "diagnosis.jsonl", [])
    _write_round(cycle_dir / "round-02", round_number=2, commit=second_commit, passed=True, divergence="correct")

    with pytest.raises(GrpoExportError) as exc_info:
        export_cycle(cycle_dir=cycle_dir, repo_root=repo)

    assert exc_info.value.code == "GRPO_FIX_DIAGNOSIS_MISSING"


def test_exporter_rejects_malformed_prediction_rows(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "MetricRCA Test")
    target_file = repo / "metric_rca" / "runtime" / "plan_compiler.py"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    commit = _git(repo, "rev-parse", "HEAD").strip()

    cycle_dir = tmp_path / "cycle-20260620-1200"
    write_json_atomic(cycle_dir / "meta.json", {"cycle_id": "cycle-20260620-1200", "base_commit": commit})
    round_dir = cycle_dir / "round-01"
    _write_round(round_dir, round_number=1, commit=commit, passed=True, divergence="correct")
    write_jsonl_atomic(round_dir / "predictions.jsonl", [{"aspect": "outcome", "reasoning": "", "prediction": {}}])

    with pytest.raises(GrpoExportError) as exc_info:
        export_cycle(cycle_dir=cycle_dir, repo_root=repo)

    assert exc_info.value.code == "GRPO_PREDICTION_INVALID"


def _write_round(round_dir: Path, *, round_number: int, commit: str, passed: bool, divergence: str) -> None:
    round_dir.mkdir(parents=True)
    eval_id = f"eval-{round_number}"
    case = {
        "case_id": "RS01",
        "intent_ok": 1,
        "anomaly_ok": 1,
        "top1_ok": 1 if passed else 0,
        "top3_ok": 1 if passed else 0,
        "sql_safe": 1,
        "reflection_repair_ok": 1,
        "report_traceable_ok": 1,
        "root_cause_set_recall": 1.0 if passed else 0.5,
        "root_cause_set_precision": 1.0 if passed else 0.5,
        "weighted_explanation_coverage": 1.0 if passed else 0.42,
        "top3_contains_all_major_causes": 1 if passed else 0,
        "evidence_coverage": 1.0,
        "memory_pollution_ok": 1,
    }
    metrics = {
        "eval_suite": "regression",
        "case_total": 1,
        "completed_case_total": 1,
        "complete": True,
        "thresholds_met": passed,
        "per_family_gate": passed,
        "top1_rate": 1.0 if passed else 0.0,
        "top3_rate": 1.0 if passed else 0.0,
        "root_cause_set_recall_avg": case["root_cause_set_recall"],
        "root_cause_set_precision_avg": case["root_cause_set_precision"],
        "weighted_explanation_coverage_avg": case["weighted_explanation_coverage"],
    }
    write_json_atomic(round_dir / "eval-result.json", {"eval_id": eval_id, "summary": metrics, "cases": [case]})
    write_json_atomic(
        round_dir / "summary.json",
        {
            "round": round_number,
            "metricrca_gates_passed": passed,
            "metrics_after": metrics,
        },
    )
    write_json_atomic(
        round_dir / "optimization_summary.json",
        {
            "selected_fix_category": "FIX-D",
            "selected_layer": "policy/pipeline",
            "controller_justification": "metric_rca/runtime/plan_compiler.py lacks residual lanes",
            "gap_summary": {},
            "remaining_gaps": [] if passed else [{"case_id": "RS01", "category": "FIX-D"}],
            "controller_rules_applied": {
                "rule_c1_blocked_categories": [],
                "rule_c2_promoted": None,
                "rule_c3_discovery_priority": True,
                "rule_c4_revert_assessment": {"triggered": False},
                "rule_c5_streak_counts": {"FIX-D": round_number},
            },
        },
    )
    write_json_atomic(
        round_dir / "commit_lineage.json",
        {"eval_code_commit": commit, "fix_commit": commit, "post_eval_review_fix_commit": None},
    )
    prediction = {
        "case_id": "RS01",
        "aspect": "outcome",
        "prediction": {"root_cause_type": "campaign_traffic_drop", "top1_ok": True},
        "reasoning": "metric_rca/runtime/plan_compiler.py:212 controls residual discovery",
        "confidence": 0.8,
        "risks": ["secondary AOV lane may be absent"],
    }
    write_jsonl_atomic(round_dir / "predictions.jsonl", [prediction])
    write_json_atomic(
        round_dir / "gap_report.json",
        {
            "eval_id": eval_id,
            "gaps": [
                {
                    "case_id": "RS01",
                    "aspect": "outcome",
                    "divergence": divergence,
                    "actual": {"top3_ok": passed},
                    "detail": "residual discovery result",
                }
            ],
            "summary": {"total": 1},
        },
    )
    diagnosis = [
        {
            "case_id": "RS01",
            "aspect": "outcome",
            "divergence": "correct" if passed else "design_flaw",
            "diagnosis": "residual_decomposition",
            "fix_category": "NO-FIX" if passed else "FIX-D",
            "proposed_fix": {
                "files": [
                    "metric_rca/runtime/plan_compiler.py",
                    "tests/test_runtime_plan.py",
                ]
            },
        }
    ]
    write_jsonl_atomic(round_dir / "diagnosis.jsonl", diagnosis)
    task_dir = round_dir / eval_id / "grpo_dataset"
    task_dir.mkdir(parents=True)
    write_jsonl_atomic(
        task_dir / "trajectories.jsonl",
        [
            {
                "schema_version": "metric-rca-grpo-v1",
                "dataset_kind": "metric_rca_eval_trajectory",
                "eval_id": eval_id,
                "eval_suite": "regression",
                "phase": "baseline",
                "case": {"case_id": "RS01", "question": "Why?", "tags": ["residual"]},
                "predictions": [],
                "ground_truth": {"case_id": "RS01"},
                "trajectory": {"run_id": f"run-{round_number}", "trace_steps": []},
                "final_answer": {"selected_candidate": {}},
                "judge": {"reward": 1.0 if passed else 0.0},
                "diagnostics": case,
                "detail": {},
            }
        ],
    )
    write_json_atomic(
        task_dir / "manifest.json",
        {
            "schema_version": "metric-rca-grpo-v1",
            "dataset_kind": "metric_rca_eval_trajectory",
            "eval_id": eval_id,
            "eval_suite": "regression",
            "created_at": "2026-06-20T00:00:00+00:00",
            "record_count": 1,
            "phases": {"baseline": 1},
            "reward_rate": 1.0 if passed else 0.0,
        },
    )


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(("git", *args), cwd=repo, text=True, capture_output=True, check=True)
    return completed.stdout

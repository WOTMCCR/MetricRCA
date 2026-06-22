from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from metric_rca.evals.ptv_artifacts import (
    PtvLayout,
    canonicalize_eval_artifacts,
    create_cycle,
    create_round,
    validate_round_outputs_fresh,
    verify_artifact_manifest,
    write_artifact_manifest,
)
from metric_rca.evals.ptv_errors import PtvRuntimeError


def test_cycle_round_and_manifest_are_canonical(tmp_path: Path) -> None:
    layout = PtvLayout(tmp_path / "ptv")
    cycle_dir = create_cycle(
        layout=layout,
        cycle_id="cycle-20260620-1200",
        branch="codex/c-complex-causal",
        base_commit="a" * 40,
        total_cases=46,
        max_rounds=25,
    )
    round_dir = create_round(
        layout=layout,
        cycle_id="cycle-20260620-1200",
        round_number=22,
        eval_id="eval-r22",
        eval_code_commit="b" * 40,
        fix_commit="b" * 40,
        post_eval_review_fix_commit=None,
    )
    assert cycle_dir == tmp_path / "ptv" / "cycle-20260620-1200"
    assert round_dir.name == "round-22"
    (round_dir / "eval-r22.json").write_text(
        json.dumps(
            {
                "eval_id": "eval-r22",
                "summary": {"case_total": 1},
                "cases": [{"case_id": "case-a"}],
            }
        ),
        encoding="utf-8",
    )
    source_cases = round_dir / "eval-r22" / "cases"
    source_cases.mkdir(parents=True)
    (source_cases / "case-a.json").write_text(json.dumps({"case_id": "case-a"}), encoding="utf-8")
    paths = canonicalize_eval_artifacts(round_dir=round_dir, eval_id="eval-r22")
    assert paths["eval_result"] == round_dir / "eval-result.json"
    manifest_path = write_artifact_manifest(round_dir)
    assert manifest_path.exists()
    manifest = verify_artifact_manifest(round_dir)
    assert manifest["artifact_count"] >= 4


def test_canonicalize_eval_artifacts_requires_per_case_outputs(tmp_path: Path) -> None:
    round_dir = tmp_path / "round-01"
    round_dir.mkdir()
    (round_dir / "eval-r1.json").write_text(
        json.dumps(
            {
                "eval_id": "eval-r1",
                "summary": {"case_total": 1},
                "cases": [{"case_id": "case-a"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PtvRuntimeError) as exc_info:
        canonicalize_eval_artifacts(round_dir=round_dir, eval_id="eval-r1")

    assert exc_info.value.code == "PTV_EVAL_RESULT_INVALID"


def test_canonicalize_eval_artifacts_rejects_per_case_count_mismatch(tmp_path: Path) -> None:
    round_dir = tmp_path / "round-01"
    round_dir.mkdir()
    (round_dir / "eval-r1.json").write_text(
        json.dumps(
            {
                "eval_id": "eval-r1",
                "summary": {"case_total": 2},
                "cases": [{"case_id": "case-a"}, {"case_id": "case-b"}],
            }
        ),
        encoding="utf-8",
    )
    source_cases = round_dir / "eval-r1" / "cases"
    source_cases.mkdir(parents=True)
    (source_cases / "case-a.json").write_text(json.dumps({"case_id": "case-a"}), encoding="utf-8")

    with pytest.raises(PtvRuntimeError) as exc_info:
        canonicalize_eval_artifacts(round_dir=round_dir, eval_id="eval-r1")

    assert exc_info.value.code == "PTV_EVAL_RESULT_INVALID"


def test_round_output_freshness_rejects_stale_same_round_predictions(tmp_path: Path) -> None:
    round_dir = tmp_path / "round-01"
    round_dir.mkdir()
    predictions = round_dir / "predictions.jsonl"
    predictions.write_text("[]\n", encoding="utf-8")
    eval_result = round_dir / "eval-r1.json"
    eval_result.write_text(json.dumps({"eval_id": "eval-r1", "summary": {"case_total": 0}, "cases": []}), encoding="utf-8")
    old_time = 1_700_000_000
    os.utime(predictions, (old_time, old_time))
    os.utime(eval_result, (old_time, old_time))

    with pytest.raises(PtvRuntimeError) as exc_info:
        validate_round_outputs_fresh(
            round_dir=round_dir,
            eval_id="eval-r1",
            barrier={
                "status": "reached",
                "commands": {
                    "prediction": {"return_code": 0, "started_at": "2026-06-20T00:00:00Z"},
                    "eval": {"return_code": 0, "started_at": "2026-06-20T00:00:00Z"},
                },
            },
        )

    assert exc_info.value.code == "PTV_ARTIFACT_INVALID"


def test_create_round_rejects_reusing_executed_round_artifacts(tmp_path: Path) -> None:
    layout = PtvLayout(tmp_path / "ptv")
    create_round(
        layout=layout,
        cycle_id="cycle-20260620-1200",
        round_number=1,
        eval_id="eval-r1",
        eval_code_commit="a" * 40,
        fix_commit="a" * 40,
        post_eval_review_fix_commit=None,
    )
    round_dir = layout.round_dir("cycle-20260620-1200", 1)
    (round_dir / "barrier.json").write_text(json.dumps({"status": "reached"}), encoding="utf-8")
    (round_dir / "predictions.jsonl").write_text("", encoding="utf-8")

    with pytest.raises(PtvRuntimeError) as exc_info:
        create_round(
            layout=layout,
            cycle_id="cycle-20260620-1200",
            round_number=1,
            eval_id="eval-r1",
            eval_code_commit="a" * 40,
            fix_commit="a" * 40,
            post_eval_review_fix_commit=None,
        )

    assert exc_info.value.code == "PTV_ARTIFACT_INVALID"


def test_create_round_rejects_execution_artifacts_without_round_meta(tmp_path: Path) -> None:
    layout = PtvLayout(tmp_path / "ptv")
    round_dir = layout.round_dir("cycle-20260620-1200", 1)
    round_dir.mkdir(parents=True)
    (round_dir / "barrier.json").write_text(json.dumps({"status": "reached"}), encoding="utf-8")

    with pytest.raises(PtvRuntimeError) as exc_info:
        create_round(
            layout=layout,
            cycle_id="cycle-20260620-1200",
            round_number=1,
            eval_id="eval-r1",
            eval_code_commit="a" * 40,
            fix_commit="a" * 40,
            post_eval_review_fix_commit=None,
        )

    assert exc_info.value.code == "PTV_ARTIFACT_INVALID"


def test_commit_lineage_rejects_unevaluated_fix_commit(tmp_path: Path) -> None:
    layout = PtvLayout(tmp_path / "ptv")
    with pytest.raises(PtvRuntimeError) as exc_info:
        create_round(
            layout=layout,
            cycle_id="cycle-20260620-1200",
            round_number=1,
            eval_id="eval-r1",
            eval_code_commit="a" * 40,
            fix_commit="b" * 40,
            post_eval_review_fix_commit=None,
        )
    assert exc_info.value.code == "PTV_COMMIT_LINEAGE_INVALID"

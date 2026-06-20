from __future__ import annotations

import json
from pathlib import Path

import pytest

from metric_rca.evals.ptv_artifacts import write_json_atomic, write_jsonl_atomic
from metric_rca.evals.ptv_summary import build_round_summaries
from metric_rca.evals.ptv_errors import PtvRuntimeError


def test_two_green_confirmation_requires_same_commit_and_contract(tmp_path: Path) -> None:
    first = tmp_path / "round-01"
    second = tmp_path / "round-02"
    _write_round_inputs(first, round_number=1, commit="a" * 40)
    build_round_summaries(
        cycle_id="cycle-20260620-1200",
        round_number=1,
        round_dir=first,
        previous_round_dirs=[],
        selected_fix_category="FIX-D",
        selected_layer="policy",
        controller_justification="First green after a discovery fix.",
    )
    _write_round_inputs(second, round_number=2, commit="a" * 40)
    _, summary = build_round_summaries(
        cycle_id="cycle-20260620-1200",
        round_number=2,
        round_dir=second,
        previous_round_dirs=[first],
        selected_fix_category=None,
        selected_layer=None,
        controller_justification="Formal confirmation evaluates identical code.",
    )
    assert summary["formal_two_green_confirmed"] is True
    assert summary["formal_two_green_confirmation_pending"] is False


def test_regression_memory_field_is_not_classified_as_behavior_failure(tmp_path: Path) -> None:
    round_dir = tmp_path / "round-01"
    _write_round_inputs(round_dir, round_number=1, commit="a" * 40, memory_gate=False)
    optimization, _ = build_round_summaries(
        cycle_id="cycle-20260620-1200",
        round_number=1,
        round_dir=round_dir,
        previous_round_dirs=[],
        selected_fix_category="FIX-D",
        selected_layer="policy",
        controller_justification="Regression suite does not directly test memory treatment.",
    )
    assert optimization["memory_treatment"]["classification"] == "gate_not_applicable"
    assert optimization["memory_treatment"]["gate_evaluated"] is False


def test_round_summary_rejects_missing_gate_fields_instead_of_default_green(tmp_path: Path) -> None:
    round_dir = tmp_path / "round-01"
    _write_round_inputs(round_dir, round_number=1, commit="a" * 40)
    payload = json.loads((round_dir / "eval-result.json").read_text(encoding="utf-8"))
    del payload["summary"]["per_family_gate"]
    (round_dir / "eval-result.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PtvRuntimeError) as exc_info:
        build_round_summaries(
            cycle_id="cycle-20260620-1200",
            round_number=1,
            round_dir=round_dir,
            previous_round_dirs=[],
            selected_fix_category=None,
            selected_layer=None,
            controller_justification="Missing gate field must fail typed.",
        )

    assert exc_info.value.code == "PTV_EVAL_RESULT_INVALID"


def test_round_summary_rejects_completed_case_total_mismatch(tmp_path: Path) -> None:
    round_dir = tmp_path / "round-01"
    _write_round_inputs(round_dir, round_number=1, commit="a" * 40)
    payload = json.loads((round_dir / "eval-result.json").read_text(encoding="utf-8"))
    payload["summary"]["completed_case_total"] = 0
    (round_dir / "eval-result.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PtvRuntimeError) as exc_info:
        build_round_summaries(
            cycle_id="cycle-20260620-1200",
            round_number=1,
            round_dir=round_dir,
            previous_round_dirs=[],
            selected_fix_category=None,
            selected_layer=None,
            controller_justification="Incomplete eval artifact must fail typed.",
        )

    assert exc_info.value.code == "PTV_EVAL_RESULT_INVALID"


def test_round_summary_rejects_incomplete_previous_round_history(tmp_path: Path) -> None:
    previous = tmp_path / "round-01"
    current = tmp_path / "round-02"
    previous.mkdir()
    _write_round_inputs(current, round_number=2, commit="a" * 40)

    with pytest.raises(PtvRuntimeError) as exc_info:
        build_round_summaries(
            cycle_id="cycle-20260620-1200",
            round_number=2,
            round_dir=current,
            previous_round_dirs=[previous],
            selected_fix_category=None,
            selected_layer=None,
            controller_justification="Previous round history must not be skipped.",
        )

    assert exc_info.value.code == "PTV_ARTIFACT_MISSING"


def _write_round_inputs(round_dir: Path, *, round_number: int, commit: str, memory_gate: bool = True) -> None:
    round_dir.mkdir(parents=True, exist_ok=True)
    metrics = {
        "eval_suite": "regression",
        "case_total": 1,
        "complete": True,
        "thresholds_met": True,
        "per_family_gate": True,
        "completed_case_total": 1,
        "top1_rate": 1.0,
        "top3_rate": 1.0,
        "root_cause_set_recall_avg": 1.0,
        "root_cause_set_precision_avg": 1.0,
        "weighted_explanation_coverage_avg": 1.0,
        "memory_treatment_gate": memory_gate,
    }
    write_json_atomic(
        round_dir / "eval-result.json",
        {"eval_id": f"eval-{round_number}", "summary": metrics, "cases": [{"case_id": "case-a", "top1_ok": 1, "top3_ok": 1}]},
    )
    write_json_atomic(
        round_dir / "gap_report.json",
        {"eval_id": f"eval-{round_number}", "gaps": [], "summary": {"total": 0}},
    )
    write_jsonl_atomic(round_dir / "diagnosis.jsonl", [])
    write_json_atomic(
        round_dir / "commit_lineage.json",
        {"eval_code_commit": commit, "fix_commit": commit, "post_eval_review_fix_commit": None},
    )



def test_rule_c2_counts_deferred_category_once_per_round(tmp_path: Path) -> None:
    first = tmp_path / "round-01"
    second = tmp_path / "round-02"
    third = tmp_path / "round-03"
    _write_round_inputs(first, round_number=1, commit="a" * 40)
    write_jsonl_atomic(
        first / "diagnosis.jsonl",
        [
            {"case_id": "case-a", "aspect": "outcome", "divergence": "design_flaw", "fix_category": "FIX-D"},
            {"case_id": "case-b", "aspect": "outcome", "divergence": "design_flaw", "fix_category": "FIX-D"},
        ],
    )
    build_round_summaries(
        cycle_id="cycle-20260620-1200",
        round_number=1,
        round_dir=first,
        previous_round_dirs=[],
        selected_fix_category="FIX-A",
        selected_layer="ranking",
        controller_justification="Multiple FIX-D gaps in one round count as one deferred round.",
    )

    _write_round_inputs(second, round_number=2, commit="b" * 40)
    write_jsonl_atomic(
        second / "diagnosis.jsonl",
        [{"case_id": "case-c", "aspect": "outcome", "divergence": "design_flaw", "fix_category": "FIX-D"}],
    )
    optimization, _ = build_round_summaries(
        cycle_id="cycle-20260620-1200",
        round_number=2,
        round_dir=second,
        previous_round_dirs=[first],
        selected_fix_category="FIX-M",
        selected_layer="composition",
        controller_justification="One prior round is not enough for RULE-C2 promotion.",
    )
    assert optimization["controller_rules_applied"]["rule_c2_promoted"] is None

    _write_round_inputs(third, round_number=3, commit="c" * 40)
    write_jsonl_atomic(
        third / "diagnosis.jsonl",
        [{"case_id": "case-d", "aspect": "outcome", "divergence": "design_flaw", "fix_category": "FIX-D"}],
    )
    optimization, _ = build_round_summaries(
        cycle_id="cycle-20260620-1200",
        round_number=3,
        round_dir=third,
        previous_round_dirs=[first, second],
        selected_fix_category="FIX-D",
        selected_layer="policy",
        controller_justification="RULE-C2 promotes FIX-D after two distinct deferred rounds.",
    )
    assert optimization["controller_rules_applied"]["rule_c2_promoted"] == "FIX-D"

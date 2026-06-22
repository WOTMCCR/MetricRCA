from __future__ import annotations

import json
from pathlib import Path

from metric_rca.evals.ptv_anti_cheat import REQUIRED_ASPECTS, validate_round_integrity
from metric_rca.evals.ptv_artifacts import write_json_atomic, write_jsonl_atomic


def test_valid_round_passes_anti_cheat(tmp_path: Path) -> None:
    round_dir = tmp_path / "cycle" / "round-01"
    _write_valid_round(round_dir)
    report = validate_round_integrity(
        round_dir=round_dir,
        round_number=1,
        fail_on_findings=False,
    )
    assert report.valid is True
    assert report.findings == ()


def test_templated_reasoning_is_rejected(tmp_path: Path) -> None:
    round_dir = tmp_path / "cycle" / "round-01"
    _write_valid_round(round_dir, templated=True)
    report = validate_round_integrity(
        round_dir=round_dir,
        round_number=1,
        fail_on_findings=False,
    )
    assert report.valid is False
    assert "PTV_TEMPLATE" in {finding.code for finding in report.findings}


def test_identical_non_confirmation_predictions_are_stale(tmp_path: Path) -> None:
    previous = tmp_path / "cycle" / "round-01"
    current = tmp_path / "cycle" / "round-02"
    _write_valid_round(previous, commit="a" * 40)
    _write_valid_round(current, commit="b" * 40)
    report = validate_round_integrity(
        round_dir=current,
        round_number=2,
        previous_round_dir=previous,
        confirmation_round=False,
        fail_on_findings=False,
    )
    assert "PTV_STALE" in {finding.code for finding in report.findings}


def _write_valid_round(round_dir: Path, *, templated: bool = False, commit: str = "a" * 40) -> None:
    round_dir.mkdir(parents=True, exist_ok=True)
    cases = [{"case_id": "case-alpha"}, {"case_id": "case-beta"}]
    write_json_atomic(
        round_dir / "eval-result.json",
        {
            "eval_id": "eval-1",
            "summary": {"case_total": len(cases)},
            "cases": cases,
        },
    )
    predictions = []
    for case_index, case in enumerate(cases):
        for aspect_index, aspect in enumerate(REQUIRED_ASPECTS):
            reasoning = (
                "metric_rca/runtime/plan_compiler.py:120 validates this case-specific path"
                if templated
                else f"metric_rca/runtime/{case['case_id']}_{aspect}.py:{100 + case_index * 10 + aspect_index} validates {case['case_id']} {aspect}"
            )
            predictions.append(
                {
                    "case_id": case["case_id"],
                    "aspect": aspect,
                    "prediction": _prediction(aspect),
                    "reasoning": reasoning,
                    "confidence": 0.7,
                    "risks": [f"{case['case_id']} {aspect} may diverge"],
                }
            )
    write_jsonl_atomic(round_dir / "predictions.jsonl", predictions)
    write_json_atomic(
        round_dir / "gap_report.json",
        {
            "eval_id": "eval-1",
            "gaps": [
                {
                    "case_id": row["case_id"],
                    "aspect": row["aspect"],
                    "divergence": "correct",
                }
                for row in predictions
            ],
            "summary": {"total": len(predictions), "accuracy": 1.0},
        },
    )
    (round_dir / "diagnosis.jsonl").write_text("", encoding="utf-8")
    write_json_atomic(
        round_dir / "commit_lineage.json",
        {
            "eval_code_commit": commit,
            "fix_commit": commit,
            "post_eval_review_fix_commit": None,
        },
    )
    write_json_atomic(
        round_dir / "optimization_summary.json",
        {
            "selected_fix_category": "FIX-D",
            "controller_rules_applied": {
                "rule_c1_blocked_categories": [],
                "rule_c2_promoted": None,
                "rule_c3_discovery_priority": False,
                "rule_c4_revert_assessment": {"triggered": False},
                "rule_c5_streak_counts": {"FIX-D": 1},
            },
        },
    )


def _prediction(aspect: str) -> dict[str, object]:
    return {
        "intent": {"metric_id": "gmv"},
        "execution": {"step_count": 5},
        "evidence": {"chain": ["E1", "E4", "E_rank"]},
        "memory": {"influence": "priority_only"},
        "outcome": {"root_cause_type": "campaign_traffic_drop"},
        "multi_cause_outcome": {"root_causes": [], "top3_ok": True},
    }[aspect]

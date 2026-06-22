from __future__ import annotations

import pytest

from metric_rca.evals.grpo_rewards import assess_coding_fix, coding_fix_reward, prediction_reward, task_trajectory_reward
from metric_rca.evals.grpo_schema import GrpoSchemaError


def test_effective_minimal_fix_receives_positive_reward() -> None:
    assessment = assess_coding_fix(
        before_summary={"top1_rate": 0.8, "top3_rate": 0.9},
        after_summary={"top1_rate": 0.9, "top3_rate": 1.0},
        before_cases=[_case("RS01", passed=False), _case("stable", passed=True)],
        after_cases=[_case("RS01", passed=True), _case("stable", passed=True)],
        targeted_cases=["RS01"],
        changed_files=["metric_rca/runtime/plan_compiler.py", "tests/test_runtime_plan.py"],
        proposed_files=["metric_rca/runtime/plan_compiler.py"],
    )
    reward = coding_fix_reward(assessment)
    assert assessment.fix_effective is True
    assert assessment.fix_minimal is True
    assert assessment.fix_regressed is False
    assert reward.eligible_for_positive is True
    assert reward.total > 0.0


def test_regression_blocks_positive_coding_example() -> None:
    assessment = assess_coding_fix(
        before_summary={"top1_rate": 0.9, "top3_rate": 1.0},
        after_summary={"top1_rate": 0.8, "top3_rate": 0.9},
        before_cases=[_case("RS01", passed=False), _case("stable", passed=True)],
        after_cases=[_case("RS01", passed=True), _case("stable", passed=False)],
        targeted_cases=["RS01"],
        changed_files=["metric_rca/runtime/plan_compiler.py"],
        proposed_files=["metric_rca/runtime/plan_compiler.py"],
    )
    reward = coding_fix_reward(assessment)
    assert assessment.fix_regressed is True
    assert reward.eligible_for_positive is False


def test_prediction_overfit_is_never_positive() -> None:
    reward = prediction_reward(divergence="overfit", reasoning_has_code_reference=True)
    assert reward.eligible_for_positive is False
    assert reward.exclusion_reason == "prediction_overfit_is_never_a_positive_example"


def test_task_trajectory_reward_rejects_missing_or_coerced_diagnostics() -> None:
    with pytest.raises(GrpoSchemaError) as exc_info:
        task_trajectory_reward(
            {
                "intent_ok": "1",
                "anomaly_ok": 1,
                "top3_ok": 1,
                "evidence_coverage": 1.0,
                "sql_safe": 1,
                "reflection_repair_ok": 1,
                "report_traceable_ok": 1,
            }
        )

    assert exc_info.value.code == "GRPO_REWARD_DIAGNOSTICS_INVALID"


def test_coding_fix_effectiveness_requires_evidence_and_memory_gates() -> None:
    assessment = assess_coding_fix(
        before_summary={"top1_rate": 0.8, "top3_rate": 0.9},
        after_summary={"top1_rate": 0.9, "top3_rate": 1.0},
        before_cases=[_case("RS01", passed=False)],
        after_cases=[{**_case("RS01", passed=True), "evidence_coverage": 0.5}],
        targeted_cases=["RS01"],
        changed_files=["metric_rca/runtime/plan_compiler.py"],
        proposed_files=["metric_rca/runtime/plan_compiler.py"],
    )

    assert assessment.fix_effective is False


def _case(case_id: str, *, passed: bool) -> dict[str, object]:
    value = 1 if passed else 0
    return {
        "case_id": case_id,
        "intent_ok": 1,
        "anomaly_ok": 1,
        "top3_ok": value,
        "sql_safe": 1,
        "reflection_repair_ok": 1,
        "report_traceable_ok": 1,
        "evidence_coverage": 1.0,
        "memory_pollution_ok": 1,
    }

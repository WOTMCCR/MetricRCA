"""Deterministic reward functions for controller, sub-agent, and coding-fix GRPO data."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

from metric_rca.evals.grpo_schema import GrpoSchemaError, RewardComponent, RewardRecord


TRACKED_AGGREGATES = (
    "top1_rate",
    "top3_rate",
    "root_cause_set_recall_avg",
    "root_cause_set_precision_avg",
    "weighted_explanation_coverage_avg",
)
CASE_PASS_FIELDS = (
    "intent_ok",
    "anomaly_ok",
    "top3_ok",
    "sql_safe",
    "reflection_repair_ok",
    "report_traceable_ok",
)


@dataclass(frozen=True)
class FixAssessment:
    fix_effective: bool
    fix_minimal: bool
    fix_regressed: bool
    targeted_cases: tuple[str, ...]
    recovered_cases: tuple[str, ...]
    regressed_cases: tuple[str, ...]
    regressed_metrics: tuple[str, ...]
    changed_files: tuple[str, ...]
    allowed_files: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "fix_effective": self.fix_effective,
            "fix_minimal": self.fix_minimal,
            "fix_regressed": self.fix_regressed,
            "targeted_cases": list(self.targeted_cases),
            "recovered_cases": list(self.recovered_cases),
            "regressed_cases": list(self.regressed_cases),
            "regressed_metrics": list(self.regressed_metrics),
            "changed_files": list(self.changed_files),
            "allowed_files": list(self.allowed_files),
        }


def controller_reward(
    *,
    before_metrics: Mapping[str, Any],
    after_metrics: Mapping[str, Any],
    controller_rules_valid: bool,
    selected_category_supported: bool,
) -> RewardRecord:
    deltas = metric_deltas(before_metrics, after_metrics)
    regression = any(value < -1e-12 for value in deltas.values())
    improvement = any(value > 1e-12 for value in deltas.values())
    components = (
        RewardComponent(
            name="rules_valid",
            value=1.0 if controller_rules_valid else -1.0,
            detail="RULE-C1..C5 were mechanically valid" if controller_rules_valid else "controller rule violation",
        ),
        RewardComponent(
            name="diagnosis_alignment",
            value=1.0 if selected_category_supported else -1.0,
            detail="selected category is supported by diagnosis" if selected_category_supported else "unsupported fix category",
        ),
        RewardComponent(
            name="next_round_effect",
            value=-1.0 if regression else (1.0 if improvement or bool(after_metrics.get("thresholds_met")) else 0.0),
            detail=f"aggregate metric deltas={deltas}",
        ),
    )
    total = _weighted_average(components)
    eligible = total > 0.0 and controller_rules_valid and selected_category_supported and not regression
    return RewardRecord(
        total=total,
        components=components,
        eligible_for_positive=eligible,
        exclusion_reason=None if eligible else "controller decision is not a verified positive example",
    )


def task_trajectory_reward(score: Mapping[str, Any]) -> RewardRecord:
    required = _fixed_task_gates(score)
    components = tuple(
        RewardComponent(name=name, value=1.0 if passed else -1.0, detail=f"{name}={passed}")
        for name, passed in required.items()
    )
    passed = all(required.values())
    return RewardRecord(
        total=1.0 if passed else 0.0,
        components=components,
        eligible_for_positive=passed,
        exclusion_reason=None if passed else "one or more fixed task gates failed",
    )


def prediction_reward(*, divergence: str, reasoning_has_code_reference: bool) -> RewardRecord:
    known = {"correct", "complexity_gap", "design_flaw", "overfit"}
    if divergence not in known:
        raise ValueError(f"GRPO_UNKNOWN_DIVERGENCE: {divergence}")
    correctness_value = {
        "correct": 1.0,
        "complexity_gap": -0.25,
        "design_flaw": -0.5,
        "overfit": 0.0,
    }[divergence]
    components = (
        RewardComponent(name="prediction_divergence", value=correctness_value, detail=f"divergence={divergence}"),
        RewardComponent(
            name="code_path_reasoning",
            value=1.0 if reasoning_has_code_reference else -1.0,
            detail="reasoning cites concrete code" if reasoning_has_code_reference else "missing concrete code reference",
        ),
    )
    total = _weighted_average(components)
    eligible = divergence == "correct" and reasoning_has_code_reference and total > 0.0
    exclusion = None
    if divergence == "overfit":
        exclusion = "prediction_overfit_is_never_a_positive_example"
    elif not eligible:
        exclusion = "prediction did not satisfy correctness and reasoning gates"
    return RewardRecord(
        total=total,
        components=components,
        eligible_for_positive=eligible,
        exclusion_reason=exclusion,
    )


def assess_coding_fix(
    *,
    before_summary: Mapping[str, Any],
    after_summary: Mapping[str, Any],
    before_cases: Iterable[Mapping[str, Any]],
    after_cases: Iterable[Mapping[str, Any]],
    targeted_cases: Iterable[str],
    changed_files: Iterable[str],
    proposed_files: Iterable[str],
) -> FixAssessment:
    before_by_case = {str(row.get("case_id")): row for row in before_cases if row.get("case_id") is not None}
    after_by_case = {str(row.get("case_id")): row for row in after_cases if row.get("case_id") is not None}
    targets = tuple(sorted(set(str(case_id) for case_id in targeted_cases)))
    recovered = tuple(
        case_id
        for case_id in targets
        if case_id in before_by_case
        and case_id in after_by_case
        and not _case_passed(before_by_case[case_id])
        and _case_passed(after_by_case[case_id])
    )
    regressed_cases = tuple(
        case_id
        for case_id in sorted(set(before_by_case) & set(after_by_case))
        if _case_passed(before_by_case[case_id]) and not _case_passed(after_by_case[case_id])
    )
    deltas = metric_deltas(before_summary, after_summary)
    regressed_metrics = tuple(name for name, value in deltas.items() if value < -1e-12)
    changed = tuple(sorted(set(str(path) for path in changed_files if str(path).strip())))
    proposed = tuple(sorted(set(str(path) for path in proposed_files if str(path).strip())))
    allowed = set(proposed)
    allowed.update(path for path in changed if path.startswith("tests/") or path.startswith("docs/"))
    code_changed = [path for path in changed if not path.startswith("tests/") and not path.startswith("docs/")]
    minimal = bool(changed) and all(path in allowed for path in code_changed) and len(changed) <= max(1, len(proposed) + 4)
    regressed = bool(regressed_cases or regressed_metrics)
    effective = bool(targets) and set(recovered) == set(targets) and not regressed
    return FixAssessment(
        fix_effective=effective,
        fix_minimal=minimal,
        fix_regressed=regressed,
        targeted_cases=targets,
        recovered_cases=recovered,
        regressed_cases=regressed_cases,
        regressed_metrics=regressed_metrics,
        changed_files=changed,
        allowed_files=tuple(sorted(allowed)),
    )


def coding_fix_reward(assessment: FixAssessment) -> RewardRecord:
    components = (
        RewardComponent(
            name="fix_effective",
            value=1.0 if assessment.fix_effective else -1.0,
            detail=f"recovered={list(assessment.recovered_cases)} targets={list(assessment.targeted_cases)}",
        ),
        RewardComponent(
            name="fix_minimal",
            value=1.0 if assessment.fix_minimal else -0.5,
            weight=0.5,
            detail=f"changed_files={list(assessment.changed_files)} allowed={list(assessment.allowed_files)}",
        ),
        RewardComponent(
            name="fix_regressed",
            value=-1.0 if assessment.fix_regressed else 1.0,
            detail=f"regressed_cases={list(assessment.regressed_cases)} regressed_metrics={list(assessment.regressed_metrics)}",
        ),
    )
    total = _weighted_average(components)
    eligible = assessment.fix_effective and assessment.fix_minimal and not assessment.fix_regressed and total > 0.0
    return RewardRecord(
        total=total,
        components=components,
        eligible_for_positive=eligible,
        exclusion_reason=None if eligible else "coding fix is ineffective, non-minimal, or regressed",
        fix_effective=assessment.fix_effective,
        fix_minimal=assessment.fix_minimal,
        fix_regressed=assessment.fix_regressed,
    )


def metric_deltas(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for name in TRACKED_AGGREGATES:
        old = before.get(name)
        new = after.get(name)
        if isinstance(old, (int, float)) and not isinstance(old, bool) and isinstance(new, (int, float)) and not isinstance(new, bool):
            result[name] = float(new) - float(old)
    return result


def _case_passed(row: Mapping[str, Any]) -> bool:
    return all(_fixed_task_gates(row).values())


def _fixed_task_gates(row: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "intent_ok": _binary_gate(row, "intent_ok"),
        "anomaly_ok": _binary_gate(row, "anomaly_ok"),
        "top3_ok": _binary_gate(row, "top3_ok"),
        "evidence_coverage": _numeric_gate(row, "evidence_coverage") >= 1.0,
        "sql_safe": _binary_gate(row, "sql_safe"),
        "reflection_repair_ok": _binary_gate(row, "reflection_repair_ok"),
        "report_traceable_ok": _binary_gate(row, "report_traceable_ok"),
        "memory_pollution_ok": _binary_gate(row, "memory_pollution_ok"),
    }


def _binary_gate(row: Mapping[str, Any], field: str) -> bool:
    if field not in row:
        raise GrpoSchemaError(
            "GRPO_REWARD_DIAGNOSTICS_INVALID",
            "reward diagnostics are missing a required gate",
            context={"field": field},
        )
    value = row[field]
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return value == 1
    raise GrpoSchemaError(
        "GRPO_REWARD_DIAGNOSTICS_INVALID",
        "reward gate must be boolean or integer 0/1",
        context={"field": field, "value": value},
    )


def _numeric_gate(row: Mapping[str, Any], field: str) -> float:
    if field not in row:
        raise GrpoSchemaError(
            "GRPO_REWARD_DIAGNOSTICS_INVALID",
            "reward diagnostics are missing a required numeric field",
            context={"field": field},
        )
    value = row[field]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise GrpoSchemaError(
            "GRPO_REWARD_DIAGNOSTICS_INVALID",
            "reward numeric field must be finite",
            context={"field": field, "value": value},
        )
    return float(value)


def _weighted_average(components: tuple[RewardComponent, ...]) -> float:
    numerator = sum(component.value * component.weight for component in components)
    denominator = sum(component.weight for component in components)
    value = numerator / denominator
    return round(max(-1.0, min(1.0, value)), 6)

"""Deterministic quality gates for generated business scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping

from metric_rca.data.dimension_catalog import DimensionCatalog, REQUIRED_DIMENSIONS
from metric_rca.data.eval_case_manifest import validate_public_cases
from metric_rca.data.metric_deriver import aggregate_metric, compare_to_baseline
from metric_rca.data.scenario_spec import ScenarioSet, ScenarioSpec, ShockType


@dataclass(frozen=True)
class QualityIssue:
    code: str
    scenario_id: str | None
    message: str
    context: dict[str, Any]


@dataclass(frozen=True)
class QualityReport:
    valid: bool
    issue_count: int
    issues: tuple[QualityIssue, ...]
    scenario_count: int
    row_count: int
    covered_dimensions: tuple[str, ...]
    covered_shocks: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "metricrca-generated-data-quality-v1",
            "valid": self.valid,
            "issue_count": self.issue_count,
            "issues": [
                {
                    "code": issue.code,
                    "scenario_id": issue.scenario_id,
                    "message": issue.message,
                    "context": issue.context,
                }
                for issue in self.issues
            ],
            "scenario_count": self.scenario_count,
            "row_count": self.row_count,
            "covered_dimensions": list(self.covered_dimensions),
            "covered_shocks": list(self.covered_shocks),
        }


def validate_generated_dataset(
    *,
    catalog: DimensionCatalog,
    scenario_set: ScenarioSet,
    scenario_rows: Mapping[str, list[dict[str, Any]]],
    ground_truth: list[dict[str, Any]],
    public_cases: list[dict[str, Any]],
) -> QualityReport:
    issues: list[QualityIssue] = []
    missing_dimensions = sorted(set(REQUIRED_DIMENSIONS) - set(catalog.dimensions))
    if missing_dimensions:
        issues.append(_issue("DIMENSION_COVERAGE_MISSING", None, "required dimensions are absent", missing=missing_dimensions))
    covered_shocks = {
        shock.shock_type.value
        for scenario in scenario_set.scenarios
        for shock in scenario.shocks
    }
    missing_shocks = sorted({shock.value for shock in ShockType} - covered_shocks)
    if missing_shocks:
        issues.append(_issue("SHOCK_COVERAGE_MISSING", None, "required shock types are absent", missing=missing_shocks))
    try:
        validate_public_cases(public_cases)
    except ValueError as exc:
        issues.append(_issue("PUBLIC_CASE_INVALID", None, str(exc)))
    public_ids = [str(row.get("case_id")) for row in public_cases]
    private_ids = [str(row.get("case_id")) for row in ground_truth]
    if sorted(public_ids) != sorted(private_ids) or len(public_ids) != len(set(public_ids)) or len(private_ids) != len(set(private_ids)):
        issues.append(
            _issue(
                "EVAL_CASE_PRIVATE_PUBLIC_MISMATCH",
                None,
                "public cases and private ground truth must have the same unique opaque case ids",
                public_ids=sorted(public_ids),
                private_ids=sorted(private_ids),
            )
        )

    truth_by_id = {str(row.get("scenario_id")): row for row in ground_truth}
    for scenario in scenario_set.scenarios:
        rows = scenario_rows.get(scenario.scenario_id)
        if not rows:
            issues.append(_issue("SCENARIO_ROWS_MISSING", scenario.scenario_id, "scenario rows are missing"))
            continue
        issues.extend(_validate_metric_identities(scenario, rows))
        issues.extend(_validate_effect(scenario, rows))
        issues.extend(_validate_negative_controls(scenario, rows))
        truth = truth_by_id.get(scenario.scenario_id)
        if truth is None:
            issues.append(_issue("GROUND_TRUTH_MISSING", scenario.scenario_id, "ground truth row is missing"))
        else:
            issues.extend(_validate_ground_truth(scenario, truth))
    total_rows = sum(len(rows) for rows in scenario_rows.values())
    return QualityReport(
        valid=not issues,
        issue_count=len(issues),
        issues=tuple(issues),
        scenario_count=len(scenario_set.scenarios),
        row_count=total_rows,
        covered_dimensions=tuple(sorted(catalog.dimensions)),
        covered_shocks=tuple(sorted(covered_shocks)),
    )


def assert_reproducible(first: Any, second: Any) -> None:
    first_hash = _stable_hash(first)
    second_hash = _stable_hash(second)
    if first_hash != second_hash:
        raise ValueError(f"SCENARIO_NOT_REPRODUCIBLE: {first_hash} != {second_hash}")


def _validate_effect(scenario: ScenarioSpec, rows: list[dict[str, Any]]) -> list[QualityIssue]:
    comparison = compare_to_baseline(
        rows,
        metric_id=scenario.metric_id,
        target_date=scenario.target_date,
        baseline=scenario.baseline_comparison,
    )
    ratio = comparison.delta_ratio
    threshold = scenario.baseline_comparison.minimum_effect_ratio
    valid = {
        "drop": ratio <= -threshold,
        "rise": ratio >= threshold,
        "spike": ratio >= threshold,
        "no_anomaly": abs(ratio) < threshold,
    }[scenario.anomaly_direction]
    if not valid:
        return [
            _issue(
                "SCENARIO_EFFECT_INVALID",
                scenario.scenario_id,
                "observed target/baseline effect does not match the declared direction",
                direction=scenario.anomaly_direction,
                delta_ratio=ratio,
                threshold=threshold,
            )
        ]
    return []


def _validate_negative_controls(scenario: ScenarioSpec, rows: list[dict[str, Any]]) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for control in scenario.negative_controls:
        baseline_values = [
            aggregate_metric(
                rows,
                metric_id=control.metric_id,
                business_date=scenario.target_date.fromordinal(scenario.target_date.toordinal() + offset),
                selector=control.selector,
            )
            for offset in scenario.baseline_comparison.offset_days
        ]
        target = aggregate_metric(
            rows,
            metric_id=control.metric_id,
            business_date=scenario.target_date,
            selector=control.selector,
        )
        baseline = sum(baseline_values) / len(baseline_values)
        ratio = (target - baseline) / abs(baseline) if baseline else 0.0
        if abs(ratio) > control.max_abs_delta_ratio:
            issues.append(
                _issue(
                    "NEGATIVE_CONTROL_FAILED",
                    scenario.scenario_id,
                    "negative control changed beyond its allowed boundary",
                    control=control.name,
                    delta_ratio=round(ratio, 9),
                    max_abs_delta_ratio=control.max_abs_delta_ratio,
                )
            )
    return issues


def _validate_metric_identities(scenario: ScenarioSpec, rows: Iterable[Mapping[str, Any]]) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for row in rows:
        orders = float(row.get("orders", 0.0))
        sessions = float(row.get("sessions", 0.0))
        gmv = float(row.get("gmv", 0.0))
        expected_gmv = orders * float(row.get("unit_price", 0.0)) * (1.0 - float(row.get("promotion_discount", 0.0)))
        if orders > sessions + 1e-6 or abs(gmv - expected_gmv) > max(1e-5, abs(expected_gmv) * 1e-6):
            issues.append(
                _issue(
                    "METRIC_IDENTITY_INVALID",
                    scenario.scenario_id,
                    "derived row violates orders<=sessions or GMV identity",
                    row_id=row.get("row_id"),
                )
            )
            break
    return issues


def _validate_ground_truth(scenario: ScenarioSpec, truth: Mapping[str, Any]) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    causes = truth.get("root_causes")
    if not isinstance(causes, list):
        return [_issue("GROUND_TRUTH_INVALID", scenario.scenario_id, "root_causes must be a list")]
    if scenario.expected_anomaly:
        weight_sum = sum(float(row.get("weight", 0.0)) for row in causes if isinstance(row, Mapping))
        if abs(weight_sum - 1.0) > 1e-9:
            issues.append(_issue("GROUND_TRUTH_INVALID", scenario.scenario_id, "root cause weights do not sum to 1", weight_sum=weight_sum))
        for cause in causes:
            chain = cause.get("expected_evidence_chain") if isinstance(cause, Mapping) else None
            if not isinstance(chain, list) or not chain or chain[0] != "E1" or chain[-1] != "E_rank":
                issues.append(_issue("GROUND_TRUTH_INVALID", scenario.scenario_id, "cause evidence chain is incomplete"))
    if not truth.get("negative_controls"):
        issues.append(_issue("GROUND_TRUTH_INVALID", scenario.scenario_id, "ground truth requires negative controls"))
    if not isinstance(truth.get("baseline_comparison"), Mapping):
        issues.append(_issue("GROUND_TRUTH_INVALID", scenario.scenario_id, "ground truth requires baseline comparison"))
    return issues


def _issue(code: str, scenario_id: str | None, message: str, **context: Any) -> QualityIssue:
    return QualityIssue(code=code, scenario_id=scenario_id, message=message, context=context)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()

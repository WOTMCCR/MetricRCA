"""Mechanical PTV integrity checks with no ground-truth or runtime bypass."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from metric_rca.evals.ptv_artifacts import read_json, read_jsonl, write_json_atomic
from metric_rca.evals.ptv_errors import PtvErrorCode, PtvRuntimeError


REQUIRED_ASPECTS = (
    "intent",
    "execution",
    "evidence",
    "memory",
    "outcome",
    "multi_cause_outcome",
)
_CODE_REFERENCE_RE = re.compile(
    r"(?:[A-Za-z0-9_./-]+\.py(?::\d+)?|[A-Za-z_][A-Za-z0-9_.]+\([A-Za-z0-9_, =]*\))"
)
_SECRET_TRUTH_TERMS = (
    "private_ground_truth",
    "regression_private_ground_truth",
    "anomaly_ground_truth",
    "ground truth says",
    "ground_truth expects",
)


@dataclass(frozen=True)
class AntiCheatFinding:
    code: str
    severity: str
    message: str
    context: dict[str, Any]


@dataclass(frozen=True)
class AntiCheatReport:
    valid: bool
    findings: tuple[AntiCheatFinding, ...]
    checks: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "metricrca-ptv-anti-cheat-v2",
            "valid": self.valid,
            "findings": [asdict(item) for item in self.findings],
            "checks": self.checks,
        }


def validate_round_integrity(
    *,
    round_dir: Path,
    round_number: int,
    previous_round_dir: Path | None = None,
    private_ground_truth_path: Path | None = None,
    confirmation_round: bool = False,
    fail_on_findings: bool = True,
) -> AntiCheatReport:
    predictions = read_jsonl(round_dir / "predictions.jsonl")
    eval_result = read_json(round_dir / "eval-result.json")
    findings: list[AntiCheatFinding] = []
    checks: dict[str, Any] = {}

    findings.extend(_check_prediction_shape(predictions, eval_result=eval_result, checks=checks))
    findings.extend(_check_reasoning_quality(predictions, checks=checks))
    findings.extend(_check_prediction_leakage(predictions, private_ground_truth_path, checks=checks))
    if previous_round_dir is not None:
        findings.extend(
            _check_cross_round_change(
                current=predictions,
                previous_path=previous_round_dir / "predictions.jsonl",
                confirmation_round=confirmation_round,
                checks=checks,
            )
        )
        findings.extend(
            _check_fix_commit(
                round_dir=round_dir,
                previous_round_dir=previous_round_dir,
                confirmation_round=confirmation_round,
                checks=checks,
            )
        )
    findings.extend(_check_diagnosis(round_dir, checks=checks))
    findings.extend(_check_controller_rules(round_dir, round_number=round_number, checks=checks))
    findings.extend(_check_stall(round_dir.parent, round_number=round_number, checks=checks))

    report = AntiCheatReport(valid=not findings, findings=tuple(findings), checks=checks)
    write_json_atomic(round_dir / "anti_cheat_report.json", report.as_dict())
    if findings and fail_on_findings:
        first = findings[0]
        raise PtvRuntimeError(
            first.code,
            first.message,
            context={"finding_count": len(findings), **first.context},
        )
    return report


def _check_prediction_shape(
    predictions: list[dict[str, Any]],
    *,
    eval_result: Mapping[str, Any],
    checks: dict[str, Any],
) -> list[AntiCheatFinding]:
    findings: list[AntiCheatFinding] = []
    case_ids = {str(row.get("case_id")) for row in eval_result.get("cases", []) if isinstance(row, Mapping)}
    seen: set[tuple[str, str]] = set()
    by_case: dict[str, set[str]] = {}
    for index, row in enumerate(predictions, start=1):
        case_id = row.get("case_id")
        aspect = row.get("aspect")
        prediction = row.get("prediction")
        confidence = row.get("confidence")
        risks = row.get("risks")
        if not isinstance(case_id, str) or not case_id or not isinstance(aspect, str) or not aspect:
            findings.append(_finding(PtvErrorCode.PREDICTION_INVALID, "HIGH", "prediction row lacks case_id/aspect", index=index))
            continue
        key = (case_id, aspect)
        if key in seen:
            findings.append(
                _finding(
                    PtvErrorCode.PREDICTION_INVALID,
                    "HIGH",
                    "duplicate case/aspect prediction",
                    case_id=case_id,
                    aspect=aspect,
                )
            )
        seen.add(key)
        by_case.setdefault(case_id, set()).add(aspect)
        if not isinstance(prediction, Mapping):
            findings.append(_finding(PtvErrorCode.PREDICTION_INVALID, "HIGH", "prediction payload must be an object", case_id=case_id, aspect=aspect))
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0.0 <= float(confidence) <= 1.0:
            findings.append(_finding(PtvErrorCode.PREDICTION_INVALID, "HIGH", "confidence must be in [0,1]", case_id=case_id, aspect=aspect))
        if not isinstance(risks, list) or not risks or any(not isinstance(item, str) or not item.strip() for item in risks):
            findings.append(_finding(PtvErrorCode.PREDICTION_INVALID, "HIGH", "risks must contain non-empty actionable strings", case_id=case_id, aspect=aspect))
    missing_cases = sorted(case_ids - set(by_case))
    extra_cases = sorted(set(by_case) - case_ids)
    if missing_cases or extra_cases:
        findings.append(
            _finding(
                PtvErrorCode.PREDICTION_INCOMPLETE,
                "HIGH",
                "prediction case set must exactly match eval case set",
                missing_cases=missing_cases,
                extra_cases=extra_cases,
            )
        )
    missing_aspects: dict[str, list[str]] = {}
    for case_id in sorted(case_ids):
        missing = sorted(set(REQUIRED_ASPECTS) - by_case.get(case_id, set()))
        if missing:
            missing_aspects[case_id] = missing
    if missing_aspects:
        findings.append(
            _finding(
                PtvErrorCode.PREDICTION_INCOMPLETE,
                "HIGH",
                "all six MetricRCA prediction aspects are required for every case",
                missing_aspects=missing_aspects,
            )
        )
    checks["prediction_shape"] = {
        "row_count": len(predictions),
        "case_count": len(by_case),
        "expected_case_count": len(case_ids),
        "required_aspects": list(REQUIRED_ASPECTS),
    }
    return findings


def _check_reasoning_quality(predictions: list[dict[str, Any]], *, checks: dict[str, Any]) -> list[AntiCheatFinding]:
    findings: list[AntiCheatFinding] = []
    normalized: list[str] = []
    missing_references: list[dict[str, str]] = []
    for row in predictions:
        reasoning = row.get("reasoning")
        if not isinstance(reasoning, str) or not reasoning.strip():
            missing_references.append({"case_id": str(row.get("case_id")), "aspect": str(row.get("aspect"))})
            continue
        normalized.append(_normalize_reasoning(reasoning))
        if _CODE_REFERENCE_RE.search(reasoning) is None:
            missing_references.append({"case_id": str(row.get("case_id")), "aspect": str(row.get("aspect"))})
    if missing_references:
        findings.append(
            _finding(
                PtvErrorCode.PREDICTION_INVALID,
                "HIGH",
                "prediction reasoning must cite a concrete Python file or callable",
                rows=missing_references[:25],
                omitted=max(0, len(missing_references) - 25),
            )
        )
    unique_ratio = len(set(normalized)) / len(normalized) if normalized else 0.0
    if normalized and unique_ratio < 0.50:
        findings.append(
            _finding(
                PtvErrorCode.PREDICTION_TEMPLATE,
                "HIGH",
                "prediction reasoning is overly templated",
                unique_ratio=round(unique_ratio, 6),
                unique_count=len(set(normalized)),
                reasoning_count=len(normalized),
            )
        )
    checks["reasoning_quality"] = {
        "reasoning_count": len(normalized),
        "unique_reasoning_count": len(set(normalized)),
        "unique_ratio": round(unique_ratio, 6),
        "missing_code_reference_count": len(missing_references),
    }
    return findings


def _check_prediction_leakage(
    predictions: list[dict[str, Any]],
    private_ground_truth_path: Path | None,
    *,
    checks: dict[str, Any],
) -> list[AntiCheatFinding]:
    findings: list[AntiCheatFinding] = []
    suspicious_rows = []
    for row in predictions:
        reasoning = str(row.get("reasoning", "")).lower()
        if any(term in reasoning for term in _SECRET_TRUTH_TERMS):
            suspicious_rows.append({"case_id": row.get("case_id"), "aspect": row.get("aspect")})
    exact_match_ratio: float | None = None
    if private_ground_truth_path is not None:
        truth_by_case = _load_truth_by_case(private_ground_truth_path)
        outcome_rows = [row for row in predictions if row.get("aspect") == "outcome" and row.get("case_id") in truth_by_case]
        exact = 0
        for row in outcome_rows:
            prediction = row.get("prediction") if isinstance(row.get("prediction"), Mapping) else {}
            truth = truth_by_case[str(row["case_id"])]
            predicted_triple = (
                prediction.get("root_cause_type"),
                prediction.get("dimension"),
                str(prediction.get("element")) if prediction.get("element") is not None else None,
            )
            truth_triple = (
                truth.get("root_cause_type"),
                truth.get("dimension"),
                str(truth.get("element")) if truth.get("element") is not None else None,
            )
            if predicted_triple == truth_triple:
                exact += 1
        exact_match_ratio = exact / len(outcome_rows) if outcome_rows else 0.0
        if exact_match_ratio > 0.80 and suspicious_rows:
            findings.append(
                _finding(
                    PtvErrorCode.PREDICTION_LEAKAGE,
                    "CRITICAL",
                    "predictions show both private-truth references and suspiciously high exact answer overlap",
                    exact_match_ratio=round(exact_match_ratio, 6),
                    suspicious_rows=suspicious_rows[:20],
                )
            )
    elif suspicious_rows:
        findings.append(
            _finding(
                PtvErrorCode.PREDICTION_LEAKAGE,
                "CRITICAL",
                "prediction reasoning references private ground-truth sources",
                suspicious_rows=suspicious_rows[:20],
            )
        )
    checks["ground_truth_leakage"] = {
        "private_truth_compared": private_ground_truth_path is not None,
        "exact_outcome_match_ratio": None if exact_match_ratio is None else round(exact_match_ratio, 6),
        "suspicious_reference_count": len(suspicious_rows),
    }
    return findings


def _check_cross_round_change(
    *,
    current: list[dict[str, Any]],
    previous_path: Path,
    confirmation_round: bool,
    checks: dict[str, Any],
) -> list[AntiCheatFinding]:
    if not previous_path.exists():
        raise PtvRuntimeError(
            PtvErrorCode.ARTIFACT_MISSING,
            "previous round predictions are required",
            context={"path": str(previous_path)},
        )
    previous = read_jsonl(previous_path)
    unchanged = _canonical_predictions(current) == _canonical_predictions(previous)
    checks["cross_round_prediction_change"] = {
        "unchanged": unchanged,
        "confirmation_round": confirmation_round,
    }
    if unchanged and not confirmation_round:
        return [
            _finding(
                PtvErrorCode.PREDICTION_STALE,
                "HIGH",
                "predictions are identical to the previous optimization round",
            )
        ]
    return []


def _check_fix_commit(
    *,
    round_dir: Path,
    previous_round_dir: Path,
    confirmation_round: bool,
    checks: dict[str, Any],
) -> list[AntiCheatFinding]:
    current = read_json(round_dir / "commit_lineage.json")
    previous = read_json(previous_round_dir / "commit_lineage.json")
    current_eval = current.get("eval_code_commit")
    previous_eval = previous.get("eval_code_commit")
    fix_commit = current.get("fix_commit")
    checks["commit_change"] = {
        "current_eval_code_commit": current_eval,
        "previous_eval_code_commit": previous_eval,
        "fix_commit": fix_commit,
        "confirmation_round": confirmation_round,
    }
    if confirmation_round:
        if current_eval != previous_eval:
            return [
                _finding(
                    PtvErrorCode.TWO_GREEN_INVALID,
                    "HIGH",
                    "formal confirmation must evaluate the same code commit as the first green round",
                    current=current_eval,
                    previous=previous_eval,
                )
            ]
        return []
    if current_eval == previous_eval or not fix_commit:
        return [
            _finding(
                PtvErrorCode.FIX_COMMIT_MISSING,
                "HIGH",
                "optimization rounds require a distinct evaluated fix commit",
                current=current_eval,
                previous=previous_eval,
                fix_commit=fix_commit,
            )
        ]
    return []


def _check_diagnosis(round_dir: Path, *, checks: dict[str, Any]) -> list[AntiCheatFinding]:
    gap_report = read_json(round_dir / "gap_report.json")
    gaps = gap_report.get("gaps")
    if not isinstance(gaps, list):
        raise PtvRuntimeError(PtvErrorCode.ARTIFACT_INVALID, "gap_report.gaps must be a list")
    divergent = [row for row in gaps if isinstance(row, Mapping) and row.get("divergence") != "correct"]
    diagnosis_path = round_dir / "diagnosis.jsonl"
    diagnosis = read_jsonl(diagnosis_path, allow_empty=True) if diagnosis_path.exists() else []
    diagnosis_keys = {
        (str(row.get("case_id")), str(row.get("aspect")))
        for row in diagnosis
        if row.get("case_id") is not None and row.get("aspect") is not None
    }
    missing = [
        {"case_id": row.get("case_id"), "aspect": row.get("aspect")}
        for row in divergent
        if (str(row.get("case_id")), str(row.get("aspect"))) not in diagnosis_keys
    ]
    checks["diagnosis_coverage"] = {
        "divergent_gap_count": len(divergent),
        "diagnosis_count": len(diagnosis),
        "missing_count": len(missing),
    }
    if missing:
        return [
            _finding(
                PtvErrorCode.DIAGNOSIS_MISSING,
                "MEDIUM",
                "every divergent case/aspect requires a diagnosis row",
                missing=missing[:50],
                omitted=max(0, len(missing) - 50),
            )
        ]
    return []


def _check_controller_rules(round_dir: Path, *, round_number: int, checks: dict[str, Any]) -> list[AntiCheatFinding]:
    summary_path = round_dir / "optimization_summary.json"
    if not summary_path.exists():
        checks["controller_rules"] = {"present": False}
        return [
            _finding(
                PtvErrorCode.CONTROLLER_RULES_MISSING,
                "HIGH",
                "optimization_summary.json is required before final verification",
            )
        ]
    summary = read_json(summary_path)
    rules = summary.get("controller_rules_applied")
    required = {
        "rule_c1_blocked_categories",
        "rule_c2_promoted",
        "rule_c3_discovery_priority",
        "rule_c4_revert_assessment",
        "rule_c5_streak_counts",
    }
    missing = sorted(required - set(rules)) if isinstance(rules, Mapping) else sorted(required)
    checks["controller_rules"] = {"present": isinstance(rules, Mapping), "missing": missing}
    if round_number > 1 and missing:
        return [
            _finding(
                PtvErrorCode.CONTROLLER_RULES_MISSING,
                "HIGH",
                "optimization summary is missing mandatory controller rule fields",
                missing=missing,
            )
        ]
    return []


def _check_stall(cycle_dir: Path, *, round_number: int, checks: dict[str, Any]) -> list[AntiCheatFinding]:
    selected: list[str] = []
    for number in range(max(1, round_number - 2), round_number + 1):
        path = cycle_dir / f"round-{number:02d}" / "optimization_summary.json"
        if not path.exists():
            continue
        summary = read_json(path)
        category = summary.get("selected_fix_category")
        if isinstance(category, str) and category:
            selected.append(category)
    stalled = len(selected) >= 3 and len(set(selected[-3:])) == 1
    checks["fix_category_stall"] = {"recent_categories": selected, "stalled": stalled}
    if stalled:
        return [
            _finding(
                PtvErrorCode.FIX_CATEGORY_STALL,
                "HIGH",
                "the same fix category was selected in three consecutive rounds",
                category=selected[-1],
            )
        ]
    return []


def _load_truth_by_case(path: Path) -> dict[str, dict[str, Any]]:
    if path.suffix == ".jsonl":
        rows = read_jsonl(path)
    else:
        payload = read_json(path)
        candidate = payload.get("cases", payload.get("ground_truth", []))
        if not isinstance(candidate, list):
            raise PtvRuntimeError(PtvErrorCode.ARTIFACT_INVALID, "private ground truth must contain a list")
        rows = [dict(row) for row in candidate if isinstance(row, Mapping)]
    return {str(row["case_id"]): row for row in rows if isinstance(row.get("case_id"), str)}


def _canonical_predictions(rows: Iterable[Mapping[str, Any]]) -> str:
    ordered = sorted((dict(row) for row in rows), key=lambda row: (str(row.get("case_id")), str(row.get("aspect"))))
    return json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_reasoning(value: str) -> str:
    normalized = value.lower()
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _finding(code: PtvErrorCode | str, severity: str, message: str, **context: Any) -> AntiCheatFinding:
    return AntiCheatFinding(code=str(code), severity=severity, message=message, context=context)

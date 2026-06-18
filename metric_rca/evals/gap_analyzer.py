"""Aspect-aware gap analyzer: compares predictions against actual eval results."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from metric_rca.evals.prediction import AspectPrediction, load_predictions


DEFAULT_OUTPUT_DIR = Path("eval_out")

DIVERGENCE_CORRECT = "correct"
DIVERGENCE_COMPLEXITY_GAP = "complexity_gap"
DIVERGENCE_DESIGN_FLAW = "design_flaw"
DIVERGENCE_OVERFIT = "overfit"


@dataclass
class AspectGap:
    case_id: str
    aspect: str
    divergence: str
    predicted: dict[str, Any]
    actual: dict[str, Any]
    detail: str


@dataclass
class GapReport:
    eval_id: str
    gaps: list[AspectGap]
    summary: dict[str, Any]


def analyze_gaps(
    predictions: list[AspectPrediction],
    actuals: list[dict[str, Any]],
    eval_id: str = "",
) -> GapReport:
    actuals_by_case = {row["case_id"]: row for row in actuals}
    gaps: list[AspectGap] = []
    for pred in predictions:
        actual = actuals_by_case.get(pred.case_id)
        if actual is None:
            gaps.append(AspectGap(
                case_id=pred.case_id,
                aspect=pred.aspect,
                divergence=DIVERGENCE_COMPLEXITY_GAP,
                predicted=pred.prediction,
                actual={},
                detail=f"no actual result found for case {pred.case_id}",
            ))
            continue
        analyzer = _ASPECT_ANALYZERS.get(pred.aspect, _analyze_unknown)
        gaps.append(analyzer(pred, actual))
    return GapReport(eval_id=eval_id, gaps=gaps, summary=_summarize(gaps))


def _analyze_intent(pred: AspectPrediction, actual: dict[str, Any]) -> AspectGap:
    detail = actual.get("detail", {})
    predicted_metric = pred.prediction.get("metric_id")
    actual_metric = detail.get("metric_id")
    actual_data = {"metric_id": actual_metric}
    if predicted_metric and actual_metric and predicted_metric != actual_metric:
        return AspectGap(
            case_id=pred.case_id, aspect="intent",
            divergence=DIVERGENCE_DESIGN_FLAW,
            predicted=pred.prediction, actual=actual_data,
            detail=f"intent parsed metric_id={actual_metric}, expected {predicted_metric}",
        )
    return AspectGap(
        case_id=pred.case_id, aspect="intent",
        divergence=DIVERGENCE_CORRECT,
        predicted=pred.prediction, actual=actual_data,
        detail="intent prediction matches",
    )


def _analyze_execution(pred: AspectPrediction, actual: dict[str, Any]) -> AspectGap:
    detail = actual.get("detail", {})
    if "step_count" not in pred.prediction:
        return _analyze_execution_invariants(pred, actual)
    predicted_steps = pred.prediction["step_count"]
    actual_steps = detail.get("trace_step_count", 0)
    actual_data = {"trace_step_count": actual_steps}
    diff = actual_steps - predicted_steps
    if abs(diff) <= 1:
        return AspectGap(
            case_id=pred.case_id, aspect="execution",
            divergence=DIVERGENCE_CORRECT,
            predicted=pred.prediction, actual=actual_data,
            detail=f"step count within tolerance (predicted={predicted_steps}, actual={actual_steps})",
        )
    if diff > 1:
        return AspectGap(
            case_id=pred.case_id, aspect="execution",
            divergence=DIVERGENCE_COMPLEXITY_GAP,
            predicted=pred.prediction, actual=actual_data,
            detail=f"more steps than predicted (predicted={predicted_steps}, actual={actual_steps}, +{diff})",
        )
    return AspectGap(
        case_id=pred.case_id, aspect="execution",
        divergence=DIVERGENCE_OVERFIT,
        predicted=pred.prediction, actual=actual_data,
        detail=f"fewer steps than predicted (predicted={predicted_steps}, actual={actual_steps}, {diff})",
    )


def _analyze_execution_invariants(pred: AspectPrediction, actual: dict[str, Any]) -> AspectGap:
    detail = actual.get("detail", {})
    expected_sequence = _string_list(pred.prediction.get("tool_sequence"))
    forbidden_tools = set(_string_list(pred.prediction.get("forbidden_tools")))
    actual_sequence = _string_list(detail.get("tool_sequence"))
    actual_data = {
        "trace_step_count": detail.get("trace_step_count"),
        "tool_sequence": actual_sequence,
    }
    forbidden_seen = [tool for tool in actual_sequence if tool in forbidden_tools]
    if forbidden_seen:
        return AspectGap(
            case_id=pred.case_id,
            aspect="execution",
            divergence=DIVERGENCE_DESIGN_FLAW,
            predicted=pred.prediction,
            actual=actual_data,
            detail=f"forbidden tools observed: {forbidden_seen}",
        )
    if expected_sequence:
        missing = _missing_ordered_subsequence(expected_sequence, actual_sequence)
        if missing:
            return AspectGap(
                case_id=pred.case_id,
                aspect="execution",
                divergence=DIVERGENCE_DESIGN_FLAW,
                predicted=pred.prediction,
                actual=actual_data,
                detail=f"required tool sequence missing or out of order: {missing}",
            )
        return AspectGap(
            case_id=pred.case_id,
            aspect="execution",
            divergence=DIVERGENCE_CORRECT,
            predicted=pred.prediction,
            actual=actual_data,
            detail="required tool sequence observed in order",
        )
    return AspectGap(
        case_id=pred.case_id,
        aspect="execution",
        divergence=DIVERGENCE_CORRECT,
        predicted=pred.prediction,
        actual=actual_data,
        detail="execution prediction has no machine-comparable step count or tool sequence",
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _missing_ordered_subsequence(expected: list[str], actual: list[str]) -> list[str]:
    missing: list[str] = []
    search_from = 0
    for item in expected:
        try:
            index = actual.index(item, search_from)
        except ValueError:
            missing.append(item)
            continue
        search_from = index + 1
    return missing


def _analyze_evidence(pred: AspectPrediction, actual: dict[str, Any]) -> AspectGap:
    coverage = actual.get("evidence_coverage", 0.0)
    actual_data = {"evidence_coverage": coverage}
    if coverage < 1.0:
        return AspectGap(
            case_id=pred.case_id, aspect="evidence",
            divergence=DIVERGENCE_DESIGN_FLAW,
            predicted=pred.prediction, actual=actual_data,
            detail=f"evidence chain incomplete (coverage={coverage})",
        )
    predicted_full = pred.prediction.get("evidence_coverage_full", True)
    if not predicted_full and coverage == 1.0:
        return AspectGap(
            case_id=pred.case_id, aspect="evidence",
            divergence=DIVERGENCE_OVERFIT,
            predicted=pred.prediction, actual=actual_data,
            detail="predicted incomplete evidence but coverage is full",
        )
    return AspectGap(
        case_id=pred.case_id, aspect="evidence",
        divergence=DIVERGENCE_CORRECT,
        predicted=pred.prediction, actual=actual_data,
        detail="evidence chain matches prediction",
    )


def _analyze_memory(pred: AspectPrediction, actual: dict[str, Any]) -> AspectGap:
    pollution_ok = bool(actual.get("memory_pollution_ok", 1))
    predicted_pollution_risk = pred.prediction.get("pollution_risk", False)
    actual_data = {"memory_pollution_ok": pollution_ok}
    if not pollution_ok and not predicted_pollution_risk:
        return AspectGap(
            case_id=pred.case_id, aspect="memory",
            divergence=DIVERGENCE_DESIGN_FLAW,
            predicted=pred.prediction, actual=actual_data,
            detail="memory pollution detected but not predicted as risk",
        )
    if not pollution_ok and predicted_pollution_risk:
        return AspectGap(
            case_id=pred.case_id, aspect="memory",
            divergence=DIVERGENCE_COMPLEXITY_GAP,
            predicted=pred.prediction, actual=actual_data,
            detail="predicted memory pollution risk materialized",
        )
    if pollution_ok and predicted_pollution_risk:
        return AspectGap(
            case_id=pred.case_id, aspect="memory",
            divergence=DIVERGENCE_OVERFIT,
            predicted=pred.prediction, actual=actual_data,
            detail="predicted memory pollution risk but no pollution occurred",
        )
    return AspectGap(
        case_id=pred.case_id, aspect="memory",
        divergence=DIVERGENCE_CORRECT,
        predicted=pred.prediction, actual=actual_data,
        detail="memory behavior matches prediction",
    )


def _analyze_outcome(pred: AspectPrediction, actual: dict[str, Any]) -> AspectGap:
    detail = actual.get("detail", {})
    selected = detail.get("selected_candidate") or {}
    actual_rc_type = selected.get("root_cause_type")
    actual_dim = selected.get("dimension")
    actual_elem = selected.get("element")
    actual_top1 = bool(actual.get("top1_ok", 0))
    actual_anomaly = bool(actual.get("anomaly_ok", 0))
    actual_intent = bool(actual.get("intent_ok", 0))
    actual_data = {
        "root_cause_type": actual_rc_type, "dimension": actual_dim,
        "element": actual_elem, "top1_ok": actual_top1,
        "anomaly_ok": actual_anomaly, "intent_ok": actual_intent,
    }
    if not actual_intent:
        return AspectGap(
            case_id=pred.case_id, aspect="outcome",
            divergence=DIVERGENCE_DESIGN_FLAW,
            predicted=pred.prediction, actual=actual_data,
            detail="intent parsing failed — system design issue",
        )
    predicted_top1 = pred.prediction.get("top1_ok", True)
    predicted_anomaly = pred.prediction.get("anomaly_ok", True)
    if predicted_top1 is True and not actual_top1:
        predicted_rc = pred.prediction.get("root_cause_type")
        if predicted_rc and actual_rc_type and predicted_rc != actual_rc_type:
            return AspectGap(
                case_id=pred.case_id, aspect="outcome",
                divergence=DIVERGENCE_DESIGN_FLAW,
                predicted=pred.prediction, actual=actual_data,
                detail=f"wrong root cause type: predicted {predicted_rc}, got {actual_rc_type}",
            )
        return AspectGap(
            case_id=pred.case_id, aspect="outcome",
            divergence=DIVERGENCE_COMPLEXITY_GAP,
            predicted=pred.prediction, actual=actual_data,
            detail="predicted top1 pass but actual fail",
        )
    if predicted_top1 is False and actual_top1:
        return AspectGap(
            case_id=pred.case_id, aspect="outcome",
            divergence=DIVERGENCE_OVERFIT,
            predicted=pred.prediction, actual=actual_data,
            detail="predicted top1 fail but actual pass",
        )
    if predicted_anomaly is True and not actual_anomaly:
        return AspectGap(
            case_id=pred.case_id, aspect="outcome",
            divergence=DIVERGENCE_DESIGN_FLAW,
            predicted=pred.prediction, actual=actual_data,
            detail="anomaly detection failed",
        )
    return AspectGap(
        case_id=pred.case_id, aspect="outcome",
        divergence=DIVERGENCE_CORRECT,
        predicted=pred.prediction, actual=actual_data,
        detail="outcome matches prediction",
    )


def _analyze_multi_cause_outcome(pred: AspectPrediction, actual: dict[str, Any]) -> AspectGap:
    actual_top3 = bool(actual.get("top3_ok", actual.get("top3_contains_all_major_causes", 0)))
    actual_recall = float(actual.get("root_cause_set_recall", 0.0) or 0.0)
    actual_weighted_coverage = float(actual.get("weighted_explanation_coverage", 0.0) or 0.0)
    actual_all_major = bool(actual.get("top3_contains_all_major_causes", actual.get("top3_ok", 0)))
    actual_data = {
        "top3_ok": actual_top3,
        "top3_contains_all_major_causes": actual_all_major,
        "root_cause_set_recall": actual_recall,
        "weighted_explanation_coverage": actual_weighted_coverage,
    }
    predicted_top3 = pred.prediction.get("top3_ok")
    if predicted_top3 is True and not actual_top3:
        return AspectGap(
            case_id=pred.case_id,
            aspect="multi_cause_outcome",
            divergence=DIVERGENCE_COMPLEXITY_GAP,
            predicted=pred.prediction,
            actual=actual_data,
            detail="predicted multi-cause top3 pass but actual fail",
        )
    if predicted_top3 is True and (actual_recall < 0.85 or actual_weighted_coverage < 0.85):
        return AspectGap(
            case_id=pred.case_id,
            aspect="multi_cause_outcome",
            divergence=DIVERGENCE_COMPLEXITY_GAP,
            predicted=pred.prediction,
            actual=actual_data,
            detail=(
                "predicted complete multi-cause set but actual recall or weighted coverage "
                f"is below threshold (recall={actual_recall}, weighted={actual_weighted_coverage})"
            ),
        )
    if predicted_top3 is False and actual_top3:
        return AspectGap(
            case_id=pred.case_id,
            aspect="multi_cause_outcome",
            divergence=DIVERGENCE_OVERFIT,
            predicted=pred.prediction,
            actual=actual_data,
            detail="predicted multi-cause top3 fail but actual pass",
        )
    return AspectGap(
        case_id=pred.case_id,
        aspect="multi_cause_outcome",
        divergence=DIVERGENCE_CORRECT,
        predicted=pred.prediction,
        actual=actual_data,
        detail="multi-cause outcome matches prediction",
    )


def _analyze_unknown(pred: AspectPrediction, actual: dict[str, Any]) -> AspectGap:
    return AspectGap(
        case_id=pred.case_id, aspect=pred.aspect,
        divergence=DIVERGENCE_DESIGN_FLAW,
        predicted=pred.prediction, actual={},
        detail=f"unknown aspect '{pred.aspect}' cannot be compared",
    )


_ASPECT_ANALYZERS: dict[str, Any] = {
    "intent": _analyze_intent,
    "execution": _analyze_execution,
    "evidence": _analyze_evidence,
    "memory": _analyze_memory,
    "outcome": _analyze_outcome,
    "multi_cause_outcome": _analyze_multi_cause_outcome,
}


def _summarize(gaps: list[AspectGap]) -> dict[str, Any]:
    total = len(gaps)
    if total == 0:
        return {"total": 0}
    by_divergence: dict[str, int] = {}
    by_aspect: dict[str, dict[str, int]] = {}
    for gap in gaps:
        by_divergence[gap.divergence] = by_divergence.get(gap.divergence, 0) + 1
        aspect_counts = by_aspect.setdefault(gap.aspect, {})
        aspect_counts[gap.divergence] = aspect_counts.get(gap.divergence, 0) + 1
    return {
        "total": total,
        "by_divergence": by_divergence,
        "by_aspect": by_aspect,
        "accuracy": round(by_divergence.get(DIVERGENCE_CORRECT, 0) / total, 4),
        "design_flaw_count": by_divergence.get(DIVERGENCE_DESIGN_FLAW, 0),
        "complexity_gap_count": by_divergence.get(DIVERGENCE_COMPLEXITY_GAP, 0),
    }


def format_markdown(report: GapReport) -> str:
    lines = [f"# Gap Report: {report.eval_id}", ""]
    s = report.summary
    total = s.get("total", 0)
    correct_count = s.get("by_divergence", {}).get(DIVERGENCE_CORRECT, 0)
    lines.append(f"**Accuracy**: {s.get('accuracy', 0):.0%} ({correct_count}/{total})")
    lines.append("")
    for div_type in [DIVERGENCE_DESIGN_FLAW, DIVERGENCE_COMPLEXITY_GAP, DIVERGENCE_OVERFIT, DIVERGENCE_CORRECT]:
        count = s.get("by_divergence", {}).get(div_type, 0)
        if count > 0:
            lines.append(f"- **{div_type}**: {count}")
    lines.extend(["", "## Details", ""])
    for gap in report.gaps:
        if gap.divergence == DIVERGENCE_CORRECT:
            continue
        lines.append(f"### {gap.case_id} [{gap.aspect}] — {gap.divergence}")
        lines.append(f"  {gap.detail}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="MetricRCA prediction gap analyzer")
    parser.add_argument("--eval-id", required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    predictions_path = args.output_dir / args.eval_id / "predictions.jsonl"
    eval_json_path = args.output_dir / f"{args.eval_id}.json"

    if not predictions_path.exists():
        print(json.dumps({"error": f"predictions not found: {predictions_path}"}))
        return 1
    if not eval_json_path.exists():
        print(json.dumps({"error": f"eval output not found: {eval_json_path}"}))
        return 1

    predictions = load_predictions(predictions_path)
    actuals = json.loads(eval_json_path.read_text())["cases"]

    report = analyze_gaps(predictions, actuals, eval_id=args.eval_id)
    report_path = args.output_dir / args.eval_id / "gap_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str))

    print(format_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Multi-aspect prediction schema for predict-then-verify eval workflow."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


KNOWN_ASPECTS = frozenset({"intent", "execution", "evidence", "memory", "outcome"})
NO_ANOMALY_FORBIDDEN_TOOLS = frozenset(
    {"drilldown_dimension", "fetch_related_signal", "calculate_contribution", "rank_root_causes"}
)

ASPECT_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "intent": ("metric_id",),
    "execution": (),
    "evidence": ("chain",),
    "memory": ("influence",),
    "outcome": ("root_cause_type",),
}


@dataclass(frozen=True)
class AspectPrediction:
    case_id: str
    aspect: str
    prediction: dict[str, Any]
    reasoning: str
    confidence: float
    risks: tuple[str, ...] = ()


def write_predictions(predictions: list[AspectPrediction], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for pred in predictions:
        lines.append(json.dumps(
            {
                "case_id": pred.case_id,
                "aspect": pred.aspect,
                "prediction": pred.prediction,
                "reasoning": pred.reasoning,
                "confidence": pred.confidence,
                "risks": list(pred.risks),
            },
            ensure_ascii=False,
            default=str,
        ))
    path.write_text("\n".join(lines) + "\n" if lines else "")


def load_predictions(path: Path) -> list[AspectPrediction]:
    predictions: list[AspectPrediction] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        case_id = payload.get("case_id")
        aspect = payload.get("aspect")
        prediction = payload.get("prediction")
        if not isinstance(case_id, str) or not isinstance(aspect, str):
            raise ValueError(f"invalid prediction at line {line_number}: missing case_id or aspect")
        if not isinstance(prediction, dict):
            raise ValueError(f"invalid prediction at line {line_number}: prediction must be a dict")
        predictions.append(AspectPrediction(
            case_id=case_id,
            aspect=aspect,
            prediction=prediction,
            reasoning=str(payload.get("reasoning", "")),
            confidence=float(payload.get("confidence", 0.0)),
            risks=tuple(payload.get("risks", [])),
        ))
    return predictions


def validate_prediction(pred: AspectPrediction) -> list[str]:
    warnings: list[str] = []
    if not pred.reasoning.strip():
        warnings.append("reasoning must not be empty")
    if not pred.risks or any(not str(risk).strip() for risk in pred.risks):
        warnings.append("risks must contain at least one non-empty risk")
    if pred.aspect not in KNOWN_ASPECTS:
        warnings.append(f"unknown aspect '{pred.aspect}'")
        return warnings
    required = ASPECT_REQUIRED_KEYS.get(pred.aspect, ())
    for key in required:
        if key not in pred.prediction:
            warnings.append(f"aspect '{pred.aspect}' missing required key '{key}'")
    if pred.aspect == "execution" and not {
        "step_count",
        "tool_sequence",
        "critical_decisions",
    } & set(pred.prediction):
        warnings.append(
            "aspect 'execution' missing one of 'step_count', 'tool_sequence', or 'critical_decisions'"
        )
    if not 0.0 <= pred.confidence <= 1.0:
        warnings.append(f"confidence {pred.confidence} outside [0.0, 1.0]")
    return warnings


def validate_predictions(predictions: list[AspectPrediction]) -> list[str]:
    warnings: list[str] = []
    for index, pred in enumerate(predictions, start=1):
        for warning in validate_prediction(pred):
            warnings.append(f"line {index} {pred.case_id}/{pred.aspect}: {warning}")
    warnings.extend(_validate_no_anomaly_execution_predictions(predictions))
    return warnings


def _validate_no_anomaly_execution_predictions(predictions: list[AspectPrediction]) -> list[str]:
    by_case: dict[str, dict[str, AspectPrediction]] = {}
    for pred in predictions:
        by_case.setdefault(pred.case_id, {})[pred.aspect] = pred
    warnings: list[str] = []
    for case_id, aspects in sorted(by_case.items()):
        outcome = aspects.get("outcome")
        if outcome is None or not _is_no_anomaly_outcome_prediction(outcome.prediction):
            continue
        execution = aspects.get("execution")
        if execution is None:
            warnings.append(
                f"{case_id}/outcome predicts no-anomaly; matching execution prediction with forbidden_tools is required"
            )
            continue
        execution_prediction = execution.prediction
        tool_sequence = _string_set(execution_prediction.get("tool_sequence"))
        forbidden_tools = _string_set(execution_prediction.get("forbidden_tools"))
        missing_forbidden = sorted(NO_ANOMALY_FORBIDDEN_TOOLS - forbidden_tools)
        if "detect_anomaly" not in tool_sequence or missing_forbidden:
            warnings.append(
                f"{case_id}/execution no-anomaly prediction must include detect_anomaly "
                f"and forbidden_tools for downstream RCA tools; missing={missing_forbidden}"
            )
    return warnings


def _is_no_anomaly_outcome_prediction(prediction: dict[str, Any]) -> bool:
    return (
        prediction.get("root_cause_type") is None
        and prediction.get("dimension") is None
        and prediction.get("element") is None
        and prediction.get("top1_ok") is True
        and prediction.get("anomaly_ok") is True
    )


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if isinstance(item, str) and item}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m metric_rca.evals.prediction <predictions.jsonl>", file=sys.stderr)
        return 2
    path = Path(args[0])
    predictions = load_predictions(path)
    warnings = validate_predictions(predictions)
    for warning in warnings:
        print(warning, file=sys.stderr)
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for multi-aspect prediction schema and gap analyzer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from metric_rca.evals.prediction import (
    AspectPrediction,
    load_predictions,
    validate_prediction,
    validate_predictions,
    write_predictions,
)
from metric_rca.evals.gap_analyzer import (
    DIVERGENCE_COMPLEXITY_GAP,
    DIVERGENCE_CORRECT,
    DIVERGENCE_DESIGN_FLAW,
    DIVERGENCE_OVERFIT,
    analyze_gaps,
    format_markdown,
)


# ── prediction schema tests ──────────────────────────────────


def test_prediction_roundtrip(tmp_path: Path) -> None:
    predictions = [
        AspectPrediction(
            case_id="gmv_paid_ads_drop", aspect="intent",
            prediction={"metric_id": "gmv", "analysis_strategy": "standard"},
            reasoning="scoped question about paid ads", confidence=0.95, risks=(),
        ),
        AspectPrediction(
            case_id="gmv_paid_ads_drop", aspect="outcome",
            prediction={"root_cause_type": "campaign_traffic_drop", "top1_ok": True},
            reasoning="direct filter match", confidence=0.9, risks=("ranker may prefer UV path",),
        ),
    ]
    path = tmp_path / "predictions.jsonl"
    write_predictions(predictions, path)
    loaded = load_predictions(path)

    assert len(loaded) == 2
    assert loaded[0].case_id == "gmv_paid_ads_drop"
    assert loaded[0].aspect == "intent"
    assert loaded[0].prediction["metric_id"] == "gmv"
    assert loaded[0].confidence == 0.95
    assert loaded[1].risks == ("ranker may prefer UV path",)


def test_prediction_load_rejects_missing_case_id(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"aspect": "intent", "prediction": {}}\n')
    with pytest.raises(ValueError, match="missing case_id"):
        load_predictions(path)


def test_prediction_load_rejects_non_dict_prediction(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"case_id": "x", "aspect": "intent", "prediction": "not a dict"}\n')
    with pytest.raises(ValueError, match="prediction must be a dict"):
        load_predictions(path)


def test_prediction_load_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "sparse.jsonl"
    path.write_text('\n{"case_id": "x", "aspect": "intent", "prediction": {"metric_id": "gmv"}}\n\n')
    loaded = load_predictions(path)
    assert len(loaded) == 1


def test_validate_prediction_known_aspect_missing_key() -> None:
    pred = AspectPrediction(
        case_id="x", aspect="outcome",
        prediction={"top1_ok": True}, reasoning="", confidence=0.5,
    )
    warnings = validate_prediction(pred)
    assert any("root_cause_type" in w for w in warnings)


def test_validate_prediction_execution_accepts_tool_sequence_without_step_count() -> None:
    pred = AspectPrediction(
        case_id="x", aspect="execution",
        prediction={
            "tool_sequence": ["detect_anomaly", "rank_root_causes"],
            "critical_decisions": ["rank_root_causes must persist E_rank"],
        },
        reasoning="sequence invariant",
        confidence=0.8,
        risks=("LLM may skip ranking",),
    )

    assert validate_prediction(pred) == []


def test_validate_prediction_unknown_aspect() -> None:
    pred = AspectPrediction(
        case_id="x", aspect="custom_aspect",
        prediction={"foo": "bar"}, reasoning="", confidence=0.5,
    )
    warnings = validate_prediction(pred)
    assert any("unknown aspect" in w for w in warnings)


def test_validate_prediction_bad_confidence() -> None:
    pred = AspectPrediction(
        case_id="x", aspect="intent",
        prediction={"metric_id": "gmv"}, reasoning="", confidence=1.5,
    )
    warnings = validate_prediction(pred)
    assert any("confidence" in w for w in warnings)


def test_validate_prediction_all_clear() -> None:
    pred = AspectPrediction(
        case_id="x", aspect="intent",
        prediction={"metric_id": "gmv"}, reasoning="test", confidence=0.9,
        risks=("parser may emit wrong metric",),
    )
    assert validate_prediction(pred) == []


def test_validate_prediction_rejects_empty_reasoning_and_risks() -> None:
    pred = AspectPrediction(
        case_id="x", aspect="intent",
        prediction={"metric_id": "gmv"}, reasoning="", confidence=0.9,
        risks=(),
    )
    warnings = validate_prediction(pred)

    assert any("reasoning" in warning for warning in warnings)
    assert any("risks" in warning for warning in warnings)


def test_prediction_main_fails_on_empty_risks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from metric_rca.evals import prediction

    path = tmp_path / "predictions.jsonl"
    path.write_text(
        '{"case_id":"c1","aspect":"intent","prediction":{"metric_id":"gmv"},'
        '"reasoning":"metric is explicit","confidence":0.9,"risks":[]}\n'
    )

    assert prediction.main([str(path)]) == 1
    assert "risks" in capsys.readouterr().err


def test_validate_predictions_requires_no_anomaly_execution_forbidden_tools() -> None:
    predictions = [
        AspectPrediction(
            case_id="gmv_no_anomaly",
            aspect="outcome",
            prediction={
                "root_cause_type": None,
                "dimension": None,
                "element": None,
                "top1_ok": True,
                "anomaly_ok": True,
            },
            reasoning="clean no-anomaly should have no candidate",
            confidence=0.9,
            risks=("downstream RCA could still run",),
        )
    ]

    warnings = validate_predictions(predictions)

    assert any("gmv_no_anomaly" in warning and "forbidden_tools" in warning for warning in warnings)


def test_validate_predictions_accepts_no_anomaly_execution_forbidden_tools() -> None:
    predictions = [
        AspectPrediction(
            case_id="gmv_no_anomaly",
            aspect="outcome",
            prediction={
                "root_cause_type": None,
                "dimension": None,
                "element": None,
                "top1_ok": True,
                "anomaly_ok": True,
            },
            reasoning="clean no-anomaly should have no candidate",
            confidence=0.9,
            risks=("downstream RCA could still run",),
        ),
        AspectPrediction(
            case_id="gmv_no_anomaly",
            aspect="execution",
            prediction={
                "tool_sequence": ["detect_anomaly"],
                "forbidden_tools": [
                    "drilldown_dimension",
                    "fetch_related_signal",
                    "calculate_contribution",
                    "rank_root_causes",
                ],
            },
            reasoning="no-anomaly should stop after E1",
            confidence=0.9,
            risks=("LLM could still ask for drilldown",),
        ),
    ]

    assert validate_predictions(predictions) == []


def test_prediction_main_fails_when_no_anomaly_execution_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from metric_rca.evals import prediction

    path = tmp_path / "predictions.jsonl"
    path.write_text(
        '{"case_id":"gmv_no_anomaly","aspect":"outcome",'
        '"prediction":{"root_cause_type":null,"dimension":null,"element":null,'
        '"top1_ok":true,"anomaly_ok":true},'
        '"reasoning":"clean no anomaly","confidence":0.9,'
        '"risks":["downstream RCA could still run"]}\n'
    )

    assert prediction.main([str(path)]) == 1
    assert "forbidden_tools" in capsys.readouterr().err


# ── gap analyzer tests ────────────────────────────────────────


def _actual(
    case_id: str = "case_1",
    *,
    intent_ok: int = 1,
    anomaly_ok: int = 1,
    top1_ok: int = 1,
    evidence_coverage: float = 1.0,
    memory_pollution_ok: int = 1,
    trace_step_count: int = 5,
    metric_id: str = "gmv",
    selected_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "intent_ok": intent_ok,
        "anomaly_ok": anomaly_ok,
        "top1_ok": top1_ok,
        "evidence_coverage": evidence_coverage,
        "memory_pollution_ok": memory_pollution_ok,
        "sql_safe": 1,
        "reflection_repair_ok": 1,
        "report_traceable_ok": 1,
        "detail": {
            "metric_id": metric_id,
            "trace_step_count": trace_step_count,
            "selected_candidate": selected_candidate or {
                "root_cause_type": "campaign_traffic_drop",
                "dimension": "channel",
                "element": "paid_ads",
            },
        },
    }


def test_gap_intent_correct() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="intent",
        prediction={"metric_id": "gmv"}, reasoning="", confidence=0.9,
    )
    report = analyze_gaps([pred], [_actual("c1")])
    assert report.gaps[0].divergence == DIVERGENCE_CORRECT


def test_gap_intent_design_flaw() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="intent",
        prediction={"metric_id": "pay_cvr"}, reasoning="", confidence=0.9,
    )
    report = analyze_gaps([pred], [_actual("c1", metric_id="gmv")])
    assert report.gaps[0].divergence == DIVERGENCE_DESIGN_FLAW


def test_gap_execution_correct_within_tolerance() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="execution",
        prediction={"step_count": 5}, reasoning="", confidence=0.8,
    )
    report = analyze_gaps([pred], [_actual("c1", trace_step_count=6)])
    assert report.gaps[0].divergence == DIVERGENCE_CORRECT


def test_gap_execution_complexity_gap() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="execution",
        prediction={"step_count": 5}, reasoning="", confidence=0.8,
    )
    report = analyze_gaps([pred], [_actual("c1", trace_step_count=8)])
    assert report.gaps[0].divergence == DIVERGENCE_COMPLEXITY_GAP


def test_gap_execution_overfit() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="execution",
        prediction={"step_count": 8}, reasoning="", confidence=0.5,
    )
    report = analyze_gaps([pred], [_actual("c1", trace_step_count=4)])
    assert report.gaps[0].divergence == DIVERGENCE_OVERFIT


def test_gap_execution_tool_sequence_correct_without_step_count() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="execution",
        prediction={
            "tool_sequence": ["detect_anomaly", "drilldown_dimension", "rank_root_causes"],
            "critical_decisions": ["rank_root_causes must persist E_rank"],
        },
        reasoning="sequence invariant",
        confidence=0.8,
    )
    actual = _actual("c1")
    actual["detail"]["tool_sequence"] = [
        "parse_question",
        "detect_anomaly",
        "drilldown_dimension",
        "calculate_contribution",
        "rank_root_causes",
    ]

    report = analyze_gaps([pred], [actual])

    assert report.gaps[0].divergence == DIVERGENCE_CORRECT
    assert report.gaps[0].actual["tool_sequence"] == actual["detail"]["tool_sequence"]


def test_gap_execution_tool_sequence_missing_required_tool_is_design_flaw() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="execution",
        prediction={"tool_sequence": ["detect_anomaly", "rank_root_causes"]},
        reasoning="rank is required for E_rank",
        confidence=0.9,
    )
    actual = _actual("c1")
    actual["detail"]["tool_sequence"] = ["parse_question", "detect_anomaly", "calculate_contribution"]

    report = analyze_gaps([pred], [actual])

    assert report.gaps[0].divergence == DIVERGENCE_DESIGN_FLAW
    assert "rank_root_causes" in report.gaps[0].detail


def test_gap_execution_forbidden_downstream_tool_is_design_flaw() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="execution",
        prediction={
            "tool_sequence": ["detect_anomaly"],
            "forbidden_tools": ["drilldown_dimension", "calculate_contribution", "rank_root_causes"],
        },
        reasoning="no-anomaly should stop after E1",
        confidence=0.9,
        risks=("LLM may attempt downstream RCA despite no anomaly",),
    )
    actual = _actual("c1")
    actual["detail"]["tool_sequence"] = ["parse_question", "detect_anomaly", "drilldown_dimension"]

    report = analyze_gaps([pred], [actual])

    assert report.gaps[0].divergence == DIVERGENCE_DESIGN_FLAW
    assert "forbidden" in report.gaps[0].detail


def test_gap_evidence_design_flaw_broken_chain() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="evidence",
        prediction={"chain": ["E1", "E2", "E3", "E4"]}, reasoning="", confidence=0.9,
    )
    report = analyze_gaps([pred], [_actual("c1", evidence_coverage=0.75)])
    assert report.gaps[0].divergence == DIVERGENCE_DESIGN_FLAW


def test_gap_evidence_overfit_predicted_incomplete() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="evidence",
        prediction={"chain": ["E1", "E2"], "evidence_coverage_full": False},
        reasoning="", confidence=0.5,
    )
    report = analyze_gaps([pred], [_actual("c1", evidence_coverage=1.0)])
    assert report.gaps[0].divergence == DIVERGENCE_OVERFIT


def test_gap_memory_design_flaw_unexpected_pollution() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="memory",
        prediction={"influence": "none", "pollution_risk": False},
        reasoning="", confidence=0.9,
    )
    report = analyze_gaps([pred], [_actual("c1", memory_pollution_ok=0)])
    assert report.gaps[0].divergence == DIVERGENCE_DESIGN_FLAW


def test_gap_memory_complexity_gap_expected_pollution() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="memory",
        prediction={"influence": "harmful", "pollution_risk": True},
        reasoning="", confidence=0.6,
    )
    report = analyze_gaps([pred], [_actual("c1", memory_pollution_ok=0)])
    assert report.gaps[0].divergence == DIVERGENCE_COMPLEXITY_GAP


def test_gap_memory_overfit() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="memory",
        prediction={"influence": "harmful", "pollution_risk": True},
        reasoning="", confidence=0.4,
    )
    report = analyze_gaps([pred], [_actual("c1", memory_pollution_ok=1)])
    assert report.gaps[0].divergence == DIVERGENCE_OVERFIT


def test_gap_outcome_correct() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="outcome",
        prediction={"root_cause_type": "campaign_traffic_drop", "top1_ok": True, "anomaly_ok": True},
        reasoning="", confidence=0.9,
    )
    report = analyze_gaps([pred], [_actual("c1")])
    assert report.gaps[0].divergence == DIVERGENCE_CORRECT


def test_gap_outcome_design_flaw_intent_failed() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="outcome",
        prediction={"root_cause_type": "campaign_traffic_drop", "top1_ok": True},
        reasoning="", confidence=0.9,
    )
    report = analyze_gaps([pred], [_actual("c1", intent_ok=0)])
    assert report.gaps[0].divergence == DIVERGENCE_DESIGN_FLAW


def test_gap_outcome_design_flaw_wrong_root_cause() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="outcome",
        prediction={"root_cause_type": "aov_drop", "top1_ok": True},
        reasoning="", confidence=0.7,
    )
    actual = _actual("c1", top1_ok=0, selected_candidate={
        "root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads",
    })
    report = analyze_gaps([pred], [actual])
    assert report.gaps[0].divergence == DIVERGENCE_DESIGN_FLAW


def test_gap_outcome_complexity_gap() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="outcome",
        prediction={"root_cause_type": "campaign_traffic_drop", "top1_ok": True},
        reasoning="", confidence=0.8,
    )
    report = analyze_gaps([pred], [_actual("c1", top1_ok=0)])
    assert report.gaps[0].divergence == DIVERGENCE_COMPLEXITY_GAP


def test_gap_outcome_overfit() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="outcome",
        prediction={"root_cause_type": "campaign_traffic_drop", "top1_ok": False},
        reasoning="", confidence=0.3,
    )
    report = analyze_gaps([pred], [_actual("c1", top1_ok=1)])
    assert report.gaps[0].divergence == DIVERGENCE_OVERFIT


def test_gap_outcome_null_top1_is_not_asserted() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="outcome",
        prediction={"root_cause_type": None, "top1_ok": None, "anomaly_ok": True},
        reasoning="no-anomaly outcome does not assert a root-cause candidate",
        confidence=0.8,
        risks=("no-anomaly report may contain root-cause content",),
    )
    actual = _actual("c1", top1_ok=1, anomaly_ok=1)
    actual["detail"]["selected_candidate"] = None

    report = analyze_gaps([pred], [actual])

    assert report.gaps[0].divergence == DIVERGENCE_CORRECT


def test_gap_missing_actual_case() -> None:
    pred = AspectPrediction(
        case_id="missing", aspect="outcome",
        prediction={"root_cause_type": "x"}, reasoning="", confidence=0.5,
    )
    report = analyze_gaps([pred], [_actual("c1")])
    assert report.gaps[0].divergence == DIVERGENCE_COMPLEXITY_GAP
    assert "no actual result" in report.gaps[0].detail


def test_gap_summary_aggregates() -> None:
    preds = [
        AspectPrediction(case_id="c1", aspect="intent", prediction={"metric_id": "gmv"}, reasoning="", confidence=0.9),
        AspectPrediction(case_id="c1", aspect="execution", prediction={"step_count": 5}, reasoning="", confidence=0.8),
        AspectPrediction(case_id="c1", aspect="outcome", prediction={"root_cause_type": "campaign_traffic_drop", "top1_ok": True}, reasoning="", confidence=0.9),
    ]
    actuals = [_actual("c1", trace_step_count=5)]
    report = analyze_gaps(preds, actuals, eval_id="test-1")

    assert report.eval_id == "test-1"
    assert report.summary["total"] == 3
    assert report.summary["accuracy"] > 0


def test_gap_report_markdown_output() -> None:
    preds = [
        AspectPrediction(case_id="c1", aspect="intent", prediction={"metric_id": "pay_cvr"}, reasoning="", confidence=0.9),
    ]
    report = analyze_gaps(preds, [_actual("c1")])
    md = format_markdown(report)
    assert "design_flaw" in md
    assert "c1" in md


def test_gap_report_markdown_accuracy_uses_nested_divergence_counts() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="intent",
        prediction={"metric_id": "gmv"},
        reasoning="metric explicit",
        confidence=0.9,
        risks=("parser may choose another metric",),
    )
    report = analyze_gaps([pred], [_actual("c1")], eval_id="eval-1")

    assert "**Accuracy**: 100% (1/1)" in format_markdown(report)


def test_gap_unknown_aspect_is_design_flaw() -> None:
    pred = AspectPrediction(
        case_id="c1", aspect="custom_stuff",
        prediction={"foo": 1}, reasoning="", confidence=0.5,
    )
    report = analyze_gaps([pred], [_actual("c1")])
    assert report.gaps[0].divergence == DIVERGENCE_DESIGN_FLAW
    assert "unknown aspect" in report.gaps[0].detail

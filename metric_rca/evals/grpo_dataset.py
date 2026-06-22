"""GRPO-ready trajectory export for MetricRCA eval runs."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

from metric_rca.evals.models import EvalCase, EvalRuntimeError, GroundTruth, PersistedArtifacts, RootCauseTruth
from metric_rca.evals.scorer import score_case


SCHEMA_VERSION = "metric-rca-grpo-v1"
DATASET_KIND = "metric_rca_eval_trajectory"


def write_grpo_dataset(
    *,
    output_dir: Path,
    eval_id: str,
    eval_suite: str,
    cases: list[EvalCase],
    ground_truth: dict[str, GroundTruth],
    case_scores: list[dict[str, Any]],
    memory_case_scores: list[dict[str, Any]],
    repository: Any,
    artifact_reader: Callable[[Any, str], PersistedArtifacts],
    require_predictions: bool = True,
) -> dict[str, Path]:
    """Persist memory and baseline eval trajectories as JSONL training records."""

    eval_dir = output_dir / eval_id
    dataset_dir = eval_dir / "grpo_dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    predictions_by_case = load_grpo_predictions(eval_dir / "predictions.jsonl", required=require_predictions)
    cases_by_id = {case.case_id: case for case in cases}

    records: list[dict[str, Any]] = []
    for phase, scores in (("memory", memory_case_scores), ("baseline", case_scores)):
        for score in scores:
            case_id = str(score["case_id"])
            run_id = _final_run_id(score)
            artifacts = artifact_reader(repository, run_id)
            records.append(
                build_grpo_trajectory(
                    eval_id=eval_id,
                    eval_suite=eval_suite,
                    phase=phase,
                    case=cases_by_id[case_id],
                    ground_truth=ground_truth[case_id],
                    score=score,
                    artifacts=artifacts,
                    predictions=predictions_by_case.get(case_id, []),
                )
            )

    trajectories_path = dataset_dir / "trajectories.jsonl"
    trajectories_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, default=str) + "\n" for record in records)
    )
    manifest = _manifest(eval_id=eval_id, eval_suite=eval_suite, records=records)
    manifest_path = dataset_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    return {"trajectories_path": trajectories_path, "manifest_path": manifest_path}


def build_grpo_trajectory(
    *,
    eval_id: str,
    eval_suite: str,
    phase: str,
    case: EvalCase,
    ground_truth: GroundTruth,
    score: dict[str, Any],
    artifacts: PersistedArtifacts,
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    artifact_score = _artifact_grounded_score(
        declared_score=score,
        case=case,
        ground_truth=ground_truth,
        artifacts=artifacts,
    )
    judge = _judge(score=artifact_score, ground_truth=ground_truth)
    return _jsonable(
        {
            "schema_version": SCHEMA_VERSION,
            "dataset_kind": DATASET_KIND,
            "eval_id": eval_id,
            "eval_suite": eval_suite,
            "phase": phase,
            "case": {
                "case_id": case.case_id,
                "question": case.question,
                "tags": list(case.tags),
                "scenario_family": _detail(artifact_score).get("scenario_family"),
            },
            "predictions": predictions,
            "ground_truth": _ground_truth_dict(ground_truth),
            "final_answer": _final_answer(score=artifact_score, artifacts=artifacts),
            "trajectory": {
                "run_id": _final_run_id(artifact_score),
                "agent_run": artifacts.agent_run,
                "trace_steps": artifacts.trace_steps,
                "evidences": artifacts.evidences,
                "sql_audit": artifacts.sql_audit,
                "operation_tasks": artifacts.tasks,
                "report": artifacts.report,
                "memory_records": artifacts.memory_records,
            },
            "judge": judge,
            "diagnostics": {
                key: value
                for key, value in artifact_score.items()
                if key not in {"detail"}
            },
            "detail": _detail(artifact_score),
        }
    )


def _artifact_grounded_score(
    *,
    declared_score: dict[str, Any],
    case: EvalCase,
    ground_truth: GroundTruth,
    artifacts: PersistedArtifacts,
) -> dict[str, Any]:
    recomputed = score_case(case_id=case.case_id, ground_truth=ground_truth, artifacts=artifacts)
    _validate_score_artifact_consistency(declared_score=declared_score, recomputed_score=recomputed)
    declared_detail = _detail(declared_score)
    merged_detail = dict(_detail(recomputed))
    for key in ("final_run_id", "scenario_family", "eval_attempts", "memory_enabled", "tags"):
        if key in declared_detail:
            merged_detail[key] = declared_detail[key]
    return {**recomputed, "detail": merged_detail}


_CRITICAL_SCORE_FIELDS = (
    "intent_ok",
    "anomaly_ok",
    "top1_ok",
    "top3_ok",
    "dominant_top1_ok",
    "root_cause_set_recall",
    "root_cause_set_precision",
    "weighted_explanation_coverage",
    "top3_contains_all_major_causes",
    "evidence_coverage",
    "sql_safe",
    "reflection_repair_ok",
    "report_traceable_ok",
    "memory_pollution_ok",
    "no_anomaly_task_ok",
)


def _validate_score_artifact_consistency(
    *,
    declared_score: dict[str, Any],
    recomputed_score: dict[str, Any],
) -> None:
    for field in _CRITICAL_SCORE_FIELDS:
        declared = declared_score.get(field)
        recomputed = recomputed_score.get(field)
        if isinstance(declared, float) or isinstance(recomputed, float):
            if abs(float(declared or 0.0) - float(recomputed or 0.0)) > 0.000001:
                raise EvalRuntimeError(
                    "GRPO_SCORE_ARTIFACT_MISMATCH",
                    f"{declared_score.get('case_id')} field {field}: declared={declared} recomputed={recomputed}",
                )
        elif declared != recomputed:
            raise EvalRuntimeError(
                "GRPO_SCORE_ARTIFACT_MISMATCH",
                f"{declared_score.get('case_id')} field {field}: declared={declared} recomputed={recomputed}",
            )


def _judge(*, score: dict[str, Any], ground_truth: GroundTruth) -> dict[str, Any]:
    failed_gates: list[str] = []
    if int(score.get("intent_ok", 0)) != 1:
        failed_gates.append("intent")
    if int(score.get("anomaly_ok", 0)) != 1:
        failed_gates.append("anomaly")
    if ground_truth.expected_anomaly:
        if int(score.get("dominant_top1_ok", 0)) != 1:
            failed_gates.append("dominant_top1")
        if _requires_multi_cause_set(ground_truth):
            if float(score.get("weighted_explanation_coverage", 0.0)) < 0.85:
                failed_gates.append("weighted_explanation_coverage")
            if int(score.get("top3_contains_all_major_causes", 0)) != 1:
                failed_gates.append("top3_contains_all_major_causes")
    else:
        if int(score.get("no_anomaly_task_ok", 0)) != 1:
            failed_gates.append("no_anomaly_task")
    if float(score.get("evidence_coverage", 0.0)) != 1.0:
        failed_gates.append("evidence_coverage")
    if int(score.get("sql_safe", 0)) != 1:
        failed_gates.append("sql_safe")
    if int(score.get("reflection_repair_ok", 0)) != 1:
        failed_gates.append("reflection_repair")
    if int(score.get("report_traceable_ok", 0)) != 1:
        failed_gates.append("report_traceable")
    if int(score.get("memory_pollution_ok", 0)) != 1:
        failed_gates.append("memory_pollution")
    reward = 1.0 if not failed_gates else 0.0
    return {
        "judge_name": "deterministic_ground_truth_artifact_judge",
        "reward": reward,
        "reward_scale": "binary_0_1",
        "reward_basis": "final_answer_matches_ground_truth_and_is_supported_by_current_run_artifacts",
        "failed_gates": failed_gates,
        "subrewards": {
            "intent": float(score.get("intent_ok", 0)),
            "anomaly": float(score.get("anomaly_ok", 0)),
            "dominant_top1": float(score.get("dominant_top1_ok", 0)),
            "root_cause_set_recall": float(score.get("root_cause_set_recall", 0.0)),
            "root_cause_set_precision": float(score.get("root_cause_set_precision", 0.0)),
            "weighted_explanation_coverage": float(score.get("weighted_explanation_coverage", 0.0)),
            "top3_contains_all_major_causes": float(score.get("top3_contains_all_major_causes", 0)),
            "evidence_coverage": float(score.get("evidence_coverage", 0.0)),
            "sql_safe": float(score.get("sql_safe", 0)),
            "report_traceable": float(score.get("report_traceable_ok", 0)),
            "memory_pollution": float(score.get("memory_pollution_ok", 0)),
        },
    }


def _requires_multi_cause_set(ground_truth: GroundTruth) -> bool:
    major_causes = [cause for cause in ground_truth.root_causes if float(cause.weight) >= 0.20]
    return len(major_causes) > 1


def _final_answer(*, score: dict[str, Any], artifacts: PersistedArtifacts) -> dict[str, Any]:
    detail = _detail(score)
    selected_candidate = detail.get("selected_candidate")
    return {
        "status": detail.get("status"),
        "metric_id": detail.get("metric_id"),
        "selected_candidate": selected_candidate,
        "report": artifacts.report,
        "evidence_ids": _final_evidence_ids(selected_candidate, artifacts.report),
    }


def _final_evidence_ids(selected_candidate: Any, report: dict[str, Any] | None) -> list[str]:
    if isinstance(selected_candidate, dict) and isinstance(selected_candidate.get("evidence_ids"), list):
        return [str(item) for item in selected_candidate["evidence_ids"]]
    if isinstance(report, dict) and isinstance(report.get("evidence_ids"), list):
        return [str(item) for item in report["evidence_ids"]]
    return []


def load_grpo_predictions(path: Path, *, required: bool) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        if required:
            raise EvalRuntimeError("GRPO_PREDICTIONS_MISSING", str(path))
        return {}
    predictions: dict[str, list[dict[str, Any]]] = {}
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"prediction line {line_number} must be an object")
        case_id = payload.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"prediction line {line_number} missing case_id")
        predictions.setdefault(case_id, []).append(payload)
    return predictions


def _ground_truth_dict(ground_truth: GroundTruth) -> dict[str, Any]:
    return {
        "case_id": ground_truth.case_id,
        "business_date": ground_truth.business_date,
        "metric_id": ground_truth.metric_id,
        "expected_anomaly": ground_truth.expected_anomaly,
        "root_cause_type": ground_truth.root_cause_type,
        "dimension": ground_truth.dimension,
        "element": ground_truth.element,
        "root_causes": [_truth_dict(cause) for cause in _expected_causes(ground_truth)],
    }


def _expected_causes(ground_truth: GroundTruth) -> tuple[RootCauseTruth, ...]:
    if ground_truth.root_causes:
        return ground_truth.root_causes
    if not ground_truth.expected_anomaly:
        return ()
    return (
        RootCauseTruth(
            root_cause_type=str(ground_truth.root_cause_type),
            dimension=ground_truth.dimension,
            element=ground_truth.element,
            weight=1.0,
        ),
    )


def _truth_dict(cause: RootCauseTruth) -> dict[str, Any]:
    return {
        "root_cause_type": cause.root_cause_type,
        "dimension": cause.dimension,
        "element": cause.element,
        "weight": cause.weight,
    }


def _manifest(*, eval_id: str, eval_suite: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    rewards = [float(record["judge"]["reward"]) for record in records]
    phases: dict[str, int] = {}
    for record in records:
        phase = str(record["phase"])
        phases[phase] = phases.get(phase, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_kind": DATASET_KIND,
        "eval_id": eval_id,
        "eval_suite": eval_suite,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "phases": phases,
        "reward_rate": round(sum(rewards) / len(rewards), 6) if rewards else 0.0,
    }


def _final_run_id(score: dict[str, Any]) -> str:
    run_id = _detail(score).get("final_run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("score detail missing final_run_id")
    return run_id


def _detail(score: dict[str, Any]) -> dict[str, Any]:
    detail = score.get("detail")
    return detail if isinstance(detail, dict) else {}


def _jsonable(value: Any) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))

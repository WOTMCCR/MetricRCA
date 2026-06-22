"""Export PTV cycles into three strictly validated, redacted GRPO layers."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable, Mapping

from metric_rca.evals.grpo_manifest import build_manifest
from metric_rca.evals.grpo_redaction import redact_record
from metric_rca.evals.grpo_dataset import DATASET_KIND as RUNNER_GRPO_DATASET_KIND
from metric_rca.evals.grpo_dataset import SCHEMA_VERSION as RUNNER_GRPO_SCHEMA_VERSION
from metric_rca.evals.grpo_rewards import (
    assess_coding_fix,
    coding_fix_reward,
    controller_reward,
    prediction_reward,
    task_trajectory_reward,
)
from metric_rca.evals.grpo_schema import TrajectoryLayer, TrajectoryRecord
from metric_rca.evals.ptv_artifacts import read_json, read_jsonl, write_json_atomic, write_jsonl_atomic


_CODE_REFERENCE_RE = re.compile(
    r"(?:[A-Za-z0-9_./-]+\.py(?::\d+)?|[A-Za-z_][A-Za-z0-9_.]+\([A-Za-z0-9_, =]*\))"
)
_TASK_TRAJECTORY_KEYS = {
    "schema_version",
    "dataset_kind",
    "eval_id",
    "eval_suite",
    "phase",
    "case",
    "predictions",
    "ground_truth",
    "final_answer",
    "trajectory",
    "judge",
    "diagnostics",
    "detail",
}
_TASK_MANIFEST_KEYS = {
    "schema_version",
    "dataset_kind",
    "eval_id",
    "eval_suite",
    "created_at",
    "record_count",
    "phases",
    "reward_rate",
}
_PREDICTION_DIVERGENCES = {"correct", "complexity_gap", "design_flaw", "overfit"}


class GrpoExportError(RuntimeError):
    def __init__(self, code: str, message: str, *, context: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.context = dict(context or {})
        super().__init__(f"{code}: {message}")

    def as_dict(self) -> dict[str, Any]:
        return {"error_code": self.code, "message": self.message, "context": self.context}


def export_cycle(
    *,
    cycle_dir: Path,
    output_dir: Path | None = None,
    repo_root: Path = Path("."),
    from_round: int | None = None,
    to_round: int | None = None,
) -> dict[str, Path]:
    cycle_meta = read_json(cycle_dir / "meta.json")
    cycle_id = str(cycle_meta.get("cycle_id", cycle_dir.name))
    round_dirs = _round_dirs(cycle_dir, from_round=from_round, to_round=to_round)
    if not round_dirs:
        raise GrpoExportError("GRPO_ROUNDS_MISSING", "no complete PTV rounds were found")
    target = output_dir or cycle_dir / "grpo_export"
    target.mkdir(parents=True, exist_ok=True)

    records_by_layer: dict[str, list[dict[str, Any]]] = {
        TrajectoryLayer.CONTROLLER.value: [],
        TrajectoryLayer.SUB_AGENT.value: [],
        TrajectoryLayer.CODING_FIX.value: [],
    }
    redaction_count = 0
    prior_round: dict[str, Any] | None = None
    for round_dir in round_dirs:
        context = _load_round(round_dir)
        controller_record = _controller_record(
            cycle_id=cycle_id,
            context=context,
            prior_context=prior_round,
        )
        redacted, count = _redacted_record(controller_record)
        records_by_layer[TrajectoryLayer.CONTROLLER.value].append(redacted)
        redaction_count += count

        for record in _sub_agent_records(cycle_id=cycle_id, context=context):
            redacted, count = _redacted_record(record)
            records_by_layer[TrajectoryLayer.SUB_AGENT.value].append(redacted)
            redaction_count += count

        if prior_round is not None and context["lineage"].get("fix_commit"):
            coding_record = _coding_fix_record(
                cycle_id=cycle_id,
                context=context,
                prior_context=prior_round,
                repo_root=repo_root,
            )
            redacted, count = _redacted_record(coding_record)
            records_by_layer[TrajectoryLayer.CODING_FIX.value].append(redacted)
            redaction_count += count
        prior_round = context

    output_files = {
        "layer1_controller": target / "layer1_controller.jsonl",
        "layer2_sub_agent": target / "layer2_sub_agent.jsonl",
        "layer3_coding_fix": target / "layer3_coding_fix.jsonl",
    }
    for layer, path in (
        (TrajectoryLayer.CONTROLLER.value, output_files["layer1_controller"]),
        (TrajectoryLayer.SUB_AGENT.value, output_files["layer2_sub_agent"]),
        (TrajectoryLayer.CODING_FIX.value, output_files["layer3_coding_fix"]),
    ):
        write_jsonl_atomic(path, records_by_layer[layer])

    positive_records = [
        record
        for records in records_by_layer.values()
        for record in records
        if bool(record["reward"]["eligible_for_positive"]) and float(record["reward"]["total"]) > 0.0
    ]
    positive_path = target / "positive_records.jsonl"
    write_jsonl_atomic(positive_path, positive_records)
    output_files["positive_records"] = positive_path

    manifest = build_manifest(
        cycle_id=cycle_id,
        records_by_layer=records_by_layer,
        output_files=output_files,
        redaction_count=redaction_count,
    )
    manifest_path = target / "manifest.json"
    write_json_atomic(manifest_path, manifest)
    output_files["manifest"] = manifest_path
    return output_files


def _load_round(round_dir: Path) -> dict[str, Any]:
    required = (
        "summary.json",
        "optimization_summary.json",
        "commit_lineage.json",
        "eval-result.json",
        "predictions.jsonl",
        "gap_report.json",
        "diagnosis.jsonl",
    )
    missing = [name for name in required if not (round_dir / name).exists()]
    if missing:
        raise GrpoExportError(
            "GRPO_ROUND_INCOMPLETE",
            "round is missing required PTV artifacts",
            context={"round_dir": str(round_dir), "missing": missing},
        )
    summary = read_json(round_dir / "summary.json")
    optimization = read_json(round_dir / "optimization_summary.json")
    lineage = read_json(round_dir / "commit_lineage.json")
    eval_result = read_json(round_dir / "eval-result.json")
    predictions = read_jsonl(round_dir / "predictions.jsonl")
    gap_report = read_json(round_dir / "gap_report.json")
    diagnosis = read_jsonl(round_dir / "diagnosis.jsonl", allow_empty=True)
    return {
        "round_dir": round_dir,
        "round": int(summary["round"]),
        "summary": summary,
        "optimization": optimization,
        "lineage": lineage,
        "eval_result": eval_result,
        "predictions": predictions,
        "gap_report": gap_report,
        "diagnosis": diagnosis,
    }


def _controller_record(
    *,
    cycle_id: str,
    context: Mapping[str, Any],
    prior_context: Mapping[str, Any] | None,
) -> TrajectoryRecord:
    optimization = context["optimization"]
    diagnosis_categories = {
        str(row.get("fix_category"))
        for row in context["diagnosis"]
        if row.get("fix_category") not in {None, "", "NO-FIX"}
    }
    selected = optimization.get("selected_fix_category")
    selected_supported = selected is None or str(selected) in diagnosis_categories or bool(context["summary"].get("metricrca_gates_passed"))
    rules = optimization.get("controller_rules_applied")
    required_rule_keys = {
        "rule_c1_blocked_categories",
        "rule_c2_promoted",
        "rule_c3_discovery_priority",
        "rule_c4_revert_assessment",
        "rule_c5_streak_counts",
    }
    rules_valid = isinstance(rules, Mapping) and required_rule_keys.issubset(rules)
    before_metrics = prior_context["summary"].get("metrics_after", {}) if prior_context else {}
    after_metrics = context["summary"].get("metrics_after", {})
    reward = controller_reward(
        before_metrics=before_metrics,
        after_metrics=after_metrics,
        controller_rules_valid=rules_valid,
        selected_category_supported=selected_supported,
    )
    return TrajectoryRecord(
        trajectory_id=_trajectory_id(cycle_id, context["round"], "controller"),
        layer=TrajectoryLayer.CONTROLLER,
        cycle_id=cycle_id,
        round=context["round"],
        source={
            "round_dir": str(context["round_dir"]),
            "eval_code_commit": context["lineage"].get("eval_code_commit"),
            "fix_commit": context["lineage"].get("fix_commit"),
        },
        input={
            "optimization_context": {
                "metrics_before": before_metrics,
                "metrics_after": after_metrics,
                "gap_summary": optimization.get("gap_summary", {}),
                "remaining_gaps": optimization.get("remaining_gaps", []),
            }
        },
        trajectory={
            "controller_rules": rules,
            "diagnosis_categories": sorted(diagnosis_categories),
        },
        output={
            "decision": {
                "selected_fix_category": selected,
                "selected_layer": optimization.get("selected_layer"),
                "justification": optimization.get("controller_justification"),
                "formal_two_green": optimization.get("formal_two_green"),
            }
        },
        reward=reward,
        metadata={"trajectory_type": "controller_optimization_context"},
    )


def _sub_agent_records(*, cycle_id: str, context: Mapping[str, Any]) -> list[TrajectoryRecord]:
    records: list[TrajectoryRecord] = []
    task_path, task_manifest_path = _runner_trajectory_artifacts(context)
    task_rows = read_jsonl(task_path)
    _validate_task_trajectory_manifest(
        task_path=task_path,
        manifest_path=task_manifest_path,
        rows=task_rows,
        context=context,
    )
    for index, row in enumerate(task_rows):
        if not isinstance(row, Mapping):
            raise GrpoExportError(
                "GRPO_TASK_TRAJECTORY_INVALID",
                "task trajectory row must be an object",
                context={"path": str(task_path), "index": index},
            )
        diagnostics = _required_mapping(row, "diagnostics", path=task_path, index=index)
        reward = task_trajectory_reward(diagnostics)
        case = _required_mapping(row, "case", path=task_path, index=index)
        trajectory = _required_mapping(row, "trajectory", path=task_path, index=index)
        final_answer = _required_mapping(row, "final_answer", path=task_path, index=index)
        judge = _required_mapping(row, "judge", path=task_path, index=index)
        eval_id = _required_string(row, "eval_id", path=task_path, index=index)
        phase = _required_string(row, "phase", path=task_path, index=index)
        case_id = _required_string(case, "case_id", path=task_path, index=index)
        question = _required_string(case, "question", path=task_path, index=index)
        tags = case.get("tags")
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise GrpoExportError(
                "GRPO_TASK_TRAJECTORY_INVALID",
                "task trajectory case.tags must be a string list",
                context={"path": str(task_path), "index": index, "case_id": case_id},
            )
        records.append(
            TrajectoryRecord(
                trajectory_id=_trajectory_id(cycle_id, context["round"], f"task-{phase}-{case_id}-{index}"),
                layer=TrajectoryLayer.SUB_AGENT,
                cycle_id=cycle_id,
                round=context["round"],
                source={
                    "round_dir": str(context["round_dir"]),
                    "runner_trajectory_path": str(task_path),
                    "eval_id": eval_id,
                    "run_id": trajectory.get("run_id"),
                },
                input={
                    "case_id": case_id,
                    "trajectory_type": "product_task",
                    "question": question,
                    "tags": tags,
                    "phase": phase,
                    "ground_truth_ref": {
                        "case_id": case_id,
                        "scenario_family": case.get("scenario_family"),
                    },
                },
                trajectory={"steps": trajectory},
                output={
                    "result": {
                        "final_answer": final_answer,
                        "judge": judge,
                        "diagnostics": diagnostics,
                    }
                },
                reward=reward,
                metadata={"trajectory_type": "product_task", "phase": phase},
            )
        )

    gaps_by_key = _validated_gap_rows(context)
    for index, prediction in enumerate(context["predictions"]):
        case_id = _required_artifact_string(prediction, "case_id", artifact="predictions.jsonl", index=index)
        aspect = _required_artifact_string(prediction, "aspect", artifact="predictions.jsonl", index=index)
        reasoning = _required_artifact_string(prediction, "reasoning", artifact="predictions.jsonl", index=index)
        prediction_payload = _required_artifact_mapping(prediction, "prediction", artifact="predictions.jsonl", index=index)
        risks = prediction.get("risks")
        if not isinstance(risks, list) or any(not isinstance(risk, str) or not risk.strip() for risk in risks):
            raise GrpoExportError(
                "GRPO_PREDICTION_INVALID",
                "prediction risks must be a list of non-empty strings",
                context={"artifact": "predictions.jsonl", "index": index, "case_id": case_id, "aspect": aspect},
            )
        gap = gaps_by_key.get((case_id, aspect))
        if gap is None:
            raise GrpoExportError(
                "GRPO_PREDICTION_GAP_MISSING",
                "prediction has no corresponding gap row",
                context={"case_id": case_id, "aspect": aspect},
            )
        divergence = gap["divergence"]
        reward = prediction_reward(
            divergence=divergence,
            reasoning_has_code_reference=_CODE_REFERENCE_RE.search(reasoning) is not None,
        )
        records.append(
            TrajectoryRecord(
                trajectory_id=_trajectory_id(cycle_id, context["round"], f"prediction-{case_id}-{aspect}-{index}"),
                layer=TrajectoryLayer.SUB_AGENT,
                cycle_id=cycle_id,
                round=context["round"],
                source={
                    "round_dir": str(context["round_dir"]),
                    "prediction_artifact": "predictions.jsonl",
                    "gap_artifact": "gap_report.json",
                },
                input={
                    "case_id": case_id,
                    "trajectory_type": "prediction",
                    "aspect": aspect,
                },
                trajectory={
                    "steps": {
                        "prediction": prediction_payload,
                        "reasoning": reasoning,
                        "risks": risks,
                        "confidence": prediction.get("confidence"),
                    }
                },
                output={
                    "result": {
                        "divergence": divergence,
                        "actual": gap["actual"],
                        "detail": gap.get("detail"),
                    }
                },
                reward=reward,
                metadata={
                    "trajectory_type": "prediction",
                    "prediction_divergence": divergence,
                    "aspect": aspect,
                },
            )
        )
    return records


def _validated_gap_rows(context: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    gaps = context["gap_report"].get("gaps")
    if not isinstance(gaps, list):
        raise GrpoExportError("GRPO_GAP_REPORT_INVALID", "gap_report.gaps must be a list")
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, row in enumerate(gaps):
        if not isinstance(row, Mapping):
            raise GrpoExportError(
                "GRPO_GAP_REPORT_INVALID",
                "gap rows must be objects",
                context={"artifact": "gap_report.json", "index": index},
            )
        case_id = _required_artifact_string(row, "case_id", artifact="gap_report.json", index=index)
        aspect = _required_artifact_string(row, "aspect", artifact="gap_report.json", index=index)
        divergence = _required_artifact_string(row, "divergence", artifact="gap_report.json", index=index)
        if divergence not in _PREDICTION_DIVERGENCES:
            raise GrpoExportError(
                "GRPO_GAP_REPORT_INVALID",
                "gap divergence is unknown",
                context={"artifact": "gap_report.json", "index": index, "divergence": divergence},
            )
        actual = row.get("actual")
        if not isinstance(actual, Mapping):
            raise GrpoExportError(
                "GRPO_GAP_REPORT_INVALID",
                "gap actual must be an object",
                context={"artifact": "gap_report.json", "index": index, "case_id": case_id, "aspect": aspect},
            )
        key = (case_id, aspect)
        if key in result:
            raise GrpoExportError(
                "GRPO_GAP_REPORT_INVALID",
                "duplicate gap row for case/aspect",
                context={"artifact": "gap_report.json", "case_id": case_id, "aspect": aspect},
            )
        result[key] = row
    return result


def _required_artifact_string(row: Mapping[str, Any], field: str, *, artifact: str, index: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GrpoExportError(
            "GRPO_PREDICTION_INVALID" if artifact == "predictions.jsonl" else "GRPO_GAP_REPORT_INVALID",
            f"{artifact} field {field} must be a non-empty string",
            context={"artifact": artifact, "index": index, "field": field},
        )
    return value.strip()


def _required_artifact_mapping(row: Mapping[str, Any], field: str, *, artifact: str, index: int) -> Mapping[str, Any]:
    value = row.get(field)
    if not isinstance(value, Mapping) or not value:
        raise GrpoExportError(
            "GRPO_PREDICTION_INVALID" if artifact == "predictions.jsonl" else "GRPO_GAP_REPORT_INVALID",
            f"{artifact} field {field} must be a non-empty object",
            context={"artifact": artifact, "index": index, "field": field},
        )
    return value


def _required_mapping(row: Mapping[str, Any], field: str, *, path: Path, index: int) -> Mapping[str, Any]:
    value = row.get(field)
    if not isinstance(value, Mapping) or not value:
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_INVALID",
            f"task trajectory field {field} must be a non-empty object",
            context={"path": str(path), "index": index, "field": field},
        )
    return value


def _required_string(row: Mapping[str, Any], field: str, *, path: Path, index: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_INVALID",
            f"task trajectory field {field} must be a non-empty string",
            context={"path": str(path), "index": index, "field": field},
        )
    return value


def _coding_fix_record(
    *,
    cycle_id: str,
    context: Mapping[str, Any],
    prior_context: Mapping[str, Any],
    repo_root: Path,
) -> TrajectoryRecord:
    commit = str(context["lineage"]["fix_commit"])
    git_diff, changed_files = _git_diff(repo_root=repo_root, commit=commit)
    actionable = _actionable_diagnosis(prior_context)
    targeted_cases = sorted({str(row.get("case_id")) for row in actionable if row.get("case_id")})
    proposed_files = sorted({str(path) for row in actionable for path in row["proposed_fix"]["files"]})
    before_eval = prior_context["eval_result"]
    after_eval = context["eval_result"]
    assessment = assess_coding_fix(
        before_summary=before_eval.get("summary", {}),
        after_summary=after_eval.get("summary", {}),
        before_cases=before_eval.get("cases", []),
        after_cases=after_eval.get("cases", []),
        targeted_cases=targeted_cases,
        changed_files=changed_files,
        proposed_files=proposed_files,
    )
    reward = coding_fix_reward(assessment)
    return TrajectoryRecord(
        trajectory_id=_trajectory_id(cycle_id, context["round"], f"coding-fix-{commit}"),
        layer=TrajectoryLayer.CODING_FIX,
        cycle_id=cycle_id,
        round=context["round"],
        source={
            "round_dir": str(context["round_dir"]),
            "before_round_dir": str(prior_context["round_dir"]),
            "fix_commit": commit,
            "eval_code_commit": context["lineage"].get("eval_code_commit"),
        },
        input={
            "diagnosis": actionable,
            "before": {
                "summary": before_eval.get("summary", {}),
                "case_results": before_eval.get("cases", []),
            },
        },
        trajectory={
            "git_diff": git_diff,
            "changed_files": changed_files,
            "proposed_files": proposed_files,
        },
        output={
            "after": {
                "summary": after_eval.get("summary", {}),
                "case_results": after_eval.get("cases", []),
            },
            "fix_assessment": assessment.as_dict(),
        },
        reward=reward,
        metadata={
            "trajectory_type": "coding_fix",
            "selected_fix_category": context["optimization"].get("selected_fix_category"),
        },
    )


def _actionable_diagnosis(prior_context: Mapping[str, Any]) -> list[dict[str, Any]]:
    actionable: list[dict[str, Any]] = []
    for index, row in enumerate(prior_context["diagnosis"]):
        if not isinstance(row, Mapping) or row.get("fix_category") in {None, "", "NO-FIX"}:
            continue
        case_id = row.get("case_id")
        fix_category = row.get("fix_category")
        proposed_fix = row.get("proposed_fix")
        files = proposed_fix.get("files") if isinstance(proposed_fix, Mapping) else None
        if (
            not isinstance(case_id, str)
            or not case_id.strip()
            or not isinstance(fix_category, str)
            or not fix_category.strip()
            or not isinstance(files, list)
            or not files
            or any(not isinstance(path, str) or not path.strip() for path in files)
        ):
            raise GrpoExportError(
                "GRPO_FIX_DIAGNOSIS_INVALID",
                "actionable diagnosis rows must declare case_id, fix_category, and proposed_fix.files",
                context={"round_dir": str(prior_context["round_dir"]), "index": index},
            )
        actionable.append(dict(row))
    if not actionable:
        raise GrpoExportError(
            "GRPO_FIX_DIAGNOSIS_MISSING",
            "a fix round requires at least one actionable diagnosis row from the prior round",
            context={"prior_round_dir": str(prior_context["round_dir"])},
        )
    return actionable


def _runner_trajectory_artifacts(context: Mapping[str, Any]) -> tuple[Path, Path]:
    round_dir = Path(context["round_dir"])
    eval_id = str(context["eval_result"].get("eval_id"))
    candidates = (
        round_dir / eval_id / "grpo_dataset" / "trajectories.jsonl",
        round_dir / "grpo_dataset" / "trajectories.jsonl",
        round_dir / "runner_grpo_dataset" / "trajectories.jsonl",
    )
    for candidate in candidates:
        if candidate.exists():
            manifest_path = candidate.with_name("manifest.json")
            if not manifest_path.exists():
                raise GrpoExportError(
                    "GRPO_TASK_TRAJECTORY_MANIFEST_INVALID",
                    "task trajectory manifest is required next to trajectories.jsonl",
                    context={"trajectory_path": str(candidate), "manifest_path": str(manifest_path)},
                )
            return candidate, manifest_path
    raise GrpoExportError(
        "GRPO_TASK_TRAJECTORY_MISSING",
        "existing artifact-grounded task trajectories are required for Layer 2",
        context={"candidates": [str(path) for path in candidates]},
    )


def _validate_task_trajectory_manifest(
    *,
    task_path: Path,
    manifest_path: Path,
    rows: list[dict[str, Any]],
    context: Mapping[str, Any],
) -> None:
    manifest = read_json(manifest_path)
    missing = sorted(_TASK_MANIFEST_KEYS - set(manifest))
    extra = sorted(set(manifest) - _TASK_MANIFEST_KEYS)
    if missing or extra:
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_MANIFEST_INVALID",
            "task trajectory manifest keys do not match the v1 schema",
            context={"manifest_path": str(manifest_path), "missing": missing, "extra": extra},
        )
    expected_eval_id = context["eval_result"].get("eval_id")
    expected_suite = context["eval_result"].get("summary", {}).get("eval_suite")
    if (
        manifest.get("schema_version") != RUNNER_GRPO_SCHEMA_VERSION
        or manifest.get("dataset_kind") != RUNNER_GRPO_DATASET_KIND
        or manifest.get("eval_id") != expected_eval_id
        or manifest.get("eval_suite") != expected_suite
    ):
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_MANIFEST_INVALID",
            "task trajectory manifest identity does not match eval result",
            context={"manifest_path": str(manifest_path)},
        )
    if not isinstance(manifest.get("record_count"), int) or isinstance(manifest.get("record_count"), bool):
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_MANIFEST_INVALID",
            "task trajectory manifest record_count must be an integer",
            context={"manifest_path": str(manifest_path), "record_count": manifest.get("record_count")},
        )
    if manifest["record_count"] != len(rows):
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_MANIFEST_INVALID",
            "task trajectory manifest record_count does not match trajectories.jsonl",
            context={"manifest_path": str(manifest_path), "record_count": manifest["record_count"], "actual": len(rows)},
        )
    phases: dict[str, int] = {}
    for index, row in enumerate(rows):
        _validate_task_trajectory_row(row, task_path=task_path, index=index, expected_eval_id=expected_eval_id, expected_suite=expected_suite)
        phase = str(row["phase"])
        phases[phase] = phases.get(phase, 0) + 1
    if manifest.get("phases") != phases:
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_MANIFEST_INVALID",
            "task trajectory manifest phases do not match trajectories.jsonl",
            context={"manifest_path": str(manifest_path), "expected": phases, "actual": manifest.get("phases")},
        )
    if not isinstance(manifest.get("reward_rate"), (int, float)) or isinstance(manifest.get("reward_rate"), bool):
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_MANIFEST_INVALID",
            "task trajectory manifest reward_rate must be numeric",
            context={"manifest_path": str(manifest_path)},
        )


def _validate_task_trajectory_row(
    row: Mapping[str, Any],
    *,
    task_path: Path,
    index: int,
    expected_eval_id: Any,
    expected_suite: Any,
) -> None:
    missing = sorted(_TASK_TRAJECTORY_KEYS - set(row))
    extra = sorted(set(row) - _TASK_TRAJECTORY_KEYS)
    if missing or extra:
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_INVALID",
            "task trajectory row keys do not match the v1 schema",
            context={"path": str(task_path), "index": index, "missing": missing, "extra": extra},
        )
    if (
        row.get("schema_version") != RUNNER_GRPO_SCHEMA_VERSION
        or row.get("dataset_kind") != RUNNER_GRPO_DATASET_KIND
        or row.get("eval_id") != expected_eval_id
        or row.get("eval_suite") != expected_suite
    ):
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_INVALID",
            "task trajectory identity does not match eval result",
            context={"path": str(task_path), "index": index},
        )
    for field in ("case", "trajectory", "final_answer", "judge", "diagnostics", "detail"):
        if not isinstance(row.get(field), Mapping):
            raise GrpoExportError(
                "GRPO_TASK_TRAJECTORY_INVALID",
                "task trajectory structured fields must be objects",
                context={"path": str(task_path), "index": index, "field": field},
            )
    if not isinstance(row.get("predictions"), list):
        raise GrpoExportError(
            "GRPO_TASK_TRAJECTORY_INVALID",
            "task trajectory predictions must be a list",
            context={"path": str(task_path), "index": index},
        )


def _git_diff(*, repo_root: Path, commit: str) -> tuple[str, list[str]]:
    diff = _git(repo_root, ("show", "--format=", "--binary", commit))
    files = _git(repo_root, ("diff-tree", "--no-commit-id", "--name-only", "-r", commit))
    changed_files = [line.strip() for line in files.splitlines() if line.strip()]
    if not git_diff_is_meaningful(diff) or not changed_files:
        raise GrpoExportError(
            "GRPO_GIT_DIFF_EMPTY",
            "fix commit must have a non-empty diff and changed-file list",
            context={"commit": commit},
        )
    return diff, changed_files


def git_diff_is_meaningful(diff: str) -> bool:
    return "diff --git " in diff and len(diff.strip()) > 20


def _git(repo_root: Path, args: tuple[str, ...]) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise GrpoExportError(
            "GRPO_GIT_COMMAND_FAILED",
            "git command failed while linking diagnosis to code",
            context={"argv": ["git", *args], "stderr": completed.stderr.strip()},
        )
    return completed.stdout


def _round_dirs(cycle_dir: Path, *, from_round: int | None, to_round: int | None) -> list[Path]:
    result = []
    for path in sorted(cycle_dir.glob("round-*")):
        try:
            number = int(path.name.split("-", maxsplit=1)[1])
        except (IndexError, ValueError) as exc:
            raise GrpoExportError("GRPO_ROUND_DIR_INVALID", "invalid round directory", context={"path": str(path)}) from exc
        if from_round is not None and number < from_round:
            continue
        if to_round is not None and number > to_round:
            continue
        if path.is_dir():
            result.append(path)
    return result


def _redacted_record(record: TrajectoryRecord) -> tuple[dict[str, Any], int]:
    payload = record.as_dict()
    result = redact_record(payload)
    if not isinstance(result.value, dict):
        raise GrpoExportError("GRPO_REDACTION_INVALID", "redacted trajectory must remain an object")
    return result.value, result.redaction_count


def _trajectory_id(cycle_id: str, round_number: int, suffix: str) -> str:
    material = f"{cycle_id}|{round_number}|{suffix}"
    digest = sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"grpo-{round_number:02d}-{digest}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export a MetricRCA PTV cycle to three-layer GRPO records")
    parser.add_argument("--cycle-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--from-round", type=int)
    parser.add_argument("--to-round", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths = export_cycle(
            cycle_dir=args.cycle_dir,
            output_dir=args.output_dir,
            repo_root=args.repo_root.resolve(),
            from_round=args.from_round,
            to_round=args.to_round,
        )
    except (GrpoExportError, ValueError) as exc:
        if isinstance(exc, GrpoExportError):
            payload = exc.as_dict()
        else:
            payload = {"error_code": "GRPO_EXPORT_INVALID", "message": str(exc), "context": {}}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({name: str(path) for name, path in paths.items()}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

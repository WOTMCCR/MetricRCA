"""Executable Predict -> Test -> Verify controller for MetricRCA.

The module orchestrates external prediction/eval/analyst commands without
introducing a fallback execution path. Every command is explicit, every failure
is typed, and every artifact is persisted under one canonical round directory.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any

from metric_rca.evals.gap_analyzer import analyze_gaps
from metric_rca.evals.prediction import load_predictions, validate_predictions
from metric_rca.evals.ptv_anti_cheat import validate_round_integrity
from metric_rca.evals.ptv_artifacts import (
    PtvLayout,
    canonicalize_eval_artifacts,
    create_cycle,
    create_round,
    generate_cycle_id,
    read_json,
    validate_round_outputs_fresh,
    verify_artifact_manifest,
    write_artifact_manifest,
    write_json_atomic,
)
from metric_rca.evals.ptv_controller import (
    CommandSpec,
    git_branch,
    git_head,
    run_checked_command,
    run_parallel_prediction_and_eval,
)
from metric_rca.evals.ptv_errors import PtvErrorCode, PtvRuntimeError
from metric_rca.evals.ptv_summary import build_round_summaries


def analyze_round(*, round_dir: Path, eval_id: str) -> dict[str, Any]:
    round_meta = read_json(round_dir / "round_meta.json")
    if round_meta.get("eval_id") != eval_id:
        raise PtvRuntimeError(
            PtvErrorCode.ARTIFACT_INVALID,
            "round metadata eval_id does not match analysis eval_id",
            context={"round_dir": str(round_dir), "expected": eval_id, "actual": round_meta.get("eval_id")},
        )
    if round_meta.get("status") != "prepared":
        raise PtvRuntimeError(
            PtvErrorCode.ARTIFACT_INVALID,
            "round metadata must be prepared before analysis",
            context={"round_dir": str(round_dir), "status": round_meta.get("status")},
        )
    barrier_path = round_dir / "barrier.json"
    if not barrier_path.exists():
        raise PtvRuntimeError(
            PtvErrorCode.BARRIER_NOT_REACHED,
            "analyst artifacts cannot be generated before prediction/eval barrier",
            context={"path": str(barrier_path)},
        )
    barrier = read_json(barrier_path)
    validate_round_outputs_fresh(round_dir=round_dir, eval_id=eval_id, barrier=barrier)
    canonicalize_eval_artifacts(round_dir=round_dir, eval_id=eval_id)
    predictions_path = round_dir / "predictions.jsonl"
    predictions = load_predictions(predictions_path)
    warnings = validate_predictions(predictions)
    if warnings:
        raise PtvRuntimeError(
            PtvErrorCode.PREDICTION_INVALID,
            "prediction schema validation failed",
            context={"warnings": warnings},
        )
    eval_result = read_json(round_dir / "eval-result.json")
    actuals = eval_result.get("cases")
    if not isinstance(actuals, list):
        raise PtvRuntimeError(PtvErrorCode.EVAL_RESULT_INVALID, "eval result cases must be a list")
    report = analyze_gaps(predictions, actuals, eval_id=eval_id)
    report_payload = asdict(report)
    write_json_atomic(round_dir / "gap_report.json", report_payload)
    divergent = [row for row in report_payload["gaps"] if row.get("divergence") != "correct"]
    analyst_input = {
        "schema_version": "metricrca-ptv-analyst-input-v1",
        "eval_id": eval_id,
        "round_dir": str(round_dir),
        "prediction_count": len(predictions),
        "case_count": len(actuals),
        "gap_summary": report_payload["summary"],
        "divergent_gaps": divergent,
        "required_output": str(round_dir / "diagnosis.jsonl"),
        "rules": {
            "one_row_per_divergent_case_aspect": True,
            "no_ground_truth_edit": True,
            "no_scorer_edit": True,
            "discovery_before_ranking_when_candidate_missing": True,
        },
    }
    write_json_atomic(round_dir / "analyst_input.json", analyst_input)
    return analyst_input


def finalize_round(
    *,
    layout: PtvLayout,
    cycle_id: str,
    round_number: int,
    selected_fix_category: str | None,
    selected_layer: str | None,
    controller_justification: str,
    revert_decision: str | None,
    private_ground_truth_path: Path | None,
    confirmation_round: bool,
) -> dict[str, Any]:
    round_dir = layout.round_dir(cycle_id, round_number)
    previous_dirs = [
        layout.round_dir(cycle_id, number)
        for number in range(1, round_number)
        if layout.round_dir(cycle_id, number).exists()
    ]
    optimization_summary, summary = build_round_summaries(
        cycle_id=cycle_id,
        round_number=round_number,
        round_dir=round_dir,
        previous_round_dirs=previous_dirs,
        selected_fix_category=selected_fix_category,
        selected_layer=selected_layer,
        controller_justification=controller_justification,
        revert_decision=revert_decision,
    )
    previous_round_dir = layout.previous_round_dir(cycle_id, round_number)
    validate_round_integrity(
        round_dir=round_dir,
        round_number=round_number,
        previous_round_dir=previous_round_dir if previous_round_dir and previous_round_dir.exists() else None,
        private_ground_truth_path=private_ground_truth_path,
        confirmation_round=confirmation_round,
        fail_on_findings=True,
    )
    manifest_path = write_artifact_manifest(round_dir)
    return {
        "optimization_summary": optimization_summary,
        "summary": summary,
        "artifact_manifest": str(manifest_path),
    }


def run_complete_round(
    *,
    layout: PtvLayout,
    cycle_id: str,
    round_number: int,
    eval_id: str,
    eval_code_commit: str,
    fix_commit: str | None,
    post_eval_review_fix_commit: str | None,
    confirmation_of_round: int | None,
    prediction_command: str,
    eval_command: str,
    analyst_command: str,
    repo_root: Path,
    selected_fix_category: str | None,
    selected_layer: str | None,
    controller_justification: str,
    revert_decision: str | None,
    private_ground_truth_path: Path | None,
) -> dict[str, Any]:
    round_dir = create_round(
        layout=layout,
        cycle_id=cycle_id,
        round_number=round_number,
        eval_id=eval_id,
        eval_code_commit=eval_code_commit,
        fix_commit=fix_commit,
        post_eval_review_fix_commit=post_eval_review_fix_commit,
        confirmation_of_round=confirmation_of_round,
    )
    prediction_spec = CommandSpec.from_shell_text(
        name="prediction",
        command=prediction_command,
        log_path=round_dir / "prediction.log",
        cwd=repo_root,
        env={
            "METRIC_RCA_PTV_CYCLE_ID": cycle_id,
            "METRIC_RCA_PTV_ROUND": str(round_number),
            "METRIC_RCA_PTV_ROUND_DIR": str(round_dir.resolve()),
            "METRIC_RCA_PTV_PREDICTIONS_PATH": str((round_dir / "predictions.jsonl").resolve()),
        },
    )
    eval_spec = CommandSpec.from_shell_text(
        name="eval",
        command=eval_command,
        log_path=round_dir / "eval.log",
        cwd=repo_root,
        env={
            "METRIC_RCA_PTV_CYCLE_ID": cycle_id,
            "METRIC_RCA_PTV_ROUND": str(round_number),
            "METRIC_RCA_PTV_ROUND_DIR": str(round_dir.resolve()),
            "METRIC_RCA_PTV_EVAL_ID": eval_id,
        },
    )
    run_parallel_prediction_and_eval(
        prediction=prediction_spec,
        evaluation=eval_spec,
        barrier_path=round_dir / "barrier.json",
    )
    analyst_input = analyze_round(round_dir=round_dir, eval_id=eval_id)
    analyst_spec = CommandSpec.from_shell_text(
        name="analyst",
        command=analyst_command,
        log_path=round_dir / "analyst.log",
        cwd=repo_root,
        env={
            "METRIC_RCA_PTV_CYCLE_ID": cycle_id,
            "METRIC_RCA_PTV_ROUND": str(round_number),
            "METRIC_RCA_PTV_ROUND_DIR": str(round_dir.resolve()),
            "METRIC_RCA_PTV_ANALYST_INPUT": str((round_dir / "analyst_input.json").resolve()),
            "METRIC_RCA_PTV_DIAGNOSIS_PATH": str((round_dir / "diagnosis.jsonl").resolve()),
        },
    )
    analyst_result = run_checked_command(analyst_spec)
    finalized = finalize_round(
        layout=layout,
        cycle_id=cycle_id,
        round_number=round_number,
        selected_fix_category=selected_fix_category,
        selected_layer=selected_layer,
        controller_justification=controller_justification,
        revert_decision=revert_decision,
        private_ground_truth_path=private_ground_truth_path,
        confirmation_round=confirmation_of_round is not None,
    )
    return {
        "round_dir": str(round_dir),
        "analyst_input": analyst_input,
        "analyst_result": analyst_result.as_dict(),
        **finalized,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MetricRCA executable PTV controller")
    parser.add_argument("--output-root", type=Path, default=Path("eval_out/ptv"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init-cycle")
    init.add_argument("--cycle-id")
    init.add_argument("--repo-root", type=Path, default=Path("."))
    init.add_argument("--branch")
    init.add_argument("--base-commit")
    init.add_argument("--total-cases", type=int, required=True)
    init.add_argument("--max-rounds", type=int, default=25)

    prepare = subparsers.add_parser("prepare-round")
    _add_round_identity_args(prepare)

    parallel = subparsers.add_parser("run-parallel")
    parallel.add_argument("--cycle-id", required=True)
    parallel.add_argument("--round", type=int, required=True)
    parallel.add_argument("--prediction-command", required=True)
    parallel.add_argument("--eval-command", required=True)
    parallel.add_argument("--repo-root", type=Path, default=Path("."))

    analyze = subparsers.add_parser("analyze")
    analyze.add_argument("--cycle-id", required=True)
    analyze.add_argument("--round", type=int, required=True)
    analyze.add_argument("--eval-id", required=True)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--cycle-id", required=True)
    finalize.add_argument("--round", type=int, required=True)
    _add_controller_args(finalize)

    run_round = subparsers.add_parser("run-round")
    _add_round_identity_args(run_round)
    run_round.add_argument("--prediction-command", required=True)
    run_round.add_argument("--eval-command", required=True)
    run_round.add_argument("--analyst-command", required=True)
    run_round.add_argument("--repo-root", type=Path, default=Path("."))
    _add_controller_args(run_round)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--cycle-id", required=True)
    verify.add_argument("--round", type=int, required=True)
    return parser


def _add_round_identity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cycle-id", required=True)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--eval-id", required=True)
    parser.add_argument("--eval-code-commit", required=True)
    parser.add_argument("--fix-commit")
    parser.add_argument("--post-eval-review-fix-commit")
    parser.add_argument("--confirmation-of-round", type=int)


def _add_controller_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--selected-fix-category")
    parser.add_argument("--selected-layer")
    parser.add_argument("--controller-justification", required=True)
    parser.add_argument("--revert-decision", choices=("keep", "revert"))
    parser.add_argument("--private-ground-truth", type=Path)
    parser.add_argument("--confirmation-round", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    layout = PtvLayout(args.output_root)
    try:
        result = _dispatch(args, layout=layout)
    except PtvRuntimeError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


def _dispatch(args: argparse.Namespace, *, layout: PtvLayout) -> dict[str, Any]:
    if args.command == "init-cycle":
        repo_root = args.repo_root.resolve()
        cycle_id = args.cycle_id or generate_cycle_id()
        cycle_dir = create_cycle(
            layout=layout,
            cycle_id=cycle_id,
            branch=args.branch or git_branch(repo_root),
            base_commit=args.base_commit or git_head(repo_root),
            total_cases=args.total_cases,
            max_rounds=args.max_rounds,
        )
        return {"cycle_id": cycle_id, "cycle_dir": str(cycle_dir)}
    if args.command == "prepare-round":
        round_dir = create_round(
            layout=layout,
            cycle_id=args.cycle_id,
            round_number=args.round,
            eval_id=args.eval_id,
            eval_code_commit=args.eval_code_commit,
            fix_commit=args.fix_commit,
            post_eval_review_fix_commit=args.post_eval_review_fix_commit,
            confirmation_of_round=args.confirmation_of_round,
        )
        return {"round_dir": str(round_dir)}
    if args.command == "run-parallel":
        round_dir = layout.round_dir(args.cycle_id, args.round)
        payload = run_parallel_prediction_and_eval(
            prediction=CommandSpec.from_shell_text(
                name="prediction",
                command=args.prediction_command,
                log_path=round_dir / "prediction.log",
                cwd=args.repo_root.resolve(),
                env={"METRIC_RCA_PTV_ROUND_DIR": str(round_dir.resolve())},
            ),
            evaluation=CommandSpec.from_shell_text(
                name="eval",
                command=args.eval_command,
                log_path=round_dir / "eval.log",
                cwd=args.repo_root.resolve(),
                env={"METRIC_RCA_PTV_ROUND_DIR": str(round_dir.resolve())},
            ),
            barrier_path=round_dir / "barrier.json",
        )
        return payload
    if args.command == "analyze":
        return analyze_round(round_dir=layout.round_dir(args.cycle_id, args.round), eval_id=args.eval_id)
    if args.command == "finalize":
        return finalize_round(
            layout=layout,
            cycle_id=args.cycle_id,
            round_number=args.round,
            selected_fix_category=args.selected_fix_category,
            selected_layer=args.selected_layer,
            controller_justification=args.controller_justification,
            revert_decision=args.revert_decision,
            private_ground_truth_path=args.private_ground_truth,
            confirmation_round=args.confirmation_round,
        )
    if args.command == "run-round":
        return run_complete_round(
            layout=layout,
            cycle_id=args.cycle_id,
            round_number=args.round,
            eval_id=args.eval_id,
            eval_code_commit=args.eval_code_commit,
            fix_commit=args.fix_commit,
            post_eval_review_fix_commit=args.post_eval_review_fix_commit,
            confirmation_of_round=args.confirmation_of_round,
            prediction_command=args.prediction_command,
            eval_command=args.eval_command,
            analyst_command=args.analyst_command,
            repo_root=args.repo_root.resolve(),
            selected_fix_category=args.selected_fix_category,
            selected_layer=args.selected_layer,
            controller_justification=args.controller_justification,
            revert_decision=args.revert_decision,
            private_ground_truth_path=args.private_ground_truth,
        )
    if args.command == "verify":
        round_dir = layout.round_dir(args.cycle_id, args.round)
        manifest = verify_artifact_manifest(round_dir)
        anti_cheat = read_json(round_dir / "anti_cheat_report.json")
        if anti_cheat.get("valid") is not True:
            raise PtvRuntimeError(PtvErrorCode.ARTIFACT_INVALID, "anti-cheat report is not valid")
        return {"round_dir": str(round_dir), "manifest": manifest, "anti_cheat": anti_cheat}
    raise PtvRuntimeError(PtvErrorCode.COMMAND_INVALID, "unsupported PTV command")


if __name__ == "__main__":
    raise SystemExit(main())

"""Real persisted-artifact eval runner for Matrix P5."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable
from uuid import uuid4

from metric_rca.agent.runner import run_rca
from metric_rca.config.settings import Settings, get_settings
from metric_rca.evals.models import EvalCase, EvalRuntimeError, GroundTruth, PersistedArtifacts
from metric_rca.evals.scorer import dangerous_sql_blocked, score_case, summarize_scores
from metric_rca.reporting.projector import build_report_from_persisted_artifacts
from metric_rca.repositories.metric_repository import MetricRepository


DEFAULT_CASES_PATH = Path(__file__).with_name("cases.jsonl")
DEFAULT_OUTPUT_DIR = Path("eval_out")
MAX_EVAL_RUN_ID_LENGTH = 42


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        case_id = payload.get("case_id")
        question = payload.get("question")
        tags = payload.get("tags", [])
        if not isinstance(case_id, str) or not isinstance(question, str):
            raise EvalRuntimeError("EVAL_CASE_INVALID", f"invalid case at line {line_number}")
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise EvalRuntimeError("EVAL_CASE_INVALID", f"invalid tags at line {line_number}")
        cases.append(EvalCase(case_id=case_id, question=question, tags=tuple(tags)))
    if not cases:
        raise EvalRuntimeError("EVAL_CASE_INVALID", "no eval cases configured")
    return cases


def run_eval(
    *,
    repository: Any | None = None,
    rca_runner: Callable[..., dict[str, Any]] = run_rca,
    settings: Settings | None = None,
    cases_path: Path = DEFAULT_CASES_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    eval_id: str | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or get_settings()
    resolved_repository = repository or MetricRepository.from_settings(resolved_settings)
    close_repository = repository is None
    resolved_eval_id = eval_id or f"eval-{uuid4().hex[:8]}"
    try:
        _validate_eval_model(resolved_settings)
        cases = load_cases(cases_path)
        ground_truth = _load_ground_truth(resolved_repository, cases)
        case_scores: list[dict[str, Any]] = []
        for case in cases:
            gt = ground_truth[case.case_id]
            case_settings = _settings_for_case(resolved_settings, gt)
            run_id, attempts = _run_case_with_retries(
                rca_runner=rca_runner,
                case=case,
                eval_id=resolved_eval_id,
                settings=case_settings,
                repository=resolved_repository,
            )
            artifacts = _read_artifacts(resolved_repository, run_id)
            score = score_case(case_id=case.case_id, ground_truth=gt, artifacts=artifacts)
            score["detail"]["eval_attempts"] = attempts
            score["detail"]["final_run_id"] = run_id
            case_scores.append(score)
            resolved_repository.create_eval_case_result(
                {
                    "eval_id": resolved_eval_id,
                    "case_id": case.case_id,
                    "intent_ok": score["intent_ok"],
                    "anomaly_ok": score["anomaly_ok"],
                    "top1_ok": score["top1_ok"],
                    "top3_ok": score["top3_ok"],
                    "evidence_coverage": score["evidence_coverage"],
                    "sql_safe": score["sql_safe"],
                    "reflection_repair_ok": score["reflection_repair_ok"],
                    "detail": {
                        "report_traceable_ok": score["report_traceable_ok"],
                        "memory_pollution_ok": score["memory_pollution_ok"],
                        "no_anomaly_task_ok": score["no_anomaly_task_ok"],
                        "adtributor_used": score["adtributor_used"],
                        "multi_agent_path": score["multi_agent_path"],
                        **score["detail"],
                    },
                }
            )
        summary = summarize_scores(
            case_scores,
            dangerous_sql_blocked=dangerous_sql_blocked(),
        )
        summary["llm_provider"] = resolved_settings.llm_provider
        summary["llm_model"] = resolved_settings.llm_model
        thresholds_met = _thresholds_met(summary)
        summary["thresholds_met"] = thresholds_met
        resolved_repository.create_eval_run(
            {
                "eval_id": resolved_eval_id,
                "created_at": datetime.now(timezone.utc),
                "summary": summary,
            }
        )
        output = {"eval_id": resolved_eval_id, "summary": summary, "cases": case_scores}
        _write_outputs(output, output_dir=output_dir)
        if not thresholds_met:
            raise EvalRuntimeError("EVAL_THRESHOLD_NOT_MET", resolved_eval_id)
        return output
    finally:
        if close_repository:
            resolved_repository.close()


def main() -> int:
    try:
        output = run_eval()
    except EvalRuntimeError as exc:
        print(json.dumps({"error_code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(output["summary"], ensure_ascii=False, sort_keys=True))
    return 0


def _load_ground_truth(repository: Any, cases: list[EvalCase]) -> dict[str, GroundTruth]:
    case_ids = [case.case_id for case in cases]
    if not hasattr(repository, "get_ground_truth_cases"):
        raise EvalRuntimeError("EVAL_GROUND_TRUTH_MISSING", "repository lacks ground truth reader")
    rows = repository.get_ground_truth_cases(case_ids)
    missing = sorted(case.case_id for case in cases if case.case_id not in rows)
    if missing:
        raise EvalRuntimeError("EVAL_GROUND_TRUTH_MISSING", missing[0])
    return {case.case_id: _ground_truth_from_row(rows[case.case_id]) for case in cases}


def _ground_truth_from_row(row: dict[str, Any]) -> GroundTruth:
    business_date = row["business_date"]
    if isinstance(business_date, str):
        from datetime import date

        business_date = date.fromisoformat(business_date)
    return GroundTruth(
        case_id=str(row["case_id"]),
        business_date=business_date,
        metric_id=str(row["metric_id"]),
        expected_anomaly=bool(row["expected_anomaly"]),
        root_cause_type=row.get("root_cause_type"),
        dimension=row.get("dimension"),
        element=row.get("element"),
    )


def _settings_for_case(settings: Settings, ground_truth: GroundTruth) -> Settings:
    values = settings.model_dump()
    values["target_date"] = ground_truth.business_date
    values["business_today"] = ground_truth.business_date + timedelta(days=1)
    values["memory_enabled"] = False
    values["memory_required"] = False
    return Settings(**values)


def _run_case_with_retries(
    *,
    rca_runner: Callable[..., dict[str, Any]],
    case: EvalCase,
    eval_id: str,
    settings: Settings,
    repository: Any,
) -> tuple[str, int]:
    max_attempts = int(getattr(settings, "eval_llm_max_attempts", 3))
    retry_seconds = float(getattr(settings, "eval_llm_retry_seconds", 20.0))
    base_run_id = _run_id(eval_id, case.case_id)
    last_run_id = base_run_id
    for attempt in range(1, max_attempts + 1):
        run_id = _attempt_run_id(base_run_id, attempt)
        last_run_id = run_id
        result = rca_runner(
            case.question,
            run_id=run_id,
            settings=settings,
            repository=repository,
            memory_repo=None,
        )
        error_code = str(result.get("error_code") or "")
        if not _is_transient_llm_error(error_code) or attempt == max_attempts:
            return run_id, attempt
        if retry_seconds > 0:
            time.sleep(retry_seconds)
    return last_run_id, max_attempts


def _read_artifacts(repository: Any, run_id: str) -> PersistedArtifacts:
    agent_run = repository.get_agent_run(run_id)
    evidences = repository.get_evidences(run_id)
    tasks = repository.get_operation_tasks(run_id)
    return PersistedArtifacts(
        agent_run=agent_run,
        evidences=evidences,
        trace_steps=repository.get_trace_steps(run_id),
        sql_audit=repository.get_sql_audit_rows(run_id),
        tasks=tasks,
        report=build_report_from_persisted_artifacts(
            agent_run=agent_run or {},
            evidences=evidences,
            tasks=tasks,
        ),
    )


def _write_outputs(output: dict[str, Any], *, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    eval_id = str(output["eval_id"])
    json_path = output_dir / f"{eval_id}.json"
    markdown_path = output_dir / f"{eval_id}.md"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    markdown_path.write_text(_markdown(output))


def _markdown(output: dict[str, Any]) -> str:
    lines = [f"# MetricRCA Eval {output['eval_id']}", "", "## Summary"]
    for key, value in output["summary"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Cases"])
    for row in output["cases"]:
        lines.append(
            f"- {row['case_id']}: top1={row['top1_ok']} anomaly={row['anomaly_ok']} "
            f"coverage={row['evidence_coverage']}"
        )
    lines.append("")
    return "\n".join(lines)


def _thresholds_met(summary: dict[str, Any]) -> bool:
    return (
        summary.get("case_total", 0) > 0
        and summary.get("intent_accuracy") == 1.0
        and summary.get("top1_rate", 0.0) >= 0.80
        and summary.get("top3_rate", 0.0) >= 0.90
        and summary.get("anomaly_accuracy") == 1.0
        and summary.get("evidence_coverage_avg") == 1.0
        and summary.get("sql_safe_rate") == 1.0
        and summary.get("report_traceable_rate") == 1.0
        and summary.get("reflection_repair_ok") is True
        and summary.get("memory_pollution_ok") is True
        and summary.get("dangerous_sql_blocked") is True
        and summary.get("no_anomaly_correct") is True
    )


_KNOWN_WEAK_MODELS = frozenset({"gpt-4.1-mini", "gpt-4.1-nano", "gpt-3.5-turbo"})


def _validate_eval_model(settings: Settings) -> None:
    model = (settings.llm_model or "").lower()
    if model in _KNOWN_WEAK_MODELS:
        raise EvalRuntimeError(
            "EVAL_MODEL_TOO_WEAK",
            f"eval requires a capable model (GPT-5 family / GPT-4.1 / DeepSeek-V3), not {settings.llm_model}",
        )


def _run_id(eval_id: str, case_id: str) -> str:
    return f"{eval_id}-{case_id}"[:MAX_EVAL_RUN_ID_LENGTH]


def _attempt_run_id(base_run_id: str, attempt: int) -> str:
    if attempt == 1:
        return base_run_id
    suffix = f"-r{attempt}"
    return f"{base_run_id[: MAX_EVAL_RUN_ID_LENGTH - len(suffix)]}{suffix}"


def _is_transient_llm_error(error_code: str) -> bool:
    normalized = error_code.lower()
    return normalized in {
        "llm_required_unavailable",
        "rate_limit_exceeded",
        "request_timeout",
        "timeout",
    }


if __name__ == "__main__":
    raise SystemExit(main())

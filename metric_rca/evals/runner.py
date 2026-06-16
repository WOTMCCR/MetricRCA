"""Real persisted-artifact eval runner for Matrix P5."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable
from uuid import uuid4

from metric_rca.agent.runner import run_rca
from metric_rca.config.settings import Settings, get_settings
from metric_rca.evals.models import EvalCase, EvalRuntimeError, GroundTruth, PersistedArtifacts
from metric_rca.evals.scorer import (
    dangerous_sql_blocked,
    score_case,
    summarize_memory_retrieval,
    summarize_scores,
)
from metric_rca.reporting.projector import build_report_from_persisted_artifacts
from metric_rca.repositories.metric_repository import MetricRepository


DEFAULT_CASES_PATH = Path(__file__).with_name("cases.jsonl")
DEFAULT_OUTPUT_DIR = Path("eval_out")
MAX_EVAL_RUN_ID_LENGTH = 42
MAX_EVAL_BASE_RUN_ID_LENGTH = 38
EVAL_RUN_ID_DIGEST_LENGTH = 8


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
    repository_factory: Callable[[], Any] | None = None,
    rca_runner: Callable[..., dict[str, Any]] = run_rca,
    settings: Settings | None = None,
    cases_path: Path = DEFAULT_CASES_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    eval_id: str | None = None,
    on_case_complete: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or get_settings()
    resolved_repository = repository or MetricRepository.from_settings(resolved_settings)
    close_repository = repository is None
    resolved_eval_id = eval_id or f"eval-{uuid4().hex[:8]}"

    try:
        cases = load_cases(cases_path)
        ground_truth = _load_ground_truth(resolved_repository, cases)
        memory_case_scores: list[dict[str, Any]] = []
        case_scores_progress: list[dict[str, Any]] = []

        def _write_progress(*, complete: bool) -> None:
            _persist_eval_summary(
                resolved_repository,
                eval_id=resolved_eval_id,
                summary=_eval_summary(
                    case_scores=case_scores_progress,
                    memory_case_scores=memory_case_scores,
                    settings=resolved_settings,
                    configured_case_total=len(cases),
                    complete=complete,
                ),
            )

        def _notify_memory_case(score: dict[str, Any]) -> None:
            memory_case_scores.append(score)
            if on_case_complete is not None:
                streamed = {"phase": "memory", **score}
                on_case_complete(streamed)
                _write_case_artifact(
                    output_dir=output_dir,
                    eval_id=resolved_eval_id,
                    case_id=score["case_id"],
                    score=streamed,
                    subdir="memory_cases",
                )
            _write_progress(complete=False)

        def _notify_case(score: dict[str, Any]) -> None:
            case_scores_progress.append(score)
            try:
                resolved_repository.upsert_eval_case_result(_case_result_row(resolved_eval_id, score))
            except RuntimeError as exc:
                raise EvalRuntimeError(_code_from_runtime_error(exc, "SYSTEM_TABLE_WRITE_FAILED"), str(exc)) from exc
            if on_case_complete is not None:
                on_case_complete(score)
                _write_case_artifact(
                    output_dir=output_dir,
                    eval_id=resolved_eval_id,
                    case_id=score["case_id"],
                    score=score,
                    subdir="cases",
                )
            _write_progress(complete=False)

        _write_progress(complete=False)
        memory_case_scores = _run_memory_cases(
            cases=cases,
            ground_truth=ground_truth,
            eval_id=f"{resolved_eval_id}-mem",
            settings=resolved_settings,
            rca_runner=rca_runner,
            repository=resolved_repository,
            repository_factory=repository_factory,
            injected_repository=repository is not None,
            on_case_complete=_notify_memory_case,
        )
        case_scores = _run_cases(
            cases=cases,
            ground_truth=ground_truth,
            eval_id=resolved_eval_id,
            settings=resolved_settings,
            rca_runner=rca_runner,
            repository=resolved_repository,
            repository_factory=repository_factory,
            injected_repository=repository is not None,
            memory_enabled=False,
            on_case_complete=_notify_case,
        )
        summary = _eval_summary(
            case_scores=case_scores,
            memory_case_scores=memory_case_scores,
            settings=resolved_settings,
            configured_case_total=len(cases),
            complete=True,
        )
        thresholds_met = bool(summary["thresholds_met"])
        _persist_eval_summary(resolved_repository, eval_id=resolved_eval_id, summary=summary)
        output = {
            "eval_id": resolved_eval_id,
            "summary": summary,
            "cases": case_scores,
            "memory_cases": memory_case_scores,
        }
        _write_outputs(output, output_dir=output_dir)
        if not thresholds_met:
            raise EvalRuntimeError("EVAL_THRESHOLD_NOT_MET", resolved_eval_id)
        return output
    finally:
        if close_repository:
            resolved_repository.close()


def _run_cases(
    *,
    cases: list[EvalCase],
    ground_truth: dict[str, GroundTruth],
    eval_id: str,
    settings: Settings,
    rca_runner: Callable[..., dict[str, Any]],
    repository: Any,
    repository_factory: Callable[[], Any] | None,
    injected_repository: bool,
    memory_enabled: bool,
    memory_write_on_finalize: bool | None = None,
    on_case_complete: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    concurrency = int(getattr(settings, "eval_concurrency", 1))
    if concurrency < 1:
        raise EvalRuntimeError("EVAL_CONCURRENCY_INVALID", "eval_concurrency must be >= 1")
    if concurrency == 1:
        results: list[dict[str, Any]] = []
        for case in cases:
            score = _run_and_score_case(
                case=case,
                ground_truth=ground_truth[case.case_id],
                eval_id=eval_id,
                settings=_settings_for_case(
                    settings,
                    ground_truth[case.case_id],
                    memory_enabled=memory_enabled,
                    memory_write_on_finalize=memory_write_on_finalize,
                ),
                rca_runner=rca_runner,
                repository=repository,
            )
            if on_case_complete is not None:
                on_case_complete(score)
            results.append(score)
        return results
    if injected_repository and repository_factory is None:
        raise EvalRuntimeError(
            "EVAL_CONCURRENCY_REPOSITORY_UNSAFE",
            "parallel eval requires a worker repository factory when repository is injected",
        )
    worker_repository_factory = repository_factory or (lambda: MetricRepository.from_settings(settings))
    executor = ThreadPoolExecutor(max_workers=concurrency)
    closed = False
    try:
        results: list[dict[str, Any] | None] = [None] * len(cases)
        futures: dict[Any, int] = {}
        next_index = 0

        def submit_next() -> None:
            nonlocal next_index
            if next_index >= len(cases):
                return
            case = cases[next_index]
            futures[
                executor.submit(
                    _run_and_score_case_with_factory,
                    case=case,
                    ground_truth=ground_truth[case.case_id],
                    eval_id=eval_id,
                    settings=_settings_for_case(
                        settings,
                        ground_truth[case.case_id],
                        memory_enabled=memory_enabled,
                        memory_write_on_finalize=memory_write_on_finalize,
                    ),
                    rca_runner=rca_runner,
                    repository_factory=worker_repository_factory,
                )
            ] = next_index
            next_index += 1

        for _ in range(min(concurrency, len(cases))):
            submit_next()
        while futures:
            for future in as_completed(tuple(futures)):
                index = futures.pop(future)
                score = future.result()
                results[index] = score
                if on_case_complete is not None:
                    on_case_complete(score)
                submit_next()
                break
        ordered_results: list[dict[str, Any]] = []
        for index, result in enumerate(results):
            if result is None:
                raise EvalRuntimeError("EVAL_CONCURRENCY_RESULT_MISSING", str(index))
            ordered_results.append(result)
        return ordered_results
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        closed = True
        raise
    finally:
        if not closed:
            executor.shutdown(wait=True, cancel_futures=False)


def _run_memory_cases(
    *,
    cases: list[EvalCase],
    ground_truth: dict[str, GroundTruth],
    eval_id: str,
    settings: Settings,
    rca_runner: Callable[..., dict[str, Any]],
    repository: Any,
    repository_factory: Callable[[], Any] | None,
    injected_repository: bool,
    on_case_complete: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    # Memory writes are disabled for eval prepass, so cases are independent and
    # can use the same concurrency controls as the baseline eval phase.
    return _run_cases(
        cases=cases,
        ground_truth=ground_truth,
        eval_id=eval_id,
        settings=settings,
        rca_runner=rca_runner,
        repository=repository,
        repository_factory=repository_factory,
        injected_repository=injected_repository,
        memory_enabled=True,
        memory_write_on_finalize=False,
        on_case_complete=on_case_complete,
    )


def _run_and_score_case_with_factory(
    *,
    case: EvalCase,
    ground_truth: GroundTruth,
    eval_id: str,
    settings: Settings,
    rca_runner: Callable[..., dict[str, Any]],
    repository_factory: Callable[[], Any],
) -> dict[str, Any]:
    repository = repository_factory()
    try:
        return _run_and_score_case(
            case=case,
            ground_truth=ground_truth,
            eval_id=eval_id,
            settings=settings,
            rca_runner=rca_runner,
            repository=repository,
        )
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()


def _run_and_score_case(
    *,
    case: EvalCase,
    ground_truth: GroundTruth,
    eval_id: str,
    settings: Settings,
    rca_runner: Callable[..., dict[str, Any]],
    repository: Any,
) -> dict[str, Any]:
    run_id, attempts = _run_case_with_retries(
        rca_runner=rca_runner,
        case=case,
        eval_id=eval_id,
        settings=settings,
        repository=repository,
    )
    artifacts = _read_artifacts(repository, run_id)
    score = score_case(case_id=case.case_id, ground_truth=ground_truth, artifacts=artifacts)
    score["detail"]["eval_attempts"] = attempts
    score["detail"]["final_run_id"] = run_id
    score["detail"]["memory_enabled"] = bool(settings.memory_enabled)
    score["detail"]["trace_step_count"] = len(artifacts.trace_steps)
    return score


def _case_result_row(eval_id: str, score: dict[str, Any]) -> dict[str, Any]:
    return {
        "eval_id": eval_id,
        "case_id": score["case_id"],
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


def _eval_summary(
    *,
    case_scores: list[dict[str, Any]],
    memory_case_scores: list[dict[str, Any]],
    settings: Settings,
    configured_case_total: int,
    complete: bool,
) -> dict[str, Any]:
    summary = summarize_scores(
        case_scores,
        dangerous_sql_blocked=dangerous_sql_blocked(),
    )
    summary.update(summarize_memory_retrieval(memory_case_scores, case_scores))
    summary["llm_provider"] = settings.llm_provider
    summary["llm_model"] = settings.llm_model
    summary["configured_case_total"] = configured_case_total
    summary["completed_case_total"] = len(case_scores)
    summary["completed_memory_case_total"] = len(memory_case_scores)
    summary["complete"] = complete
    summary["thresholds_met"] = _thresholds_met(summary) if complete else False
    return summary


def _persist_eval_summary(repository: Any, *, eval_id: str, summary: dict[str, Any]) -> None:
    writer = getattr(repository, "upsert_eval_run_summary", None)
    if not callable(writer):
        raise EvalRuntimeError("EVAL_PROGRESS_UNSUPPORTED", "repository lacks eval_run summary upsert")
    try:
        writer(
            {
                "eval_id": eval_id,
                "created_at": datetime.now(timezone.utc),
                "summary": summary,
            }
        )
    except RuntimeError as exc:
        raise EvalRuntimeError(_code_from_runtime_error(exc, "SYSTEM_TABLE_WRITE_FAILED"), str(exc)) from exc


def _code_from_runtime_error(exc: RuntimeError, default: str) -> str:
    code = str(exc).split(":", maxsplit=1)[0]
    return code if code.isupper() else default


def _write_case_artifact(
    *,
    output_dir: Path,
    eval_id: str,
    case_id: str,
    score: dict[str, Any],
    subdir: str,
) -> None:
    cases_dir = output_dir / eval_id / subdir
    cases_dir.mkdir(parents=True, exist_ok=True)
    case_path = cases_dir / f"{case_id}.json"
    case_path.write_text(json.dumps(score, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="MetricRCA eval runner")
    parser.add_argument("--stream", action="store_true", default=False, help="emit per-case JSONL to stdout")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--eval-id", type=str, default=None)
    args = parser.parse_args()

    def _stream_callback(score: dict[str, Any]) -> None:
        print(json.dumps(score, ensure_ascii=False, default=str), flush=True)

    try:
        output = run_eval(
            output_dir=args.output_dir,
            eval_id=args.eval_id,
            on_case_complete=_stream_callback if args.stream else None,
        )
    except EvalRuntimeError as exc:
        print(json.dumps({"error_code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1
    if not args.stream:
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


def _settings_for_case(
    settings: Settings,
    ground_truth: GroundTruth,
    *,
    memory_enabled: bool,
    memory_write_on_finalize: bool | None = None,
) -> Settings:
    values = settings.model_dump()
    values["target_date"] = ground_truth.business_date
    values["business_today"] = ground_truth.business_date + timedelta(days=1)
    values["memory_enabled"] = memory_enabled
    values["memory_required"] = False
    if memory_write_on_finalize is not None:
        values["memory_write_on_finalize"] = memory_write_on_finalize
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
        status = str(result.get("status") or "")
        if not error_code:
            if status in {"succeeded", "no_anomaly"}:
                return run_id, attempt
            if status == "failed":
                raise EvalRuntimeError("EVAL_RCA_RUN_FAILED", run_id)
            raise EvalRuntimeError("EVAL_RCA_RUN_STATUS_INVALID", run_id)
        if not _is_transient_eval_error(error_code) or attempt == max_attempts:
            raise EvalRuntimeError(error_code, run_id)
        if retry_seconds > 0:
            time.sleep(retry_seconds)
    raise EvalRuntimeError("EVAL_ATTEMPTS_EXHAUSTED", last_run_id)


def _read_artifacts(repository: Any, run_id: str) -> PersistedArtifacts:
    agent_run = repository.get_agent_run(run_id)
    _require_terminal_persisted_run(agent_run, run_id=run_id)
    evidences = repository.get_evidences(run_id)
    tasks = repository.get_operation_tasks(run_id)
    memory_reader = getattr(repository, "get_memory_records_for_run", None)
    if not callable(memory_reader):
        raise EvalRuntimeError("EVAL_MEMORY_ARTIFACT_UNSUPPORTED", "repository lacks memory artifact reader")
    try:
        memory_records = memory_reader(run_id)
    except RuntimeError as exc:
        raise EvalRuntimeError(_code_from_runtime_error(exc, "SYSTEM_TABLE_READ_FAILED"), str(exc)) from exc
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
        memory_records=memory_records,
    )


def _require_terminal_persisted_run(agent_run: dict[str, Any] | None, *, run_id: str) -> None:
    if agent_run is None:
        raise EvalRuntimeError("EVAL_RCA_RUN_MISSING", run_id)
    status = str(agent_run.get("status") or "")
    if status in {"succeeded", "no_anomaly"}:
        return
    if status == "failed":
        raise EvalRuntimeError(str(agent_run.get("error_code") or "EVAL_RCA_RUN_FAILED"), run_id)
    raise EvalRuntimeError("EVAL_RCA_RUN_STATUS_INVALID", run_id)


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
        and summary.get("memory_hit_improvement", -1.0) >= 0.0
        and summary.get("dangerous_sql_blocked") is True
        and summary.get("no_anomaly_correct") is True
    )


def _run_id(eval_id: str, case_id: str) -> str:
    raw = f"{eval_id}-{case_id}"
    if len(raw) <= MAX_EVAL_BASE_RUN_ID_LENGTH:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:EVAL_RUN_ID_DIGEST_LENGTH]
    prefix_length = MAX_EVAL_BASE_RUN_ID_LENGTH - len(digest) - 1
    return f"{raw[:prefix_length]}-{digest}"


def _attempt_run_id(base_run_id: str, attempt: int) -> str:
    if attempt == 1:
        return base_run_id
    suffix = f"-r{attempt}"
    return f"{base_run_id[: MAX_EVAL_RUN_ID_LENGTH - len(suffix)]}{suffix}"


def _is_transient_eval_error(error_code: str) -> bool:
    normalized = error_code.lower()
    return normalized in {
        "llm_required_unavailable",
        "rate_limit_exceeded",
        "request_timeout",
        "timeout",
    }


if __name__ == "__main__":
    raise SystemExit(main())

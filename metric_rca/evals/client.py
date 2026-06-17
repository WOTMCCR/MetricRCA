"""HTTP eval client for scoring persisted API artifacts."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable
from uuid import uuid4

import httpx

from metric_rca.evals.models import EvalRuntimeError, GroundTruth, PersistedArtifacts
from metric_rca.evals.scorer import (
    dangerous_sql_blocked,
    score_case,
    summarize_memory_retrieval,
    summarize_scores,
)


DEFAULT_CASES_PATH = Path(__file__).with_name("regression_public_cases.jsonl")
DEFAULT_PRIVATE_GROUND_TRUTH_PATH = Path(__file__).with_name("regression_private_ground_truth.jsonl")
DEFAULT_OUTPUT_DIR = Path("eval_out")
DEFAULT_HTTP_EVAL_MAX_ATTEMPTS = 3
DEFAULT_HTTP_EVAL_RETRY_SECONDS = 20.0
DEFAULT_HTTP_REQUEST_TIMEOUT_SECONDS = 600.0
DEFAULT_HTTP_EVAL_CONCURRENCY = 1
TRANSIENT_HTTP_EVAL_CODES = frozenset(
    {"llm_required_unavailable", "rate_limit_exceeded", "request_timeout", "timeout"}
)
REQUIRED_HTTP_CASE_FIELDS = frozenset(
    {
        "case_id",
        "question",
        "expected_metric_id",
        "expected_anomaly",
        "expected_root_cause_type",
        "expected_dimension",
        "expected_element",
        "expected_business_date",
    }
)
PUBLIC_HTTP_CASE_FIELDS = frozenset({"case_id", "question", "tags"})
PRIVATE_HTTP_GROUND_TRUTH_FIELDS = frozenset(
    {
        "case_id",
        "expected_metric_id",
        "expected_anomaly",
        "expected_root_cause_type",
        "expected_dimension",
        "expected_element",
        "expected_business_date",
    }
)
ANSWER_BEARING_HTTP_FIELDS = PRIVATE_HTTP_GROUND_TRUTH_FIELDS - {"case_id"}


def load_http_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    public_rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise EvalRuntimeError("EVAL_CASE_INVALID", f"invalid HTTP eval case at line {line_number}")
        leaked_fields = sorted(set(payload) & ANSWER_BEARING_HTTP_FIELDS)
        if leaked_fields:
            raise EvalRuntimeError(
                "EVAL_CASE_PRIVATE_FIELD_LEAKED",
                f"answer-bearing fields in HTTP public case at line {line_number}: {leaked_fields}",
            )
        if set(payload) == PUBLIC_HTTP_CASE_FIELDS:
            public_rows.append(payload)
            continue
        raise EvalRuntimeError("EVAL_CASE_INVALID", f"invalid HTTP eval case at line {line_number}")
    if public_rows:
        return _merge_public_cases_with_private_ground_truth(public_rows, DEFAULT_PRIVATE_GROUND_TRUTH_PATH)
    if not public_rows:
        raise EvalRuntimeError("EVAL_CASE_INVALID", "no HTTP eval cases configured")


def _merge_public_cases_with_private_ground_truth(
    public_rows: list[dict[str, Any]],
    private_ground_truth_path: Path,
) -> list[dict[str, Any]]:
    ground_truth_by_id: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(private_ground_truth_path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict) or set(payload) != PRIVATE_HTTP_GROUND_TRUTH_FIELDS:
            raise EvalRuntimeError("EVAL_CASE_INVALID", f"invalid private HTTP ground truth at line {line_number}")
        case_id = payload.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise EvalRuntimeError("EVAL_CASE_INVALID", f"invalid HTTP eval case at line {line_number}")
        ground_truth_by_id[case_id] = payload

    merged: list[dict[str, Any]] = []
    for row in public_rows:
        case_id = str(row["case_id"])
        ground_truth = ground_truth_by_id.get(case_id)
        if ground_truth is None:
            raise EvalRuntimeError("EVAL_GROUND_TRUTH_MISSING", case_id)
        merged.append(
            {
                "case_id": case_id,
                "question": row["question"],
                **{key: value for key, value in ground_truth.items() if key != "case_id"},
            }
        )
    return merged


def run_http_eval(
    *,
    base_url: str,
    provider: str,
    model: str,
    api_key: str | None = None,
    cases_path: Path = DEFAULT_CASES_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    transport: httpx.BaseTransport | None = None,
    eval_id: str | None = None,
    timeout: float = DEFAULT_HTTP_REQUEST_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_HTTP_EVAL_MAX_ATTEMPTS,
    retry_seconds: float = DEFAULT_HTTP_EVAL_RETRY_SECONDS,
    concurrency: int = DEFAULT_HTTP_EVAL_CONCURRENCY,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if max_attempts < 1:
        raise EvalRuntimeError("EVAL_HTTP_ATTEMPTS_INVALID", "max_attempts must be >= 1")
    if concurrency < 1:
        raise EvalRuntimeError("EVAL_HTTP_CONCURRENCY_INVALID", "concurrency must be >= 1")
    resolved_eval_id = eval_id or f"eval-http-{uuid4().hex[:8]}"
    cases = load_http_cases(cases_path)
    memory_scores_progress: list[dict[str, Any]] = []
    scores_progress: list[dict[str, Any]] = []
    with httpx.Client(base_url=base_url, transport=transport, timeout=timeout, trust_env=False) as client:
        output = _build_http_eval_output(
            eval_id=resolved_eval_id,
            provider=provider,
            model=model,
            total_case_count=len(cases),
            memory_scores=memory_scores_progress,
            scores=scores_progress,
            complete=False,
        )
        _publish_http_eval_state(client=client, output=output, output_dir=output_dir, progress=progress)

        def publish_memory(score: dict[str, Any]) -> None:
            memory_scores_progress.append(score)
            output = _build_http_eval_output(
                eval_id=resolved_eval_id,
                provider=provider,
                model=model,
                total_case_count=len(cases),
                memory_scores=memory_scores_progress,
                scores=scores_progress,
                complete=False,
            )
            _publish_http_eval_state(client=client, output=output, output_dir=output_dir, progress=progress)

        memory_scores = _run_http_cases(
            base_url=base_url,
            cases=cases,
            provider=provider,
            model=model,
            api_key=api_key,
            memory_enabled=True,
            memory_write_on_finalize=False,
            timeout=timeout,
            max_attempts=max_attempts,
            retry_seconds=retry_seconds,
            concurrency=concurrency,
            transport=transport,
            on_case_complete=publish_memory,
        )

        def publish_case(score: dict[str, Any]) -> None:
            scores_progress.append(score)
            _persist_http_eval_case_result(client=client, eval_id=resolved_eval_id, score=score)
            output = _build_http_eval_output(
                eval_id=resolved_eval_id,
                provider=provider,
                model=model,
                total_case_count=len(cases),
                memory_scores=memory_scores_progress,
                scores=scores_progress,
                complete=len(scores_progress) == len(cases),
            )
            _publish_http_eval_state(client=client, output=output, output_dir=output_dir, progress=progress)

        scores = _run_http_cases(
            base_url=base_url,
            cases=cases,
            provider=provider,
            model=model,
            api_key=api_key,
            memory_enabled=False,
            memory_write_on_finalize=True,
            timeout=timeout,
            max_attempts=max_attempts,
            retry_seconds=retry_seconds,
            concurrency=concurrency,
            transport=transport,
            on_case_complete=publish_case,
        )
        output = _build_http_eval_output(
            eval_id=resolved_eval_id,
            provider=provider,
            model=model,
            total_case_count=len(cases),
            memory_scores=memory_scores,
            scores=scores,
            complete=True,
        )
        _publish_http_eval_state(client=client, output=output, output_dir=output_dir, progress=progress)
    if not output["summary"]["thresholds_met"]:
        raise EvalRuntimeError("EVAL_THRESHOLD_NOT_MET", resolved_eval_id)
    return output


def _build_http_eval_output(
    *,
    eval_id: str,
    provider: str,
    model: str,
    total_case_count: int,
    memory_scores: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    complete: bool,
) -> dict[str, Any]:
    summary = summarize_scores(scores, dangerous_sql_blocked=dangerous_sql_blocked())
    completed_memory_scores = _memory_scores_for_completed_cases(memory_scores, scores)
    summary.update(summarize_memory_retrieval(completed_memory_scores, scores))
    summary["llm_provider"] = provider
    summary["llm_model"] = model
    summary["configured_case_total"] = total_case_count
    summary["completed_case_total"] = len(scores)
    summary["completed_memory_case_total"] = len(memory_scores)
    summary["complete"] = complete
    summary["thresholds_met"] = _http_thresholds_met(summary, scores) if complete else False
    return {
        "eval_id": eval_id,
        "summary": summary,
        "cases": list(scores),
        "memory_cases": list(memory_scores),
    }


def _memory_scores_for_completed_cases(
    memory_scores: list[dict[str, Any]],
    scores: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    completed_case_ids = {str(score["case_id"]) for score in scores}
    return [score for score in memory_scores if str(score["case_id"]) in completed_case_ids]


def _publish_http_eval_state(
    *,
    client: httpx.Client,
    output: dict[str, Any],
    output_dir: Path,
    progress: Callable[[dict[str, Any]], None] | None,
) -> None:
    _write_outputs(output, output_dir=output_dir)
    _persist_http_eval_summary(client=client, eval_id=str(output["eval_id"]), summary=output["summary"])
    if progress is not None:
        progress(
            {
                "eval_id": output["eval_id"],
                "summary": output["summary"],
            }
        )


def _persist_http_eval_summary(*, client: httpx.Client, eval_id: str, summary: dict[str, Any]) -> None:
    _request_json(client, "POST", f"/api/evals/{eval_id}/summary", json_payload={"summary": summary})


def _persist_http_eval_case_result(*, client: httpx.Client, eval_id: str, score: dict[str, Any]) -> None:
    _request_json(
        client,
        "POST",
        f"/api/evals/{eval_id}/case-results",
        json_payload=_http_case_result_row(score),
    )


def _http_case_result_row(score: dict[str, Any]) -> dict[str, Any]:
    return {
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


def _run_http_cases(
    *,
    base_url: str,
    cases: list[dict[str, Any]],
    provider: str,
    model: str,
    api_key: str | None,
    memory_enabled: bool,
    memory_write_on_finalize: bool,
    timeout: float,
    max_attempts: int,
    retry_seconds: float,
    concurrency: int,
    transport: httpx.BaseTransport | None,
    on_case_complete: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    if concurrency == 1:
        results: list[dict[str, Any]] = []
        for case in cases:
            score = _run_and_score_http_case_with_client(
                base_url=base_url,
                case=case,
                provider=provider,
                model=model,
                api_key=api_key,
                memory_enabled=memory_enabled,
                memory_write_on_finalize=memory_write_on_finalize,
                timeout=timeout,
                max_attempts=max_attempts,
                retry_seconds=retry_seconds,
                transport=transport,
            )
            if on_case_complete is not None:
                on_case_complete(score)
            results.append(score)
        return results

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
                    _run_and_score_http_case_with_client,
                    base_url=base_url,
                    case=case,
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    memory_enabled=memory_enabled,
                    memory_write_on_finalize=memory_write_on_finalize,
                    timeout=timeout,
                    max_attempts=max_attempts,
                    retry_seconds=retry_seconds,
                    transport=transport,
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
                raise EvalRuntimeError("EVAL_HTTP_RESULT_MISSING", str(index))
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


def _run_and_score_http_case_with_client(
    *,
    base_url: str,
    case: dict[str, Any],
    provider: str,
    model: str,
    api_key: str | None,
    memory_enabled: bool,
    memory_write_on_finalize: bool,
    timeout: float,
    max_attempts: int,
    retry_seconds: float,
    transport: httpx.BaseTransport | None,
) -> dict[str, Any]:
    with httpx.Client(base_url=base_url, transport=transport, timeout=timeout, trust_env=False) as client:
        return _run_and_score_http_case(
            client=client,
            case=case,
            provider=provider,
            model=model,
            api_key=api_key,
            memory_enabled=memory_enabled,
            memory_write_on_finalize=memory_write_on_finalize,
            max_attempts=max_attempts,
            retry_seconds=retry_seconds,
        )


def _run_and_score_http_case(
    *,
    client: httpx.Client,
    case: dict[str, Any],
    provider: str,
    model: str,
    api_key: str | None,
    memory_enabled: bool,
    memory_write_on_finalize: bool,
    max_attempts: int,
    retry_seconds: float,
) -> dict[str, Any]:
    run_id, attempts = _create_http_run_with_retries(
        client=client,
        case=case,
        provider=provider,
        model=model,
        api_key=api_key,
        memory_enabled=memory_enabled,
        memory_write_on_finalize=memory_write_on_finalize,
        max_attempts=max_attempts,
        retry_seconds=retry_seconds,
    )
    artifacts = _read_http_artifacts(client, run_id)
    score = score_case(
        case_id=str(case["case_id"]),
        ground_truth=_ground_truth_from_http_case(case),
        artifacts=artifacts,
    )
    score["detail"]["final_run_id"] = run_id
    score["detail"]["memory_enabled"] = memory_enabled
    score["detail"]["eval_attempts"] = attempts
    return score


def _create_http_run_with_retries(
    *,
    client: httpx.Client,
    case: dict[str, Any],
    provider: str,
    model: str,
    api_key: str | None,
    memory_enabled: bool,
    memory_write_on_finalize: bool,
    max_attempts: int,
    retry_seconds: float,
) -> tuple[str, int]:
    last_error: EvalRuntimeError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            run_id = _create_http_run_once(
                client=client,
                case=case,
                provider=provider,
                model=model,
                api_key=api_key,
                memory_enabled=memory_enabled,
                memory_write_on_finalize=memory_write_on_finalize,
            )
            return run_id, attempt
        except EvalRuntimeError as exc:
            last_error = exc
            if not _is_transient_http_eval_error(exc.code) or attempt == max_attempts:
                raise
            if retry_seconds > 0:
                time.sleep(retry_seconds)
    if last_error is not None:
        raise last_error
    raise EvalRuntimeError("EVAL_HTTP_ATTEMPTS_INVALID", "max_attempts must be >= 1")


def _create_http_run_once(
    *,
    client: httpx.Client,
    case: dict[str, Any],
    provider: str,
    model: str,
    api_key: str | None,
    memory_enabled: bool,
    memory_write_on_finalize: bool,
) -> str:
    target_date = date.fromisoformat(str(case["expected_business_date"]))
    payload = {
        "question": case["question"],
        "target_date": target_date.isoformat(),
        "business_today": (target_date + timedelta(days=1)).isoformat(),
        "memory_enabled": memory_enabled,
        "memory_required": False,
        "memory_write_on_finalize": memory_write_on_finalize,
        "llm_provider": provider,
        "llm_model": model,
    }
    if api_key is not None:
        payload["llm_api_key"] = api_key
    created = _request_json(client, "POST", "/api/rca/runs", json_payload=payload)
    raw_run_id = created.get("run_id")
    if not isinstance(raw_run_id, str) or not raw_run_id:
        raise EvalRuntimeError("EVAL_HTTP_RESPONSE_INVALID", "run_id")
    _require_terminal_http_run(created, run_id=raw_run_id)
    return raw_run_id


def _read_http_artifacts(client: httpx.Client, run_id: str) -> PersistedArtifacts:
    run = _request_json(client, "GET", f"/api/rca/runs/{run_id}")
    _require_terminal_http_run(run, run_id=run_id)
    evidence = _request_json(client, "GET", f"/api/rca/runs/{run_id}/evidence")
    trace = _request_json(client, "GET", f"/api/rca/runs/{run_id}/trace")
    sql_audit = _request_json(client, "GET", f"/api/rca/runs/{run_id}/sql-audit")
    tasks = _request_json(client, "GET", f"/api/rca/runs/{run_id}/tasks")
    memory = _request_json(client, "GET", f"/api/rca/runs/{run_id}/memory")
    return PersistedArtifacts(
        agent_run={
            "run_id": run_id,
            "status": run.get("status"),
            "metric_id": _metric_id_from_run(run),
        },
        evidences=_list_field(evidence, "evidence"),
        trace_steps=_list_field(trace, "trace"),
        sql_audit=_list_field(sql_audit, "sql_audit"),
        tasks=_list_field(tasks, "tasks"),
        report=run.get("report") if isinstance(run.get("report"), dict) else None,
        memory_records=_list_field(memory, "memory"),
    )


def _require_terminal_http_run(run: dict[str, Any], *, run_id: str) -> None:
    status = str(run.get("status") or "")
    if status in {"succeeded", "no_anomaly"}:
        return
    if status == "failed":
        raise EvalRuntimeError(str(run.get("error_code") or "EVAL_HTTP_RUN_FAILED"), run_id)
    raise EvalRuntimeError("EVAL_RCA_RUN_STATUS_INVALID", run_id)


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        response = client.request(method, path, json=json_payload)
    except httpx.TimeoutException as exc:
        raise EvalRuntimeError("EVAL_HTTP_REQUEST_TIMEOUT", path) from exc
    except httpx.RequestError as exc:
        raise EvalRuntimeError("EVAL_HTTP_REQUEST_FAILED", path) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise EvalRuntimeError("EVAL_HTTP_RESPONSE_INVALID", path) from exc
    if not isinstance(payload, dict):
        raise EvalRuntimeError("EVAL_HTTP_RESPONSE_INVALID", path)
    if response.status_code >= 400:
        code = payload.get("error_code")
        raise EvalRuntimeError(str(code or "EVAL_HTTP_REQUEST_FAILED"), str(payload.get("message") or path))
    return payload


def _is_transient_http_eval_error(error_code: str) -> bool:
    return str(error_code).lower() in TRANSIENT_HTTP_EVAL_CODES


def _ground_truth_from_http_case(case: dict[str, Any]) -> GroundTruth:
    return GroundTruth(
        case_id=str(case["case_id"]),
        business_date=date.fromisoformat(str(case["expected_business_date"])),
        metric_id=str(case["expected_metric_id"]),
        expected_anomaly=bool(case["expected_anomaly"]),
        root_cause_type=_optional_text(case["expected_root_cause_type"]),
        dimension=_optional_text(case["expected_dimension"]),
        element=_optional_text(case["expected_element"]),
    )


def _metric_id_from_run(run: dict[str, Any]) -> str | None:
    report = run.get("report")
    if isinstance(report, dict) and report.get("metric_id") is not None:
        return str(report["metric_id"])
    return None


def _list_field(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise EvalRuntimeError("EVAL_HTTP_RESPONSE_INVALID", key)
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _http_thresholds_met(summary: dict[str, Any], scores: list[dict[str, Any]]) -> bool:
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


def _write_outputs(output: dict[str, Any], *, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    eval_id = str(output["eval_id"])
    (output_dir / f"{eval_id}.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    (output_dir / f"{eval_id}.md").write_text(_markdown(output))


def _markdown(output: dict[str, Any]) -> str:
    lines = [f"# MetricRCA HTTP Eval {output['eval_id']}", "", "## Summary"]
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("METRIC_RCA_API_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--provider", default=os.getenv("METRIC_RCA_EVAL_PROVIDER"))
    parser.add_argument("--model", default=os.getenv("METRIC_RCA_EVAL_MODEL"))
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--cases-path", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=float, default=float(os.getenv("METRIC_RCA_EVAL_HTTP_TIMEOUT", DEFAULT_HTTP_REQUEST_TIMEOUT_SECONDS)))
    parser.add_argument("--max-attempts", type=int, default=int(os.getenv("METRIC_RCA_EVAL_LLM_MAX_ATTEMPTS", DEFAULT_HTTP_EVAL_MAX_ATTEMPTS)))
    parser.add_argument("--retry-seconds", type=float, default=float(os.getenv("METRIC_RCA_EVAL_LLM_RETRY_SECONDS", DEFAULT_HTTP_EVAL_RETRY_SECONDS)))
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("METRIC_RCA_EVAL_CONCURRENCY", DEFAULT_HTTP_EVAL_CONCURRENCY)))
    args = parser.parse_args(argv)
    if not args.provider:
        parser.error("--provider or METRIC_RCA_EVAL_PROVIDER is required")
    if not args.model:
        parser.error("--model or METRIC_RCA_EVAL_MODEL is required")
    api_key = _resolve_api_key(provider=args.provider, explicit_api_key=args.api_key)
    try:
        output = run_http_eval(
            base_url=args.base_url,
            provider=args.provider,
            model=args.model,
            api_key=api_key,
            cases_path=args.cases_path,
            output_dir=args.output_dir,
            timeout=args.timeout,
            max_attempts=args.max_attempts,
            retry_seconds=args.retry_seconds,
            concurrency=args.concurrency,
            progress=_print_progress,
        )
    except EvalRuntimeError as exc:
        print(json.dumps({"error_code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(output["summary"], ensure_ascii=False, sort_keys=True))
    return 0


def _print_progress(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)


def _resolve_api_key(*, provider: str, explicit_api_key: str | None) -> str | None:
    if explicit_api_key is not None:
        return explicit_api_key
    generic_key = os.getenv("METRIC_RCA_LLM_API_KEY")
    if generic_key is not None:
        return generic_key
    normalized_provider = provider.lower()
    if normalized_provider == "openai":
        return os.getenv("OPENAI_API_KEY")
    if normalized_provider == "deepseek":
        return os.getenv("DEEPSEEK_API_KEY")
    return None


if __name__ == "__main__":
    raise SystemExit(main())

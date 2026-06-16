from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any

import httpx
import pytest


def test_eval_http_client_uses_only_api_endpoints_and_scores_locally(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval

    cases_path = _http_cases_file(tmp_path)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    progress_events: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode()) if request.content else None
        calls.append((request.method, request.url.path, payload))
        if request.method == "POST" and request.url.path == "/api/evals/eval-http-1/summary":
            assert payload is not None
            assert payload["summary"]["llm_provider"] == "openai"
            assert "secret-http-eval" not in json.dumps(payload, default=str)
            return httpx.Response(200, json={"eval_id": "eval-http-1", "status": "stored"})
        if request.method == "POST" and request.url.path == "/api/evals/eval-http-1/case-results":
            assert payload is not None
            assert payload["case_id"] in {"gmv_paid_ads_drop", "gmv_no_anomaly"}
            assert payload["detail"]["memory_enabled"] is False
            assert payload["detail"]["final_run_id"].endswith("-base")
            assert "secret-http-eval" not in json.dumps(payload, default=str)
            return httpx.Response(200, json={"eval_id": "eval-http-1", "case_id": payload["case_id"], "status": "stored"})
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            assert payload is not None
            assert payload["question"] in {"Why did paid ads GMV drop?", "Was GMV normal yesterday?"}
            assert payload["target_date"] == "2026-06-05"
            assert payload["business_today"] == "2026-06-06"
            assert payload["llm_provider"] == "openai"
            assert payload["llm_model"] == "gpt-5-nano"
            assert payload["llm_api_key"] == "secret-http-eval"
            assert payload["memory_required"] is False
            assert payload["memory_enabled"] in {True, False}
            assert payload["memory_write_on_finalize"] is (not payload["memory_enabled"])
            return httpx.Response(200, json=_run_summary(_run_id_from_payload(payload)))
        return _artifact_response(request)

    output = run_http_eval(
        base_url="http://127.0.0.1:8000",
        provider="openai",
        model="gpt-5-nano",
        api_key="secret-http-eval",
        cases_path=cases_path,
        output_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        eval_id="eval-http-1",
        progress=progress_events.append,
    )

    assert output["summary"]["llm_provider"] == "openai"
    assert output["summary"]["llm_model"] == "gpt-5-nano"
    assert output["summary"]["thresholds_met"] is True
    assert output["summary"]["no_anomaly_correct"] is True
    assert output["cases"][0]["top1_ok"] == 1
    assert output["summary"]["memory_hit_improvement"] >= 0
    assert output["memory_cases"][0]["detail"]["memory_enabled"] is True
    paths = [path for _, path, _ in calls]
    assert paths.count("/api/rca/runs") == 4
    assert sum(path.endswith("/evidence") for path in paths) == 4
    assert sum(path.endswith("/trace") for path in paths) == 4
    assert sum(path.endswith("/sql-audit") for path in paths) == 4
    assert sum(path.endswith("/tasks") for path in paths) == 4
    assert sum(path.endswith("/memory") for path in paths) == 4
    assert paths.count("/api/evals/eval-http-1/case-results") == 2
    assert paths.count("/api/evals/eval-http-1/summary") > 1
    summary_payloads = [
        payload
        for _, path, payload in calls
        if path == "/api/evals/eval-http-1/summary" and payload is not None
    ]
    assert any(item["summary"]["complete"] is False for item in summary_payloads)
    assert summary_payloads[-1]["summary"]["complete"] is True
    assert any(event["summary"]["complete"] is False for event in progress_events)
    assert progress_events[-1]["summary"]["complete"] is True
    assert "secret-http-eval" not in (tmp_path / "eval-http-1.json").read_text()


def test_eval_http_client_parallelizes_memory_and_baseline_phases(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval

    lock = threading.Lock()
    active_by_phase = {"memory": 0, "baseline": 0}
    max_active_by_phase = {"memory": 0, "baseline": 0}
    memory_write_flags: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode()) if request.content else None
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            assert payload is not None
            phase = "memory" if payload["memory_enabled"] else "baseline"
            with lock:
                active_by_phase[phase] += 1
                max_active_by_phase[phase] = max(max_active_by_phase[phase], active_by_phase[phase])
                if phase == "memory":
                    memory_write_flags.append(payload["memory_write_on_finalize"])
            time.sleep(0.05)
            try:
                return httpx.Response(200, json=_run_summary(_run_id_from_payload(payload)))
            finally:
                with lock:
                    active_by_phase[phase] -= 1
        return _artifact_response(request)

    output = run_http_eval(
        base_url="http://127.0.0.1:8000",
        provider="openai",
        model="gpt-5-nano",
        cases_path=_http_cases_file(tmp_path),
        output_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        eval_id="eval-http-parallel",
        retry_seconds=0,
        concurrency=2,
    )

    assert output["summary"]["thresholds_met"] is True
    assert max_active_by_phase == {"memory": 2, "baseline": 2}
    assert memory_write_flags == [False, False]
    assert [row["case_id"] for row in output["cases"]] == ["gmv_paid_ads_drop", "gmv_no_anomaly"]


def test_eval_http_client_retries_typed_transient_failed_run_response(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval

    cases_path = _http_cases_file(tmp_path)
    post_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        payload = json.loads(request.content.decode()) if request.content else None
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            post_attempts += 1
            assert payload is not None
            if payload["memory_enabled"] is True and post_attempts == 1:
                return httpx.Response(
                    200,
                    json={"run_id": "http-run-transient", "status": "failed", "error_code": "RATE_LIMIT_EXCEEDED"},
                )
            run_id = _run_id_from_payload(payload, prefix=f"http-run-{post_attempts}")
            return httpx.Response(200, json=_run_summary(run_id))
        return _artifact_response(request)

    output = run_http_eval(
        base_url="http://127.0.0.1:8000",
        provider="openai",
        model="gpt-5-nano",
        cases_path=cases_path,
        output_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        eval_id="eval-http-retry",
        retry_seconds=0,
    )

    assert output["summary"]["thresholds_met"] is True
    assert output["summary"]["no_anomaly_correct"] is True
    assert output["memory_cases"][0]["detail"]["eval_attempts"] == 2
    assert output["memory_cases"][0]["detail"]["final_run_id"] == "http-run-2-anomaly-mem"
    assert output["cases"][0]["detail"]["eval_attempts"] == 1


def test_eval_http_client_does_not_retry_system_table_write_failure(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval
    from metric_rca.evals.models import EvalRuntimeError

    cases_path = _http_cases_file(tmp_path)
    post_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        progress_response = _eval_summary_response(request)
        if progress_response is not None:
            return progress_response
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            post_attempts += 1
            return httpx.Response(
                500,
                json={"error_code": "SYSTEM_TABLE_WRITE_FAILED", "message": "system write failed"},
            )
        return httpx.Response(404, json={"error_code": "ROUTE_NOT_FOUND", "message": "route not found"})

    try:
        run_http_eval(
            base_url="http://127.0.0.1:8000",
            provider="openai",
            model="gpt-5-nano",
            cases_path=cases_path,
            output_dir=tmp_path,
            transport=httpx.MockTransport(handler),
            eval_id="eval-http-no-system-retry",
            retry_seconds=0,
        )
    except EvalRuntimeError as exc:
        assert exc.code == "SYSTEM_TABLE_WRITE_FAILED"
    else:
        raise AssertionError("SYSTEM_TABLE_WRITE_FAILED must fail fast in HTTP eval")

    assert post_attempts == 1


def test_eval_http_client_wraps_non_json_persistence_error(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval
    from metric_rca.evals.models import EvalRuntimeError

    post_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        if request.method == "POST" and request.url.path == "/api/evals/eval-http-non-json/summary":
            return httpx.Response(502, text="bad gateway")
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            post_attempts += 1
            return httpx.Response(200, json=_run_summary(_run_id_from_payload(json.loads(request.content.decode()))))
        return _artifact_response(request)

    try:
        run_http_eval(
            base_url="http://127.0.0.1:8000",
            provider="openai",
            model="gpt-5-nano",
            cases_path=_http_cases_file(tmp_path),
            output_dir=tmp_path,
            transport=httpx.MockTransport(handler),
            eval_id="eval-http-non-json",
            retry_seconds=0,
        )
    except EvalRuntimeError as exc:
        assert exc.code == "EVAL_HTTP_RESPONSE_INVALID"
    else:
        raise AssertionError("non-JSON persistence error should be typed")

    assert post_attempts == 0


def test_eval_http_client_does_not_score_200_failed_system_table_write_run(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval
    from metric_rca.evals.models import EvalRuntimeError

    post_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        progress_response = _eval_summary_response(request)
        if progress_response is not None:
            return progress_response
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            post_attempts += 1
            return httpx.Response(
                200,
                json={"run_id": "http-run-system-write", "status": "failed", "error_code": "SYSTEM_TABLE_WRITE_FAILED"},
            )
        raise AssertionError(f"failed run should not be scored through artifact reads: {request.method} {request.url.path}")

    try:
        run_http_eval(
            base_url="http://127.0.0.1:8000",
            provider="openai",
            model="gpt-5-nano",
            cases_path=_http_cases_file(tmp_path),
            output_dir=tmp_path,
            transport=httpx.MockTransport(handler),
            eval_id="eval-http-200-system-failure",
            retry_seconds=0,
        )
    except EvalRuntimeError as exc:
        assert exc.code == "SYSTEM_TABLE_WRITE_FAILED"
    else:
        raise AssertionError("200 failed SYSTEM_TABLE_WRITE_FAILED response must fail fast")

    assert post_attempts == 1


def test_eval_http_client_rejects_post_success_with_nonterminal_status(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval
    from metric_rca.evals.models import EvalRuntimeError

    post_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        progress_response = _eval_summary_response(request)
        if progress_response is not None:
            return progress_response
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            post_attempts += 1
            return httpx.Response(200, json={"run_id": "http-run-running", "status": "running", "error_code": None})
        raise AssertionError(f"nonterminal POST run should not be scored: {request.method} {request.url.path}")

    try:
        run_http_eval(
            base_url="http://127.0.0.1:8000",
            provider="openai",
            model="gpt-5-nano",
            cases_path=_http_cases_file(tmp_path),
            output_dir=tmp_path,
            transport=httpx.MockTransport(handler),
            eval_id="eval-http-post-running",
            retry_seconds=0,
        )
    except EvalRuntimeError as exc:
        assert exc.code == "EVAL_RCA_RUN_STATUS_INVALID"
    else:
        raise AssertionError("nonterminal POST run should fail before scoring")

    assert post_attempts == 1


def test_eval_http_client_rejects_get_run_nonterminal_before_artifact_reads(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval
    from metric_rca.evals.models import EvalRuntimeError

    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        progress_response = _eval_summary_response(request)
        if progress_response is not None:
            return progress_response
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            return httpx.Response(200, json=_run_summary("http-run-late-running"))
        if request.method == "GET" and request.url.path == "/api/rca/runs/http-run-late-running":
            return httpx.Response(200, json={"run_id": "http-run-late-running", "status": "running", "error_code": None})
        raise AssertionError(f"nonterminal GET run should stop artifact reads: {request.method} {request.url.path}")

    try:
        run_http_eval(
            base_url="http://127.0.0.1:8000",
            provider="openai",
            model="gpt-5-nano",
            cases_path=_http_cases_file(tmp_path),
            output_dir=tmp_path,
            transport=httpx.MockTransport(handler),
            eval_id="eval-http-get-running",
            retry_seconds=0,
        )
    except EvalRuntimeError as exc:
        assert exc.code == "EVAL_RCA_RUN_STATUS_INVALID"
    else:
        raise AssertionError("nonterminal GET run should fail before artifact reads")

    assert calls == [
        ("POST", "/api/evals/eval-http-get-running/summary"),
        ("POST", "/api/rca/runs"),
        ("GET", "/api/rca/runs/http-run-late-running"),
    ]


def test_eval_http_client_does_not_retry_artifact_transport_failure_as_llm_transient(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval
    from metric_rca.evals.models import EvalRuntimeError

    post_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        progress_response = _eval_summary_response(request)
        if progress_response is not None:
            return progress_response
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            post_attempts += 1
            return httpx.Response(200, json=_run_summary("http-run-artifact-timeout"))
        if request.method == "GET" and request.url.path == "/api/rca/runs/http-run-artifact-timeout":
            raise httpx.TimeoutException("artifact timeout", request=request)
        return httpx.Response(404, json={"error_code": "ROUTE_NOT_FOUND", "message": "route not found"})

    try:
        run_http_eval(
            base_url="http://127.0.0.1:8000",
            provider="openai",
            model="gpt-5-nano",
            cases_path=_http_cases_file(tmp_path),
            output_dir=tmp_path,
            transport=httpx.MockTransport(handler),
            eval_id="eval-http-artifact-timeout",
            retry_seconds=0,
        )
    except EvalRuntimeError as exc:
        assert exc.code == "EVAL_HTTP_REQUEST_TIMEOUT"
    else:
        raise AssertionError("artifact transport failure should fail fast without case retry")

    assert post_attempts == 1


def test_eval_http_client_does_not_retry_artifact_body_error_as_llm_transient(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval
    from metric_rca.evals.models import EvalRuntimeError

    post_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        progress_response = _eval_summary_response(request)
        if progress_response is not None:
            return progress_response
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            post_attempts += 1
            return httpx.Response(200, json=_run_summary("http-run-artifact-body-error"))
        if request.method == "GET" and request.url.path == "/api/rca/runs/http-run-artifact-body-error":
            return httpx.Response(503, json={"error_code": "RATE_LIMIT_EXCEEDED", "message": "artifact endpoint throttled"})
        return httpx.Response(404, json={"error_code": "ROUTE_NOT_FOUND", "message": "route not found"})

    try:
        run_http_eval(
            base_url="http://127.0.0.1:8000",
            provider="openai",
            model="gpt-5-nano",
            cases_path=_http_cases_file(tmp_path),
            output_dir=tmp_path,
            transport=httpx.MockTransport(handler),
            eval_id="eval-http-artifact-body-error",
            retry_seconds=0,
        )
    except EvalRuntimeError as exc:
        assert exc.code == "RATE_LIMIT_EXCEEDED"
    else:
        raise AssertionError("artifact body error should fail fast without case retry")

    assert post_attempts == 1


def test_eval_http_client_does_not_retry_post_transport_timeout_as_llm_transient(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval
    from metric_rca.evals.models import EvalRuntimeError

    post_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        progress_response = _eval_summary_response(request)
        if progress_response is not None:
            return progress_response
        if request.method == "POST" and request.url.path == "/api/rca/runs":
            post_attempts += 1
            raise httpx.TimeoutException("local API timeout", request=request)
        return httpx.Response(404, json={"error_code": "ROUTE_NOT_FOUND", "message": "route not found"})

    try:
        run_http_eval(
            base_url="http://127.0.0.1:8000",
            provider="openai",
            model="gpt-5-nano",
            cases_path=_http_cases_file(tmp_path),
            output_dir=tmp_path,
            transport=httpx.MockTransport(handler),
            eval_id="eval-http-post-timeout",
            retry_seconds=0,
        )
    except EvalRuntimeError as exc:
        assert exc.code == "EVAL_HTTP_REQUEST_TIMEOUT"
    else:
        raise AssertionError("POST transport timeout should fail fast without case retry")

    assert post_attempts == 1


def test_eval_http_client_rejects_zero_attempts(tmp_path: Path) -> None:
    from metric_rca.evals.client import run_http_eval
    from metric_rca.evals.models import EvalRuntimeError

    try:
        run_http_eval(
            base_url="http://127.0.0.1:8000",
            provider="openai",
            model="gpt-5-nano",
            cases_path=_http_cases_file(tmp_path),
            output_dir=tmp_path,
            transport=httpx.MockTransport(lambda request: httpx.Response(500, json={})),
            max_attempts=0,
        )
    except EvalRuntimeError as exc:
        assert exc.code == "EVAL_HTTP_ATTEMPTS_INVALID"
    else:
        raise AssertionError("zero HTTP eval attempts should be rejected")


def test_eval_http_client_source_does_not_import_backend_runner_or_repositories() -> None:
    source = Path("metric_rca/evals/client.py").read_text()

    assert "run_rca" not in source
    assert "MetricRepository" not in source
    assert "repositories" not in source


def test_cases_jsonl_embeds_expected_fields_for_http_eval() -> None:
    required = {
        "expected_metric_id",
        "expected_anomaly",
        "expected_root_cause_type",
        "expected_dimension",
        "expected_element",
        "expected_business_date",
    }
    rows = [
        json.loads(line)
        for line in Path("metric_rca/evals/cases.jsonl").read_text().splitlines()
        if line.strip()
    ]

    assert len(rows) == 20
    assert all(required <= set(row) for row in rows)


def test_makefile_has_eval_http_target() -> None:
    source = Path("Makefile").read_text()

    assert "\neval-http:" in f"\n{source}"
    assert "metric_rca.evals.client" in source
    assert "LANGSMITH_TRACING=false" in source
    assert "LANGCHAIN_TRACING_V2=false" in source
    assert "--timeout $(HTTP_TIMEOUT)" in source
    assert "--concurrency $(HTTP_CONCURRENCY)" in source
    assert "PROVIDER ?=" not in source
    assert "MODEL ?=" not in source
    assert "PROVIDER is required for eval-http" in source
    assert "MODEL is required for eval-http" in source


def test_eval_http_client_main_requires_explicit_provider_and_model(monkeypatch: Any, tmp_path: Path) -> None:
    from metric_rca.evals import client

    monkeypatch.delenv("METRIC_RCA_EVAL_PROVIDER", raising=False)
    monkeypatch.delenv("METRIC_RCA_EVAL_MODEL", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        client.main(
            [
                "--base-url",
                "http://127.0.0.1:8000",
                "--cases-path",
                str(_http_cases_file(tmp_path)),
                "--output-dir",
                str(tmp_path),
            ]
        )

    assert exc_info.value.code == 2


def test_eval_http_client_main_passes_configured_timeout(monkeypatch: Any, tmp_path: Path) -> None:
    from metric_rca.evals import client

    captured: dict[str, Any] = {}

    def fake_run_http_eval(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "summary": {
                "thresholds_met": True,
                "case_total": 0,
            }
        }

    monkeypatch.setattr(client, "run_http_eval", fake_run_http_eval)
    monkeypatch.setenv("METRIC_RCA_EVAL_HTTP_TIMEOUT", "456")
    monkeypatch.setenv("METRIC_RCA_EVAL_CONCURRENCY", "7")

    exit_code = client.main(
        [
            "--base-url",
            "http://127.0.0.1:8000",
            "--provider",
            "openai",
            "--model",
            "gpt-5-nano",
            "--cases-path",
            str(_http_cases_file(tmp_path)),
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert captured["timeout"] == 456.0
    assert captured["concurrency"] == 7


def test_eval_http_thresholds_require_no_anomaly_correct_even_without_trap_cases() -> None:
    from metric_rca.evals.client import _http_thresholds_met

    summary = {
        "case_total": 1,
        "intent_accuracy": 1.0,
        "top1_rate": 1.0,
        "top3_rate": 1.0,
        "anomaly_accuracy": 1.0,
        "evidence_coverage_avg": 1.0,
        "sql_safe_rate": 1.0,
        "report_traceable_rate": 1.0,
        "reflection_repair_ok": True,
        "memory_pollution_ok": True,
        "memory_hit_improvement": 0.0,
        "dangerous_sql_blocked": True,
        "no_anomaly_correct": False,
    }

    assert _http_thresholds_met(summary, scores=[{"case_id": "single_anomaly_case"}]) is False


def test_eval_http_client_resolves_provider_native_keys_without_cross_provider_substitution(monkeypatch: Any) -> None:
    from metric_rca.evals.client import _resolve_api_key

    monkeypatch.delenv("METRIC_RCA_LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")

    assert _resolve_api_key(provider="openai", explicit_api_key=None) == "openai-key"
    assert _resolve_api_key(provider="deepseek", explicit_api_key=None) == "deepseek-key"
    assert _resolve_api_key(provider="openai-compatible", explicit_api_key=None) is None


def _artifact_response(request: httpx.Request) -> httpx.Response:
    parts = request.url.path.strip("/").split("/")
    if request.method == "POST" and len(parts) == 4 and parts[:2] == ["api", "evals"]:
        if parts[3] == "summary":
            return httpx.Response(200, json={"eval_id": parts[2], "status": "stored"})
        if parts[3] == "case-results":
            return httpx.Response(200, json={"eval_id": parts[2], "status": "stored"})
    run_id = parts[3] if len(parts) >= 4 else ""
    if request.method == "GET" and request.url.path == f"/api/rca/runs/{run_id}":
        return httpx.Response(200, json=_run_summary(run_id))
    if request.method == "GET" and request.url.path == f"/api/rca/runs/{run_id}/evidence":
        return httpx.Response(200, json={"run_id": run_id, "evidence": _evidences(run_id)})
    if request.method == "GET" and request.url.path == f"/api/rca/runs/{run_id}/trace":
        return httpx.Response(200, json={"run_id": run_id, "trace": _trace()})
    if request.method == "GET" and request.url.path == f"/api/rca/runs/{run_id}/sql-audit":
        return httpx.Response(200, json={"run_id": run_id, "sql_audit": [{"guard_status": "passed"}]})
    if request.method == "GET" and request.url.path == f"/api/rca/runs/{run_id}/tasks":
        tasks = [] if _run_is_no_anomaly(run_id) else [{"task_id": f"{run_id}:task"}]
        return httpx.Response(200, json={"run_id": run_id, "tasks": tasks})
    if request.method == "GET" and request.url.path == f"/api/rca/runs/{run_id}/memory":
        return httpx.Response(200, json={"run_id": run_id, "memory": _memory_records(run_id)})
    return httpx.Response(404, json={"error_code": "ROUTE_NOT_FOUND", "message": "route not found"})


def _eval_summary_response(request: httpx.Request) -> httpx.Response | None:
    parts = request.url.path.strip("/").split("/")
    if request.method == "POST" and len(parts) == 4 and parts[:2] == ["api", "evals"] and parts[3] == "summary":
        return httpx.Response(200, json={"eval_id": parts[2], "status": "stored"})
    return None


def _http_cases_file(tmp_path: Path) -> Path:
    path = tmp_path / "cases.jsonl"
    rows = [
        {
            "case_id": "gmv_paid_ads_drop",
            "question": "Why did paid ads GMV drop?",
            "expected_metric_id": "gmv",
            "expected_anomaly": True,
            "expected_root_cause_type": "campaign_traffic_drop",
            "expected_dimension": "channel",
            "expected_element": "paid_ads",
            "expected_business_date": "2026-06-05",
        },
        {
            "case_id": "gmv_no_anomaly",
            "question": "Was GMV normal yesterday?",
            "expected_metric_id": "gmv",
            "expected_anomaly": False,
            "expected_root_cause_type": None,
            "expected_dimension": None,
            "expected_element": None,
            "expected_business_date": "2026-06-05",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows))
    return path


def _run_id_from_payload(payload: dict[str, Any], *, prefix: str = "http-run") -> str:
    case_part = "no-anomaly" if _payload_is_no_anomaly(payload) else "anomaly"
    memory_part = "mem" if payload["memory_enabled"] else "base"
    return f"{prefix}-{case_part}-{memory_part}"


def _payload_is_no_anomaly(payload: dict[str, Any]) -> bool:
    return payload.get("question") == "Was GMV normal yesterday?"


def _run_is_no_anomaly(run_id: str) -> bool:
    return "no-anomaly" in run_id


def _run_summary(run_id: str) -> dict[str, Any]:
    if _run_is_no_anomaly(run_id):
        return {
            "run_id": run_id,
            "status": "no_anomaly",
            "error_code": None,
            "report": {"status": "no_anomaly", "metric_id": "gmv", "evidence_ids": [f"{run_id}:E1"]},
            "candidates": [],
            "tasks": [],
            "token_summary": {"total_tokens": 10, "latency_ms": 100},
            "links": {},
        }
    return {
        "run_id": run_id,
        "status": "succeeded",
        "error_code": None,
        "report": {
            "status": "succeeded",
            "metric_id": "gmv",
            "top_candidate": {
                "root_cause_type": "campaign_traffic_drop",
                "dimension": "channel",
                "element": "paid_ads",
                "verdict": "confirmed",
            },
            "numeric_claims": [{"name": "contribution_pct", "value": 0.9, "evidence_id": f"{run_id}:E4"}],
        },
        "candidates": [
            {
                "root_cause_type": "campaign_traffic_drop",
                "dimension": "channel",
                "element": "paid_ads",
                "verdict": "confirmed",
            }
        ],
        "tasks": [{"task_id": f"{run_id}:task"}],
        "token_summary": {"total_tokens": 10, "latency_ms": 100},
        "links": {},
    }


def _evidences(run_id: str) -> list[dict[str, Any]]:
    if _run_is_no_anomaly(run_id):
        return [
            {
                "evidence_id": f"{run_id}:E1",
                "run_id": run_id,
                "guard_status": "passed",
                "result_summary": {"is_anomaly": False},
            }
        ]
    candidate = {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": "paid_ads",
        "contribution_pct": 0.9,
        "signal_severity": 0.8,
        "evidence_support": 1.0,
        "eng_confidence": 0.85,
        "verdict": "confirmed",
        "evidence_ids": [
            f"{run_id}:E1",
            f"{run_id}:E2",
            f"{run_id}:E3",
            f"{run_id}:E4",
            f"{run_id}:E_rank",
        ],
    }
    return [
        {"evidence_id": f"{run_id}:E1", "run_id": run_id, "guard_status": "passed", "result_summary": {"is_anomaly": True}},
        {"evidence_id": f"{run_id}:E2", "run_id": run_id, "guard_status": "passed", "result_summary": {"value": 1.0}},
        {"evidence_id": f"{run_id}:E3", "run_id": run_id, "guard_status": "passed", "result_summary": {"signal_type": "campaign"}},
        {
            "evidence_id": f"{run_id}:E4",
            "run_id": run_id,
            "guard_status": "passed",
            "result_summary": {"selected_candidate": candidate, "candidates": [candidate]},
        },
        {
            "evidence_id": f"{run_id}:E_rank",
            "run_id": run_id,
            "guard_status": "passed",
            "result_summary": {"ranker": "v1", "selected_candidate": candidate, "candidates": [candidate]},
        },
    ]


def _trace() -> list[dict[str, Any]]:
    return [
        {"seq": 1, "node": "parse_question", "latency_ms": 5},
        {
            "seq": 2,
            "node": "llm_call",
            "latency_ms": 95,
            "token_usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
        },
        {"seq": 3, "node": "reflection_verify", "output_summary": {"passed": True, "issues": []}},
    ]


def _memory_records(run_id: str) -> list[dict[str, Any]]:
    return [
        {
            "memory_id": f"{run_id}:semantic-gmv",
            "layer": "semantic",
            "mem_key": "gmv|semantic",
            "payload": {"metric_id": "gmv"},
            "confidence": 0.95,
            "source": "system_verified",
            "version": 1,
            "ttl_days": 30,
            "created_at": "2026-06-05T00:00:00",
        }
    ]

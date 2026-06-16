from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from metric_rca.api.dependencies import ApiDependencies
from metric_rca.config.settings import Settings
from metric_rca.api.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_health_ok() -> None:
    client = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca)))

    assert client.get("/health").json() == {"status": "ok"}


def test_local_ui_cors_preflight_allows_rca_and_eval_posts() -> None:
    client = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca)))

    for path in ["/api/rca/runs", "/api/evals/run"]:
        response = client.options(
            path,
            headers={
                "origin": "http://127.0.0.1:5173",
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        assert "POST" in response.headers["access-control-allow-methods"]


def test_post_rca_runs_invokes_run_rca_and_persists_agent_run() -> None:
    repo = _Repository()

    client = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca)))
    response = client.post(
        "/api/rca/runs",
        json={
            "question": "Why did yesterday GMV drop?",
            "target_date": "2026-06-05",
            "memory_enabled": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "api-run-1"
    assert body["status"] == "succeeded"
    assert body["report"]["top_candidate"]["dimension"] == "channel"
    assert body["report"]["top_candidate"]["element"] == "paid_ads"
    assert body["report"]["top_candidate"]["element"] != "unsafe_post_state"
    assert repo.runner_calls == [{"question": "Why did yesterday GMV drop?", "memory_enabled": False}]
    assert repo.get_agent_run("api-run-1")["status"] == "succeeded"


def test_post_rca_runs_passes_per_request_llm_overrides_only_to_runner() -> None:
    repo = _Repository()
    captured: dict[str, Any] = {}

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        settings = kwargs["settings"]
        captured.update(
            {
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "api_key": settings.llm_api_key,
            }
        )
        return _run_rca(question, **kwargs)

    response = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=runner))).post(
        "/api/rca/runs",
        json={
            "question": "Why did yesterday GMV drop?",
            "llm_provider": "openai",
            "llm_model": "gpt-5-nano",
            "llm_api_key": "secret-eval-key",
            "memory_enabled": False,
        },
    )

    assert response.status_code == 200
    assert captured == {
        "provider": "openai",
        "model": "gpt-5-nano",
        "api_key": "secret-eval-key",
    }
    assert "secret-eval-key" not in response.text
    assert "secret-eval-key" not in json.dumps(repo.agent_runs, default=str)


def test_settings_override_does_not_reuse_default_key_across_providers(monkeypatch: Any) -> None:
    from metric_rca.api import dependencies

    base = Settings(
        db_dsn="sqlite+pysqlite:///:memory:",
        readonly_db_dsn="sqlite+pysqlite:///:memory:",
        llm_provider="openai",
        llm_model="gpt-5-nano",
        llm_api_key="openai-default-key",
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: base)

    settings = dependencies.settings_with_overrides(
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key=None,
    )

    assert settings.llm_provider == "deepseek"
    assert settings.llm_model == "deepseek-chat"
    assert settings.llm_api_key is None


def test_settings_override_does_not_fill_ambient_openai_key_for_provider_override(monkeypatch: Any) -> None:
    from metric_rca.api import dependencies

    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai-key")
    base = Settings(
        db_dsn="sqlite+pysqlite:///:memory:",
        readonly_db_dsn="sqlite+pysqlite:///:memory:",
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key="deepseek-key",
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: base)

    settings = dependencies.settings_with_overrides(
        llm_provider="openai",
        llm_model="gpt-5-nano",
        llm_api_key=None,
    )

    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-5-nano"
    assert settings.llm_api_key is None


def test_settings_override_keeps_default_key_when_provider_is_not_overridden(monkeypatch: Any) -> None:
    from metric_rca.api import dependencies

    base = Settings(
        db_dsn="sqlite+pysqlite:///:memory:",
        readonly_db_dsn="sqlite+pysqlite:///:memory:",
        llm_provider="openai",
        llm_model="gpt-5-nano",
        llm_api_key="openai-default-key",
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: base)

    settings = dependencies.settings_with_overrides(
        llm_model="gpt-5-mini",
        llm_api_key=None,
    )

    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-5-mini"
    assert settings.llm_api_key == "openai-default-key"


def test_get_run_reads_persisted_artifacts_not_graph_return_state() -> None:
    repo = _Repository()
    repo.agent_runs["run-1"] = _agent_run("run-1", status="succeeded")
    repo.evidences["run-1"] = _evidences("run-1", candidate=_candidate(element="persisted_paid_ads"))
    repo.tasks["run-1"] = [_task("run-1")]

    client = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_unsafe_return_state)))
    response = client.get("/api/rca/runs/run-1")

    assert response.status_code == 200
    body = response.json()
    assert body["report"]["top_candidate"]["element"] == "persisted_paid_ads"
    assert body["report"]["top_candidate"]["element"] != "unsafe_memory_state"
    assert repo.calls[:3] == ["get_agent_run", "get_evidences", "get_operation_tasks"]


def test_get_run_includes_token_summary_from_persisted_trace() -> None:
    repo = _Repository()
    repo.agent_runs["run-1"] = _agent_run("run-1", status="succeeded")
    repo.evidences["run-1"] = _evidences("run-1", candidate=_candidate())
    repo.trace_steps["run-1"] = [
        {
            "step_id": "s1",
            "run_id": "run-1",
            "seq": 1,
            "node": "parse_question",
            "latency_ms": 8,
            "token_usage": None,
        },
        {
            "step_id": "s2",
            "run_id": "run-1",
            "seq": 2,
            "node": "llm_call",
            "latency_ms": 120,
            "token_usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        },
    ]

    response = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/run-1"
    )

    assert response.status_code == 200
    summary = response.json()["token_summary"]
    assert summary["prompt_tokens"] == 7
    assert summary["completion_tokens"] == 3
    assert summary["total_tokens"] == 10
    assert summary["latency_ms"] == 128
    assert summary["by_step"][-1]["node"] == "llm_call"


def test_get_run_reconstructs_verified_report_from_persisted_e4() -> None:
    repo = _Repository()
    repo.agent_runs["run-1"] = _agent_run("run-1", status="succeeded")
    repo.evidences["run-1"] = _evidences("run-1", candidate=_candidate())

    response = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/run-1"
    )

    assert response.status_code == 200
    report = response.json()["report"]
    assert report["top_candidate"] == {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": "paid_ads",
        "verdict": "confirmed",
    }
    assert "contribution_pct" not in report["top_candidate"]
    assert report["numeric_claims"] == [
        {"name": "contribution_pct", "value": 0.9, "evidence_id": "run-1:E4"}
    ]


def test_api_returns_top_k_candidates_from_persisted_e4_candidates() -> None:
    repo = _Repository()
    repo.agent_runs["run-1"] = _agent_run("run-1", status="succeeded")
    repo.evidences["run-1"] = _evidences("run-1", candidate=_candidate())
    e4 = next(row for row in repo.evidences["run-1"] if row["evidence_id"] == "run-1:E4")
    first = e4["result_summary"]["selected_candidate"]
    e4["result_summary"]["candidates"] = [
        first,
        {
            **first,
            "element": "organic",
            "verdict": "likely",
            "contribution_pct": 0.1,
            "eng_confidence": 0.25,
        },
    ]

    response = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/run-1"
    )

    assert response.status_code == 200
    assert response.json()["candidates"] == [
        {
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "verdict": "confirmed",
            "contribution_pct": 0.9,
            "eng_confidence": 0.85,
                "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4", "run-1:E_rank"],
        },
        {
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "organic",
            "verdict": "likely",
            "contribution_pct": 0.1,
            "eng_confidence": 0.25,
                "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4", "run-1:E_rank"],
        },
    ]


def test_get_run_failed_state_returns_error_and_no_report() -> None:
    repo = _Repository()
    repo.agent_runs["failed-run"] = _agent_run(
        "failed-run",
        status="failed",
        error_code="REFLECTION_REPAIR_FAILED",
    )
    repo.evidences["failed-run"] = _evidences("failed-run", candidate=_candidate())

    response = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/failed-run"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert body["report"] is None
    assert body["candidates"] == []


def test_get_missing_run_returns_unified_error_body() -> None:
    response = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca))).get(
        "/api/rca/runs/missing"
    )

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "RUN_NOT_FOUND",
        "message": "run not found",
        "recoverable": False,
        "retryable": False,
        "trace_step_id": None,
        "suggested_next_action": None,
    }


def test_unmatched_route_returns_unified_error_body() -> None:
    response = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca))).get(
        "/api/not-real"
    )

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "ROUTE_NOT_FOUND",
        "message": "route not found",
        "recoverable": False,
        "retryable": False,
        "trace_step_id": None,
        "suggested_next_action": None,
    }


def test_get_run_no_anomaly_has_e1_only_no_task_no_candidate() -> None:
    repo = _Repository()
    repo.agent_runs["no-anom"] = _agent_run("no-anom", status="no_anomaly")
    repo.evidences["no-anom"] = [_evidence("no-anom:E1")]

    body = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/no-anom"
    ).json()

    assert body["status"] == "no_anomaly"
    assert body["report"] == {
        "status": "no_anomaly",
        "metric_id": "gmv",
        "target_date": "2026-06-05",
        "evidence_ids": ["no-anom:E1"],
    }
    assert body["candidates"] == []
    assert body["tasks"] == []


def test_get_trace_reads_persisted_trace_ordered_by_seq() -> None:
    repo = _Repository()
    repo.trace_steps["run-1"] = [
        {"step_id": "s2", "run_id": "run-1", "seq": 2, "node": "execute_tool"},
        {"step_id": "s1", "run_id": "run-1", "seq": 1, "node": "parse_question"},
    ]

    body = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/run-1/trace"
    ).json()

    assert [row["seq"] for row in body["trace"]] == [1, 2]


def test_get_evidence_reads_persisted_evidence_and_decodes_json() -> None:
    repo = _Repository()
    repo.evidences["run-1"] = _evidences("run-1", candidate=_candidate())

    body = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/run-1/evidence"
    ).json()

    assert body["evidence"][0]["result_summary"]["value"] == 1.0


def test_get_sql_audit_reads_persisted_sql_audit() -> None:
    repo = _Repository()
    repo.sql_audit["run-1"] = [
        {"audit_id": 1, "run_id": "run-1", "guard_status": "passed", "guard_errors": []}
    ]

    body = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/run-1/sql-audit"
    ).json()

    assert body["sql_audit"][0]["guard_status"] == "passed"


def test_get_tasks_reads_persisted_operation_task() -> None:
    repo = _Repository()
    repo.tasks["run-1"] = [_task("run-1")]

    body = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/run-1/tasks"
    ).json()

    assert body["tasks"][0]["task_id"] == "run-1:task"


def test_get_memory_reads_layered_memory_records_not_trace_steps() -> None:
    repo = _Repository()
    repo.memory_records["run-1"] = [
        {"memory_id": "m-sem", "layer": "semantic", "mem_key": "gmv|semantic", "payload": {"metric_id": "gmv"}},
        {"memory_id": "m-epi", "layer": "episodic", "mem_key": "gmv|channel", "payload": {"run_id": "run-1"}},
        {"memory_id": "m-ref", "layer": "reflection", "mem_key": "run-1|reflection", "payload": {"error_code": "REFLECTION_REPAIR_FAILED"}},
    ]
    repo.trace_steps["run-1"] = [
        {"step_id": "trace-memory", "run_id": "run-1", "seq": 1, "node": "read_memory"}
    ]

    body = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/run-1/memory"
    ).json()

    assert [row["layer"] for row in body["memory"]] == ["semantic", "episodic", "reflection"]
    assert {row["memory_id"] for row in body["memory"]} == {"m-sem", "m-epi", "m-ref"}
    assert "trace-memory" not in json.dumps(body["memory"])


def test_bad_body_returns_422() -> None:
    response = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca))).post(
        "/api/rca/runs",
        json={"target_date": "2026-06-05"},
    )

    assert response.status_code == 422


def test_business_error_response_shape() -> None:
    repo = _Repository(read_error=True)

    response = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca))).get(
        "/api/rca/runs/run-1/trace"
    )

    assert response.status_code == 500
    assert response.json() == {
        "error_code": "SYSTEM_TABLE_READ_FAILED",
        "message": "system table read failed",
        "recoverable": False,
        "retryable": False,
        "trace_step_id": None,
        "suggested_next_action": None,
    }


def test_api_routes_do_not_read_anomaly_ground_truth() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "metric_rca" / "api").rglob("*.py")
        if "anomaly_ground_truth" in path.read_text()
    ]

    assert offenders == []


def test_api_eval_endpoint_runs_real_eval() -> None:
    repo = _Repository()
    response = TestClient(
        create_app(ApiDependencies(repository=repo, rca_runner=_run_rca, eval_runner=_run_eval))
    ).post("/api/evals/run")

    assert response.status_code == 200
    assert response.json()["summary"]["case_total"] == 5
    assert repo.eval_calls == 1


def test_post_eval_case_result_persists_result_row() -> None:
    repo = _Repository()
    client = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca)))

    response = client.post(
        "/api/evals/eval-http-1/case-results",
        json={
            "case_id": "gmv_paid_ads_drop",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 1,
            "top3_ok": 1,
            "evidence_coverage": 1.0,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "detail": {
                "report_traceable_ok": 1,
                "memory_pollution_ok": 1,
                "no_anomaly_task_ok": 1,
                "adtributor_used": 0,
                "multi_agent_path": "single_agent",
                "final_run_id": "run-1",
                "memory_enabled": False,
            },
        },
    )
    second = client.post(
        "/api/evals/eval-http-1/case-results",
        json={
            "case_id": "gmv_paid_ads_drop",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 0,
            "top3_ok": 1,
            "evidence_coverage": 0.5,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "detail": {
                "report_traceable_ok": 1,
                "memory_pollution_ok": 1,
                "no_anomaly_task_ok": 1,
                "adtributor_used": 0,
                "multi_agent_path": "single_agent",
                "final_run_id": "run-2",
                "memory_enabled": False,
            },
        },
    )

    assert response.status_code == 200
    assert second.status_code == 200
    assert response.json() == {
        "eval_id": "eval-http-1",
        "case_id": "gmv_paid_ads_drop",
        "status": "stored",
    }
    assert repo.case_results == [
        {
            "eval_id": "eval-http-1",
            "case_id": "gmv_paid_ads_drop",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 0,
            "top3_ok": 1,
            "evidence_coverage": 0.5,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "detail": {
                "report_traceable_ok": 1,
                "memory_pollution_ok": 1,
                "no_anomaly_task_ok": 1,
                "adtributor_used": 0,
                "multi_agent_path": "single_agent",
                "final_run_id": "run-2",
                "memory_enabled": False,
            },
        }
    ]


def test_post_eval_case_result_rejects_invalid_score_flags() -> None:
    response = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca))).post(
        "/api/evals/eval-http-1/case-results",
        json={
            "case_id": "gmv_paid_ads_drop",
            "intent_ok": 2,
            "anomaly_ok": 1,
            "top1_ok": 1,
            "top3_ok": 1,
            "evidence_coverage": 1.2,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "detail": {
                "report_traceable_ok": 1,
                "memory_pollution_ok": 1,
                "no_anomaly_task_ok": 1,
                "adtributor_used": 0,
                "multi_agent_path": "single_agent",
            },
        },
    )

    assert response.status_code == 422


def test_post_eval_case_result_rejects_coerced_score_types() -> None:
    response = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca))).post(
        "/api/evals/eval-http-1/case-results",
        json={
            "case_id": "gmv_paid_ads_drop",
            "intent_ok": True,
            "anomaly_ok": 1,
            "top1_ok": 1,
            "top3_ok": 1,
            "evidence_coverage": "0.5",
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "detail": {
                "report_traceable_ok": 1,
                "memory_pollution_ok": 1,
                "no_anomaly_task_ok": 1,
                "adtributor_used": 0,
                "multi_agent_path": "single_agent",
            },
        },
    )

    assert response.status_code == 422


def test_post_eval_summary_upserts_eval_run_summary() -> None:
    repo = _Repository()
    client = TestClient(create_app(ApiDependencies(repository=repo, rca_runner=_run_rca)))

    first = client.post(
        "/api/evals/eval-http-1/summary",
        json={"summary": {"complete": False, "case_total": 1, "configured_case_total": 2}},
    )
    second = client.post(
        "/api/evals/eval-http-1/summary",
        json={
            "summary": {
                "case_total": 2,
                "intent_accuracy": 1.0,
                "top1_rate": 1.0,
                "top3_rate": 1.0,
                "anomaly_accuracy": 1.0,
                "evidence_coverage_avg": 1.0,
                "sql_safe_rate": 1.0,
                "report_traceable_rate": 1.0,
                "reflection_repair_ok": True,
                "memory_pollution_ok": True,
                "dangerous_sql_blocked": True,
                "no_anomaly_correct": True,
                "avg_tokens_per_case": 12.0,
                "avg_latency_ms_per_case": 100.0,
                "memory_enabled_top1_rate": 1.0,
                "memory_disabled_top1_rate": 1.0,
                "memory_hit_improvement": 0.0,
                "llm_provider": "openai",
                "llm_model": "gpt-5-nano",
                "configured_case_total": 2,
                "completed_case_total": 2,
                "completed_memory_case_total": 2,
                "complete": True,
                "thresholds_met": True,
            }
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert repo.eval_runs["eval-http-1"]["summary"] == {
        "case_total": 2,
        "intent_accuracy": 1.0,
        "top1_rate": 1.0,
        "top3_rate": 1.0,
        "anomaly_accuracy": 1.0,
        "evidence_coverage_avg": 1.0,
        "sql_safe_rate": 1.0,
        "report_traceable_rate": 1.0,
        "reflection_repair_ok": True,
        "memory_pollution_ok": True,
        "dangerous_sql_blocked": True,
        "no_anomaly_correct": True,
        "avg_tokens_per_case": 12.0,
        "avg_latency_ms_per_case": 100.0,
        "memory_enabled_top1_rate": 1.0,
        "memory_disabled_top1_rate": 1.0,
        "memory_hit_improvement": 0.0,
        "llm_provider": "openai",
        "llm_model": "gpt-5-nano",
        "configured_case_total": 2,
        "completed_case_total": 2,
        "completed_memory_case_total": 2,
        "complete": True,
        "thresholds_met": True,
    }


def test_post_eval_summary_rejects_null_typed_metrics() -> None:
    client = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca)))

    response = client.post(
        "/api/evals/eval-http-1/summary",
        json={"summary": {"complete": False, "dangerous_sql_blocked": None}},
    )

    assert response.status_code == 422


def test_post_eval_summary_complete_requires_final_metrics() -> None:
    client = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca)))

    response = client.post(
        "/api/evals/eval-http-1/summary",
        json={"summary": {"complete": True, "case_total": 2, "thresholds_met": True}},
    )

    assert response.status_code == 422


def test_post_eval_summary_rejects_coerced_boolean_metrics() -> None:
    client = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca)))

    response = client.post(
        "/api/evals/eval-http-1/summary",
        json={"summary": {"complete": "yes", "dangerous_sql_blocked": 1}},
    )

    assert response.status_code == 422


def test_get_missing_eval_returns_unified_error_body() -> None:
    response = TestClient(create_app(ApiDependencies(repository=_Repository(), rca_runner=_run_rca))).get(
        "/api/evals/missing"
    )

    assert response.status_code == 404
    assert response.json() == {
        "error_code": "EVAL_NOT_FOUND",
        "message": "eval not found",
        "recoverable": False,
        "retryable": False,
        "trace_step_id": None,
        "suggested_next_action": None,
    }


def _run_rca(question: str, **kwargs: Any) -> dict[str, Any]:
    repo = kwargs["repository"]
    repo.runner_calls.append(
        {"question": question, "memory_enabled": kwargs["settings"].memory_enabled}
    )
    repo.agent_runs["api-run-1"] = _agent_run("api-run-1", status="succeeded")
    repo.evidences["api-run-1"] = _evidences("api-run-1", candidate=_candidate())
    return {
        "run_id": "api-run-1",
        "status": "succeeded",
        "error_code": None,
        "report": {
            "status": "succeeded",
            "top_candidate": {"dimension": "channel", "element": "unsafe_post_state"},
        },
    }


def _unsafe_return_state(question: str, **kwargs: Any) -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "status": "succeeded",
        "report": {
            "top_candidate": {
                "root_cause_type": "campaign_traffic_drop",
                "dimension": "channel",
                "element": "unsafe_memory_state",
                "verdict": "confirmed",
            }
        },
    }


def _run_eval(**kwargs: Any) -> dict[str, Any]:
    repo = kwargs["repository"]
    repo.eval_calls += 1
    return {
        "eval_id": "eval-1",
        "summary": {
            "case_total": 5,
            "dangerous_sql_blocked": True,
            "no_anomaly_correct": True,
        },
        "cases": [],
    }


def _agent_run(run_id: str, *, status: str, error_code: str | None = None) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "question": "Why did yesterday GMV drop?",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "status": status,
        "error_code": error_code,
    }


def _candidate(*, element: str = "paid_ads") -> dict[str, Any]:
    return {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": element,
        "contribution_pct": 0.9,
        "signal_severity": 0.8,
        "evidence_support": 1.0,
        "eng_confidence": 0.85,
        "verdict": "confirmed",
        "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4", "run-1:E_rank"],
    }


def _evidence(evidence_id: str, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "run_id": evidence_id.split(":", maxsplit=1)[0],
        "guard_status": "passed",
        "query_spec": {"metric_id": "gmv"},
        "sql_hash": "0" * 64,
        "result_summary": summary or {"value": 1.0},
    }


def _evidences(run_id: str, *, candidate: dict[str, Any]) -> list[dict[str, Any]]:
    run_candidate = {
        **candidate,
        "evidence_ids": [f"{run_id}:E1", f"{run_id}:E2", f"{run_id}:E3", f"{run_id}:E4", f"{run_id}:E_rank"],
    }
    return [
        _evidence(f"{run_id}:E1"),
        _evidence(f"{run_id}:E2"),
        _evidence(f"{run_id}:E3"),
        _evidence(f"{run_id}:E4", summary={"selected_candidate": run_candidate}),
        _evidence(f"{run_id}:E_rank", summary={"selected_candidate": run_candidate}),
    ]


def _task(run_id: str) -> dict[str, Any]:
    return {
        "task_id": f"{run_id}:task",
        "run_id": run_id,
        "title": "Fix channel",
        "root_cause_type": "campaign_traffic_drop",
        "payload": {"owner": "ops"},
    }


class _Repository:
    def __init__(self, *, read_error: bool = False) -> None:
        self.read_error = read_error
        self.runner_calls: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.agent_runs: dict[str, dict[str, Any]] = {}
        self.evidences: dict[str, list[dict[str, Any]]] = {}
        self.trace_steps: dict[str, list[dict[str, Any]]] = {}
        self.sql_audit: dict[str, list[dict[str, Any]]] = {}
        self.tasks: dict[str, list[dict[str, Any]]] = {}
        self.memory_records: dict[str, list[dict[str, Any]]] = {}
        self.eval_runs: dict[str, dict[str, Any]] = {}
        self.case_results: list[dict[str, Any]] = []
        self.eval_calls = 0

    def _maybe_fail(self) -> None:
        if self.read_error:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED")

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        self.calls.append("get_agent_run")
        self._maybe_fail()
        return self.agent_runs.get(run_id)

    def get_evidences(self, run_id: str) -> list[dict[str, Any]]:
        self.calls.append("get_evidences")
        self._maybe_fail()
        return list(self.evidences.get(run_id, []))

    def get_operation_tasks(self, run_id: str) -> list[dict[str, Any]]:
        self.calls.append("get_operation_tasks")
        self._maybe_fail()
        return list(self.tasks.get(run_id, []))

    def get_trace_steps(self, run_id: str) -> list[dict[str, Any]]:
        self._maybe_fail()
        return sorted(self.trace_steps.get(run_id, []), key=lambda row: row["seq"])

    def get_sql_audit_rows(self, run_id: str) -> list[dict[str, Any]]:
        self._maybe_fail()
        return list(self.sql_audit.get(run_id, []))

    def get_memory_records_for_run(self, run_id: str) -> list[dict[str, Any]]:
        self._maybe_fail()
        return list(self.memory_records.get(run_id, []))

    def get_eval_run(self, eval_id: str) -> dict[str, Any] | None:
        return self.eval_runs.get(eval_id)

    def get_eval_case_results(self, eval_id: str) -> list[dict[str, Any]]:
        return [row for row in self.case_results if row["eval_id"] == eval_id]

    def upsert_eval_run_summary(self, row: dict[str, Any]) -> None:
        self.eval_runs[row["eval_id"]] = row

    def create_eval_case_result(self, row: dict[str, Any]) -> None:
        self.case_results.append(row)

    def upsert_eval_case_result(self, row: dict[str, Any]) -> None:
        for index, existing in enumerate(self.case_results):
            if existing["eval_id"] == row["eval_id"] and existing["case_id"] == row["case_id"]:
                self.case_results[index] = row
                return
        self.case_results.append(row)

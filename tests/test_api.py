from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from metric_rca.api.dependencies import ApiDependencies
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
    first = repo.evidences["run-1"][-1]["result_summary"]["selected_candidate"]
    repo.evidences["run-1"][-1]["result_summary"]["candidates"] = [
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
        },
        {
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "organic",
            "verdict": "likely",
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
        "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"],
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
        "evidence_ids": [f"{run_id}:E1", f"{run_id}:E2", f"{run_id}:E3", f"{run_id}:E4"],
    }
    return [
        _evidence(f"{run_id}:E1"),
        _evidence(f"{run_id}:E2"),
        _evidence(f"{run_id}:E3"),
        _evidence(f"{run_id}:E4", summary={"selected_candidate": run_candidate}),
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

    def get_eval_run(self, eval_id: str) -> dict[str, Any] | None:
        return None

    def get_eval_case_results(self, eval_id: str) -> list[dict[str, Any]]:
        return []

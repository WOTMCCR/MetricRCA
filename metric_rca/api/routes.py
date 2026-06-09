"""HTTP routes for MetricRCA API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from metric_rca.api.dependencies import ApiDependencies, settings_with_overrides
from metric_rca.api.schemas import (
    ErrorBody,
    EvalResponse,
    EvidenceResponse,
    MemoryResponse,
    RunCreateRequest,
    RunResponse,
    SqlAuditResponse,
    TasksResponse,
    TraceResponse,
)
from metric_rca.evals.models import EvalRuntimeError
from metric_rca.reporting.projector import build_report_from_persisted_artifacts


def build_router(dependencies: ApiDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/api/rca/runs", response_model=RunResponse)
    def create_run(request: RunCreateRequest) -> RunResponse | JSONResponse:
        repository = dependencies.get_repository()
        settings = settings_with_overrides(
            target_date=request.target_date,
            business_today=request.business_today,
            memory_enabled=request.memory_enabled,
            memory_required=request.memory_required,
        )
        try:
            result = dependencies.rca_runner(
                request.question,
                settings=settings,
                repository=repository,
            )
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        run_id = str(result["run_id"])
        try:
            agent_run = repository.get_agent_run(run_id)
            evidences = repository.get_evidences(run_id)
            tasks = repository.get_operation_tasks(run_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        report = build_report_from_persisted_artifacts(
            agent_run=agent_run or {},
            evidences=evidences,
            tasks=tasks,
        )
        candidates = _candidates_from_report(report)
        return RunResponse(
            run_id=run_id,
            status=str(result.get("status")),
            error_code=result.get("error_code"),
            report=report,
            candidates=candidates,
            tasks=tasks,
            links=_run_links(run_id),
        )

    @router.get("/api/rca/runs/{run_id}", response_model=RunResponse)
    def get_run(run_id: str) -> RunResponse:
        repository = dependencies.get_repository()
        try:
            agent_run = repository.get_agent_run(run_id)
            if agent_run is None:
                raise HTTPException(status_code=404, detail=_error_dict("RUN_NOT_FOUND", "run not found"))
            evidences = repository.get_evidences(run_id)
            tasks = repository.get_operation_tasks(run_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        report = build_report_from_persisted_artifacts(
            agent_run=agent_run,
            evidences=evidences,
            tasks=tasks,
        )
        error_code = agent_run.get("error_code")
        if agent_run.get("status") == "succeeded" and report is None:
            error_code = "REPORT_ARTIFACT_MISSING"
        return RunResponse(
            run_id=run_id,
            status=str(agent_run.get("status")),
            error_code=error_code,
            report=report,
            candidates=_candidates_from_report(report),
            tasks=tasks,
            links=_run_links(run_id),
        )

    @router.get("/api/rca/runs/{run_id}/trace", response_model=TraceResponse)
    def get_trace(run_id: str) -> TraceResponse | JSONResponse:
        try:
            trace = dependencies.get_repository().get_trace_steps(run_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        return TraceResponse(run_id=run_id, trace=trace)

    @router.get("/api/rca/runs/{run_id}/evidence", response_model=EvidenceResponse)
    def get_evidence(run_id: str) -> EvidenceResponse | JSONResponse:
        try:
            evidence = dependencies.get_repository().get_evidences(run_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        return EvidenceResponse(run_id=run_id, evidence=evidence)

    @router.get("/api/rca/runs/{run_id}/sql-audit", response_model=SqlAuditResponse)
    def get_sql_audit(run_id: str) -> SqlAuditResponse | JSONResponse:
        try:
            sql_audit = dependencies.get_repository().get_sql_audit_rows(run_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        return SqlAuditResponse(run_id=run_id, sql_audit=sql_audit)

    @router.get("/api/rca/runs/{run_id}/tasks", response_model=TasksResponse)
    def get_tasks(run_id: str) -> TasksResponse | JSONResponse:
        try:
            tasks = dependencies.get_repository().get_operation_tasks(run_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        return TasksResponse(run_id=run_id, tasks=tasks)

    @router.get("/api/rca/runs/{run_id}/memory", response_model=MemoryResponse)
    def get_memory(run_id: str) -> MemoryResponse | JSONResponse:
        try:
            trace = dependencies.get_repository().get_trace_steps(run_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        memory = [
            {
                "step_id": row.get("step_id"),
                "node": row.get("node"),
                "output_summary": row.get("output_summary"),
                "error_code": row.get("error_code"),
            }
            for row in trace
            if row.get("node") in {"read_memory", "write_memory"}
        ]
        return MemoryResponse(run_id=run_id, memory=memory)

    @router.post("/api/evals/run", response_model=EvalResponse)
    def run_eval_route() -> EvalResponse | JSONResponse:
        try:
            output = dependencies.eval_runner(repository=dependencies.get_repository())
        except EvalRuntimeError as exc:
            return JSONResponse(status_code=409, content=_error_dict(exc.code, str(exc)))
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        return EvalResponse(
            eval_id=str(output["eval_id"]),
            summary=output["summary"],
            cases=output["cases"],
        )

    @router.get("/api/evals/{eval_id}", response_model=EvalResponse)
    def get_eval(eval_id: str) -> EvalResponse | JSONResponse:
        repository = dependencies.get_repository()
        try:
            eval_run = repository.get_eval_run(eval_id)
            if eval_run is None:
                raise HTTPException(status_code=404, detail=_error_dict("EVAL_NOT_FOUND", "eval not found"))
            cases = repository.get_eval_case_results(eval_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        return EvalResponse(eval_id=eval_id, summary=eval_run["summary"], cases=cases)

    return router


def _runtime_error_response(exc: RuntimeError) -> JSONResponse:
    code = str(exc).split(":", maxsplit=1)[0]
    if code not in _TYPED_RUNTIME_ERROR_CODES:
        code = "SYSTEM_TABLE_READ_FAILED"
    return JSONResponse(status_code=500, content=_error_dict(code, _message_for_code(code)))


def _error_dict(error_code: str, message: str) -> dict[str, Any]:
    return ErrorBody(error_code=error_code, message=message).model_dump()


def _run_links(run_id: str) -> dict[str, str]:
    return {
        "self": f"/api/rca/runs/{run_id}",
        "trace": f"/api/rca/runs/{run_id}/trace",
        "evidence": f"/api/rca/runs/{run_id}/evidence",
        "sql_audit": f"/api/rca/runs/{run_id}/sql-audit",
        "tasks": f"/api/rca/runs/{run_id}/tasks",
        "memory": f"/api/rca/runs/{run_id}/memory",
    }


def _candidates_from_report(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not report:
        return []
    top = report.get("top_candidate")
    if not isinstance(top, dict):
        return []
    return [top]


_TYPED_RUNTIME_ERROR_CODES = {
    "SYSTEM_TABLE_READ_FAILED",
    "SYSTEM_TABLE_WRITE_FAILED",
    "SQL_EXECUTION_FAILED",
    "SQL_GUARD_REJECTED",
    "SQL_PLAN_INVALID",
    "QUERY_BUDGET_EXCEEDED",
    "LLM_REQUIRED_UNAVAILABLE",
    "MEMORY_READ_FAILED",
    "MEMORY_WRITE_FAILED",
}


def _message_for_code(code: str) -> str:
    return code.lower().replace("_", " ")

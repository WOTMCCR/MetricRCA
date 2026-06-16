"""HTTP routes for MetricRCA API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from metric_rca.api.dependencies import ApiDependencies, settings_with_overrides
from metric_rca.api.schemas import (
    ErrorBody,
    EvalCaseResultCreateRequest,
    EvalCaseResultStoreResponse,
    EvalResponse,
    EvalSummaryCreateRequest,
    EvalSummaryStoreResponse,
    EvidenceResponse,
    MemoryResponse,
    RunCreateRequest,
    RunResponse,
    SqlAuditResponse,
    TasksResponse,
    TraceResponse,
)
from metric_rca.evals.models import EvalRuntimeError
from metric_rca.observability.summary import build_token_summary
from metric_rca.reporting.projector import (
    build_report_from_persisted_artifacts,
    project_candidates_from_e4,
)


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
            llm_provider=request.llm_provider,
            llm_model=request.llm_model,
            llm_api_key=request.llm_api_key,
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
            trace = repository.get_trace_steps(run_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        report = build_report_from_persisted_artifacts(
            agent_run=agent_run or {},
            evidences=evidences,
            tasks=tasks,
        )
        candidates = _candidates_from_verified_report(
            report=report,
            status=str(result.get("status")),
            run_id=run_id,
            evidences=evidences,
        )
        return RunResponse(
            run_id=run_id,
            status=str(result.get("status")),
            error_code=result.get("error_code"),
            report=report,
            candidates=candidates,
            tasks=tasks,
            token_summary=build_token_summary(trace),
            links=_run_links(run_id),
        )

    @router.get("/api/rca/runs/{run_id}", response_model=RunResponse)
    def get_run(run_id: str) -> RunResponse | JSONResponse:
        repository = dependencies.get_repository()
        try:
            agent_run = repository.get_agent_run(run_id)
            if agent_run is None:
                return JSONResponse(
                    status_code=404,
                    content=_error_dict("RUN_NOT_FOUND", "run not found"),
                )
            evidences = repository.get_evidences(run_id)
            tasks = repository.get_operation_tasks(run_id)
            trace = repository.get_trace_steps(run_id)
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
            candidates=_candidates_from_verified_report(
                report=report,
                status=str(agent_run.get("status")),
                run_id=run_id,
                evidences=evidences,
            ),
            tasks=tasks,
            token_summary=build_token_summary(trace),
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
            memory = dependencies.get_repository().get_memory_records_for_run(run_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
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

    @router.post("/api/evals/{eval_id}/case-results", response_model=EvalCaseResultStoreResponse)
    def create_eval_case_result(
        eval_id: str,
        request: EvalCaseResultCreateRequest,
    ) -> EvalCaseResultStoreResponse | JSONResponse:
        repository = dependencies.get_repository()
        try:
            repository.upsert_eval_case_result({"eval_id": eval_id, **request.model_dump()})
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        return EvalCaseResultStoreResponse(
            eval_id=eval_id,
            case_id=request.case_id,
            status="stored",
        )

    @router.post("/api/evals/{eval_id}/summary", response_model=EvalSummaryStoreResponse)
    def upsert_eval_summary(
        eval_id: str,
        request: EvalSummaryCreateRequest,
    ) -> EvalSummaryStoreResponse | JSONResponse:
        repository = dependencies.get_repository()
        try:
            repository.upsert_eval_run_summary(
                {
                    "eval_id": eval_id,
                    "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
                    "summary": request.summary.model_dump(exclude_none=True),
                }
            )
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        return EvalSummaryStoreResponse(eval_id=eval_id, status="stored")

    @router.get("/api/evals/{eval_id}", response_model=EvalResponse)
    def get_eval(eval_id: str) -> EvalResponse | JSONResponse:
        repository = dependencies.get_repository()
        try:
            eval_run = repository.get_eval_run(eval_id)
            if eval_run is None:
                return JSONResponse(
                    status_code=404,
                    content=_error_dict("EVAL_NOT_FOUND", "eval not found"),
                )
            cases = repository.get_eval_case_results(eval_id)
        except RuntimeError as exc:
            return _runtime_error_response(exc)
        return EvalResponse(eval_id=eval_id, summary=eval_run["summary"], cases=cases)

    return router


def _runtime_error_response(exc: RuntimeError) -> JSONResponse:
    code = str(exc).split(":", maxsplit=1)[0]
    if code not in _TYPED_RUNTIME_ERROR_CODES:
        code = "SYSTEM_TABLE_READ_FAILED"
    return JSONResponse(
        status_code=_HTTP_STATUS_BY_CODE.get(code, 500),
        content=_error_dict(code, _message_for_code(code)),
    )


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


def _candidates_from_evidences(*, run_id: str, evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    e4_id = f"{run_id}:E4"
    for evidence in evidences:
        if evidence.get("evidence_id") == e4_id:
            summary = evidence.get("result_summary") or {}
            if isinstance(summary, dict):
                return project_candidates_from_e4(summary)
            return []
    return []


def _candidates_from_verified_report(
    *,
    report: dict[str, Any] | None,
    status: str,
    run_id: str,
    evidences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if status != "succeeded" or report is None:
        return []
    return _candidates_from_evidences(run_id=run_id, evidences=evidences)


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


_HTTP_STATUS_BY_CODE = {
    "RUN_NOT_FOUND": 404,
    "EVAL_NOT_FOUND": 404,
    "REPORT_ARTIFACT_MISSING": 409,
    "LLM_REQUIRED_UNAVAILABLE": 503,
    "MEMORY_READ_FAILED": 409,
    "MEMORY_WRITE_FAILED": 409,
    "SQL_GUARD_REJECTED": 400,
    "QUERY_BUDGET_EXCEEDED": 409,
    "SYSTEM_TABLE_READ_FAILED": 500,
    "SYSTEM_TABLE_WRITE_FAILED": 500,
    "SQL_EXECUTION_FAILED": 500,
    "SQL_PLAN_INVALID": 500,
}


def _message_for_code(code: str) -> str:
    return code.lower().replace("_", " ")

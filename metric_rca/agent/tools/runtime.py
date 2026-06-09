"""Shared runtime helpers for deterministic tool execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from metric_rca.agent.tools.schemas import ToolResult
from metric_rca.domain.models import Evidence, Observation, SQLPlan


@dataclass(frozen=True)
class ToolRuntimeError(Exception):
    code: str
    message: str


def run_context_error(repository: Any, run_id: str, metric_id: str, target_date: Any) -> str | None:
    row = repository.get_agent_run(run_id)
    if row is None or row.get("status") != "running":
        return "RUN_NOT_FOUND"
    if row.get("metric_id") != metric_id or str(row.get("target_date")) != str(target_date):
        return "RUN_CONTEXT_MISMATCH"
    return None


def current_run_guarded_evidence(
    repository: Any,
    run_id: str,
    evidence_ids: list[str],
    required_aliases: set[str],
) -> bool:
    if not evidence_ids:
        return False
    aliases = {evidence_id.split(":", maxsplit=1)[1] for evidence_id in evidence_ids if ":" in evidence_id}
    if not required_aliases.issubset(aliases):
        return False
    for evidence_id in evidence_ids:
        if not evidence_id.startswith(f"{run_id}:"):
            return False
        row = repository.get_evidence(run_id=run_id, evidence_id=evidence_id)
        if row is None or row.get("guard_status") != "passed":
            return False
    return True


def execute_guarded_plan(*, repository: Any, plan: SQLPlan, run_id: str):
    if plan.guard_status != "passed":
        raise ToolRuntimeError("SQL_GUARD_REJECTED", "renderer output failed SQLGuard")
    try:
        return repository.execute_plan(plan, run_id=run_id)
    except ValueError as exc:
        code = _error_code_from_message(str(exc))
        if code in {"SQL_GUARD_REJECTED", "SQL_PLAN_INVALID", "QUERY_SPEC_INVALID"}:
            raise ToolRuntimeError(code, str(exc)) from exc
        raise
    except RuntimeError as exc:
        code = _error_code_from_message(str(exc))
        if code == "SQL_EXECUTION_FAILED":
            raise ToolRuntimeError(code, "SQL execution failed") from exc
        raise


def persist_evidence(*, repository: Any, row: dict[str, Any]) -> None:
    try:
        repository.create_evidence(row)
    except RuntimeError as exc:
        code = _error_code_from_message(str(exc))
        if code == "SYSTEM_TABLE_WRITE_FAILED":
            raise ToolRuntimeError(code, "system table write failed") from exc
        raise


def tool_error(action: str, code: str, message: str) -> ToolResult:
    return ToolResult(
        observation=Observation(action_name=action, ok=False, error_code=code, message=message)
    )


def runtime_error(action: str, error: ToolRuntimeError) -> ToolResult:
    return tool_error(action, error.code, error.message)


def query_sources(*, current_plan: SQLPlan, baseline_plan: SQLPlan) -> dict[str, Any]:
    return {
        "current_sql_hash": current_plan.sql_hash,
        "baseline_sql_hash": baseline_plan.sql_hash,
        "current_sql": current_plan.sql,
        "baseline_sql": baseline_plan.sql,
        "current_params": {key: str(value) for key, value in current_plan.params.items()},
        "baseline_params": {key: str(value) for key, value in baseline_plan.params.items()},
    }


def evidence_row(run_id: str, evidence: Evidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "run_id": run_id,
        "query_spec": evidence.query_spec.model_dump(mode="json"),
        "sql_text": evidence.sql,
        "sql_hash": evidence.sql_hash,
        "guard_status": evidence.guard_status,
        "result_summary": evidence.result_summary,
        "data_source": evidence.data_source,
        "created_at": evidence.created_at,
    }


def _error_code_from_message(message: str) -> str:
    return message.split(":", maxsplit=1)[0]

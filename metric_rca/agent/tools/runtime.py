"""Shared runtime helpers for deterministic tool execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from metric_rca.agent.tools.schemas import ToolResult
from metric_rca.domain.models import Evidence, Observation, SQLPlan
from metric_rca.runtime.evidence_identity import (
    EvidenceIdentityError,
    alias_from_row,
    alias_matches,
    compose_evidence_id,
    split_evidence_id,
)


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


def get_evidence_by_alias(repository: Any, *, run_id: str, alias: str) -> dict[str, Any] | None:
    getter = getattr(repository, "get_evidence_by_alias", None)
    if callable(getter):
        return getter(run_id=run_id, alias=alias)
    return repository.get_evidence(
        run_id=run_id,
        evidence_id=compose_evidence_id(run_id, alias),
    )


def current_run_guarded_evidence(
    repository: Any,
    run_id: str,
    evidence_ids: list[str],
    required_aliases: set[str],
) -> bool:
    if not evidence_ids:
        return False

    aliases: set[str] = set()
    for evidence_id in evidence_ids:
        try:
            identity = split_evidence_id(evidence_id)
        except EvidenceIdentityError:
            return False
        if identity.run_id != run_id:
            return False
        row = repository.get_evidence(run_id=run_id, evidence_id=evidence_id)
        if row is None or row.get("guard_status") != "passed":
            return False
        try:
            aliases.add(alias_from_row(row))
        except EvidenceIdentityError:
            return False

    return all(
        any(alias_matches(actual_alias, required_alias) for actual_alias in aliases)
        for required_alias in required_aliases
    )


def current_run_guarded_evidence_hint(
    repository: Any,
    run_id: str,
    required_aliases: list[str],
) -> list[str]:
    """Return exact current-run evidence ids that satisfy required aliases."""

    rows = repository.get_evidences(run_id)
    hints: list[str] = []
    for required_alias in required_aliases:
        evidence_id = _first_guarded_alias_evidence_id(
            rows,
            run_id=run_id,
            required_alias=required_alias,
        )
        if evidence_id is not None:
            hints.append(evidence_id)
    return hints


def _first_guarded_alias_evidence_id(
    rows: list[dict[str, Any]],
    *,
    run_id: str,
    required_alias: str,
) -> str | None:
    exact: str | None = None
    suffixed: str | None = None
    for row in rows:
        evidence_id = str(row.get("evidence_id") or "")
        if row.get("guard_status") != "passed":
            continue
        try:
            identity = split_evidence_id(evidence_id)
            actual_alias = alias_from_row(row)
        except EvidenceIdentityError:
            continue
        if identity.run_id != run_id:
            continue
        if actual_alias == required_alias:
            exact = evidence_id
            break
        if suffixed is None and alias_matches(actual_alias, required_alias):
            suffixed = evidence_id
    return exact or suffixed


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
        if code == "SYSTEM_TABLE_WRITE_FAILED":
            raise ToolRuntimeError(code, "system table write failed") from exc
        if code == "QUERY_BUDGET_EXCEEDED":
            raise ToolRuntimeError(code, "query budget exceeded") from exc
        raise


def persist_evidence(*, repository: Any, row: dict[str, Any]) -> None:
    try:
        repository.create_evidence(row)
    except RuntimeError as exc:
        code = _error_code_from_message(str(exc))
        if code == "SYSTEM_TABLE_WRITE_FAILED":
            raise ToolRuntimeError(code, "system table write failed") from exc
        raise


def tool_error(action: str, code: str, message: str, *, sql_count: int = 0) -> ToolResult:
    return ToolResult(
        observation=Observation(action_name=action, ok=False, error_code=code, message=message),
        sql_count=sql_count,
    )


def runtime_error(action: str, error: ToolRuntimeError, *, sql_count: int = 0) -> ToolResult:
    return tool_error(action, error.code, error.message, sql_count=sql_count)


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
    try:
        identity = split_evidence_id(evidence.evidence_id)
    except EvidenceIdentityError as exc:
        raise ToolRuntimeError(exc.code, str(exc)) from exc
    if identity.run_id != run_id:
        raise ToolRuntimeError("EVIDENCE_SCOPE_INVALID", "evidence_id does not belong to run_id")
    return {
        "evidence_id": evidence.evidence_id,
        "run_id": run_id,
        "alias": identity.alias,
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

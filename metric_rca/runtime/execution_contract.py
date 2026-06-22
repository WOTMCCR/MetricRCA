"""Explicit error precedence and SQL-audit accounting for plan execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActionExecutionResolution:
    actual_sql_count: int
    declared_sql_count: int
    primary_error: str | None
    sql_count_mismatch: bool
    audit_available: bool


def resolve_action_execution(
    *,
    tool_ok: bool,
    tool_error_code: str | None,
    audit_before: int | None,
    audit_after: int | None,
    declared_sql_count: int,
    audit_required: bool,
) -> ActionExecutionResolution:
    """Resolve one action using an explicit precedence ladder.

    Precedence:
    1. Required SQL audit unavailable or internally inconsistent.
    2. The tool's typed failure.
    3. SQL-count mismatch for an otherwise successful tool.
    """

    declared = int(declared_sql_count)
    if declared < 0:
        return ActionExecutionResolution(
            actual_sql_count=0,
            declared_sql_count=declared,
            primary_error="TOOL_SQL_COUNT_INVALID",
            sql_count_mismatch=False,
            audit_available=audit_before is not None and audit_after is not None,
        )

    if audit_before is None or audit_after is None:
        if audit_required or declared != 0:
            return ActionExecutionResolution(
                actual_sql_count=0,
                declared_sql_count=declared,
                primary_error="SQL_AUDIT_UNAVAILABLE",
                sql_count_mismatch=False,
                audit_available=False,
            )
        return ActionExecutionResolution(
            actual_sql_count=0,
            declared_sql_count=declared,
            primary_error=(tool_error_code or "TOOL_EXECUTION_FAILED") if not tool_ok else None,
            sql_count_mismatch=False,
            audit_available=False,
        )

    actual = int(audit_after) - int(audit_before)
    if actual < 0:
        return ActionExecutionResolution(
            actual_sql_count=0,
            declared_sql_count=declared,
            primary_error="SQL_AUDIT_INVALID",
            sql_count_mismatch=False,
            audit_available=True,
        )

    mismatch = actual != declared
    if not tool_ok:
        primary_error = tool_error_code or "TOOL_EXECUTION_FAILED"
    elif mismatch:
        primary_error = "TOOL_SQL_COUNT_MISMATCH"
    else:
        primary_error = None

    return ActionExecutionResolution(
        actual_sql_count=actual,
        declared_sql_count=declared,
        primary_error=primary_error,
        sql_count_mismatch=mismatch,
        audit_available=True,
    )

"""Resolve tool results and SQL audit accounting with an explicit precedence ladder."""

from __future__ import annotations

from dataclasses import dataclass


SQL_AUDIT_UNAVAILABLE = "SQL_AUDIT_UNAVAILABLE"
TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
TOOL_SQL_COUNT_MISMATCH = "TOOL_SQL_COUNT_MISMATCH"


@dataclass(frozen=True)
class ActionExecutionDecision:
    """Authoritative decision for one executed runtime action.

    Error precedence is intentional and independent of executor line ordering:

    1. Required SQL audit data must be available and internally consistent.
    2. A failed tool keeps its typed error as the primary error.
    3. A successful tool fails when its declared SQL count differs from audit.

    ``sql_count_mismatch`` remains diagnostic when a failed tool already owns the
    primary error.
    """

    primary_error: str | None
    actual_sql_count: int
    declared_sql_count: int
    sql_count_mismatch: bool
    audit_available: bool



def resolve_action_execution(
    *,
    tool_ok: bool,
    tool_error_code: str | None,
    audit_count_before: int | None,
    audit_count_after: int | None,
    declared_sql_count: int,
    audit_required: bool,
) -> ActionExecutionDecision:
    """Resolve one action using repository audit rows as the SQL-count authority."""

    declared = int(declared_sql_count)
    audit_available = audit_count_before is not None and audit_count_after is not None

    if not audit_available:
        if audit_required or declared != 0:
            return ActionExecutionDecision(
                primary_error=SQL_AUDIT_UNAVAILABLE,
                actual_sql_count=0,
                declared_sql_count=declared,
                sql_count_mismatch=False,
                audit_available=False,
            )
        actual = 0
    else:
        actual = int(audit_count_after) - int(audit_count_before)
        if actual < 0:
            return ActionExecutionDecision(
                primary_error=SQL_AUDIT_UNAVAILABLE,
                actual_sql_count=0,
                declared_sql_count=declared,
                sql_count_mismatch=False,
                audit_available=True,
            )

    mismatch = actual != declared
    if not tool_ok:
        primary_error = tool_error_code or TOOL_EXECUTION_FAILED
    elif mismatch:
        primary_error = TOOL_SQL_COUNT_MISMATCH
    else:
        primary_error = None

    return ActionExecutionDecision(
        primary_error=primary_error,
        actual_sql_count=actual,
        declared_sql_count=declared,
        sql_count_mismatch=mismatch,
        audit_available=audit_available,
    )

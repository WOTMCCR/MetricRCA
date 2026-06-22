from __future__ import annotations

from metric_rca.runtime.execution_contract import resolve_action_execution


def test_failed_tool_typed_error_precedes_sql_count_mismatch() -> None:
    resolution = resolve_action_execution(
        tool_ok=False,
        tool_error_code="SYSTEM_TABLE_WRITE_FAILED",
        audit_before=3,
        audit_after=5,
        declared_sql_count=0,
        audit_required=True,
    )

    assert resolution.primary_error == "SYSTEM_TABLE_WRITE_FAILED"
    assert resolution.actual_sql_count == 2
    assert resolution.sql_count_mismatch is True


def test_successful_tool_sql_count_mismatch_is_terminal() -> None:
    resolution = resolve_action_execution(
        tool_ok=True,
        tool_error_code=None,
        audit_before=3,
        audit_after=5,
        declared_sql_count=1,
        audit_required=True,
    )

    assert resolution.primary_error == "TOOL_SQL_COUNT_MISMATCH"
    assert resolution.actual_sql_count == 2


def test_required_audit_unavailable_precedes_other_diagnostics() -> None:
    resolution = resolve_action_execution(
        tool_ok=False,
        tool_error_code="QUERY_SPEC_INVALID",
        audit_before=None,
        audit_after=None,
        declared_sql_count=2,
        audit_required=True,
    )

    assert resolution.primary_error == "SQL_AUDIT_UNAVAILABLE"
    assert resolution.audit_available is False

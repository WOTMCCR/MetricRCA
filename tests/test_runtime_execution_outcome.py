from __future__ import annotations

from metric_rca.runtime.execution_outcome import (
    SQL_AUDIT_UNAVAILABLE,
    TOOL_SQL_COUNT_MISMATCH,
    resolve_action_execution,
)


def test_failed_tool_keeps_typed_error_when_sql_count_differs() -> None:
    decision = resolve_action_execution(
        tool_ok=False,
        tool_error_code="SYSTEM_TABLE_WRITE_FAILED",
        audit_count_before=10,
        audit_count_after=12,
        declared_sql_count=0,
        audit_required=True,
    )

    assert decision.primary_error == "SYSTEM_TABLE_WRITE_FAILED"
    assert decision.actual_sql_count == 2
    assert decision.declared_sql_count == 0
    assert decision.sql_count_mismatch is True
    assert decision.audit_available is True


def test_successful_tool_fails_on_sql_count_mismatch() -> None:
    decision = resolve_action_execution(
        tool_ok=True,
        tool_error_code=None,
        audit_count_before=3,
        audit_count_after=5,
        declared_sql_count=1,
        audit_required=True,
    )

    assert decision.primary_error == TOOL_SQL_COUNT_MISMATCH
    assert decision.actual_sql_count == 2
    assert decision.sql_count_mismatch is True


def test_required_audit_unavailable_is_terminal() -> None:
    decision = resolve_action_execution(
        tool_ok=True,
        tool_error_code=None,
        audit_count_before=None,
        audit_count_after=None,
        declared_sql_count=0,
        audit_required=True,
    )

    assert decision.primary_error == SQL_AUDIT_UNAVAILABLE
    assert decision.audit_available is False


def test_non_data_action_without_audit_accepts_zero_declared_count() -> None:
    decision = resolve_action_execution(
        tool_ok=True,
        tool_error_code=None,
        audit_count_before=None,
        audit_count_after=None,
        declared_sql_count=0,
        audit_required=False,
    )

    assert decision.primary_error is None
    assert decision.actual_sql_count == 0
    assert decision.sql_count_mismatch is False
    assert decision.audit_available is False


def test_non_data_action_without_audit_rejects_nonzero_declared_count() -> None:
    decision = resolve_action_execution(
        tool_ok=True,
        tool_error_code=None,
        audit_count_before=None,
        audit_count_after=None,
        declared_sql_count=1,
        audit_required=False,
    )

    assert decision.primary_error == SQL_AUDIT_UNAVAILABLE


def test_decreasing_audit_count_is_treated_as_unavailable() -> None:
    decision = resolve_action_execution(
        tool_ok=True,
        tool_error_code=None,
        audit_count_before=5,
        audit_count_after=4,
        declared_sql_count=0,
        audit_required=True,
    )

    assert decision.primary_error == SQL_AUDIT_UNAVAILABLE

"""Deterministic executor for compiled RCA plans."""

from __future__ import annotations

from typing import Any

from metric_rca.observability.trace import TraceWriteError
from metric_rca.runtime.action_gate import ActionGate
from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_models import ExecutionResult, RcaAction, RcaPlan
from metric_rca.runtime.run_context import RunContext


DATA_ACTION_KINDS = frozenset(
    {
        "detect_anomaly",
        "drilldown_dimension",
        "select_signal_element",
        "fetch_related_signal",
        "calculate_contribution",
    }
)


class RcaPlanExecutor:
    def __init__(self, *, action_gate: Any | None = None, tool_executor: Any, trace_writer: Any | None = None) -> None:
        self._action_gate = action_gate or ActionGate()
        self._tool_executor = tool_executor
        self._trace_writer = trace_writer

    def execute(self, ctx: RunContext, plan: RcaPlan) -> ExecutionResult:
        graph = EvidenceGraph.from_repository(run_id=ctx.run_id, repository=ctx.repository) if ctx.repository else EvidenceGraph(run_id=ctx.run_id)
        produced: list[str] = [*graph.evidence_ids]

        for index, action in enumerate(plan.actions):
            decision = self._action_gate.validate(ctx, action, graph)
            if not decision.allowed:
                error_code = decision.error_code or "ACTION_GATE_DENIED"
                ctx.mark_failed(error_code)
                trace_error = self._write_trace_step(
                    ctx,
                    action,
                    output_summary={"allowed": False},
                    error_code=error_code,
                )
                if trace_error is not None:
                    ctx.mark_failed(trace_error)
                    return ExecutionResult(status="failed", error_code=trace_error, produced_evidence_ids=produced)
                return ExecutionResult(status="failed", error_code=error_code, produced_evidence_ids=produced)

            audit_required = action.kind in DATA_ACTION_KINDS
            audit_count_before = _sql_audit_count(ctx, required=audit_required)
            if audit_count_before == "SQL_AUDIT_UNAVAILABLE":
                ctx.mark_failed("SQL_AUDIT_UNAVAILABLE")
                return ExecutionResult(status="failed", error_code="SQL_AUDIT_UNAVAILABLE", produced_evidence_ids=produced)
            result = self._tool_executor.execute(ctx, action, graph)
            audit_count_after = _sql_audit_count(ctx, required=audit_required)
            if audit_count_after == "SQL_AUDIT_UNAVAILABLE":
                ctx.mark_failed("SQL_AUDIT_UNAVAILABLE")
                return ExecutionResult(status="failed", error_code="SQL_AUDIT_UNAVAILABLE", produced_evidence_ids=produced)
            audit_delta = _authoritative_sql_delta(
                before=audit_count_before,
                after=audit_count_after,
                declared_sql_count=result.sql_count,
            )
            if audit_delta == "SQL_AUDIT_UNAVAILABLE":
                ctx.mark_failed("SQL_AUDIT_UNAVAILABLE")
                return ExecutionResult(status="failed", error_code="SQL_AUDIT_UNAVAILABLE", produced_evidence_ids=produced)
            if audit_delta == "TOOL_SQL_COUNT_MISMATCH":
                ctx.mark_failed("TOOL_SQL_COUNT_MISMATCH")
                trace_error = self._write_trace_step(
                    ctx,
                    action,
                    output_summary={
                        "ok": False,
                        "error_code": "TOOL_SQL_COUNT_MISMATCH",
                        "declared_sql_count": result.sql_count,
                        "sql_audit_delta": (
                            audit_count_after - audit_count_before
                            if audit_count_before is not None and audit_count_after is not None
                            else None
                        ),
                    },
                    error_code="TOOL_SQL_COUNT_MISMATCH",
                )
                if trace_error is not None:
                    ctx.mark_failed(trace_error)
                    return ExecutionResult(status="failed", error_code=trace_error, produced_evidence_ids=produced)
                return ExecutionResult(status="failed", error_code="TOOL_SQL_COUNT_MISMATCH", produced_evidence_ids=produced)

            sql_delta = int(audit_delta)
            ctx.record_allowed_action(action.kind, sql_count=sql_delta)
            trace_error = self._write_trace_step(
                ctx,
                action,
                output_summary={
                    "ok": result.observation.ok,
                    "error_code": result.observation.error_code,
                    "evidence_ids": result.evidence_ids,
                    "payload": result.observation.payload,
                    "declared_sql_count": result.sql_count,
                    "sql_audit_delta": sql_delta,
                    "budget": {
                        "step_count": ctx.step_count,
                        "query_count": ctx.query_count,
                        "drilldown_depth": ctx.drilldown_depth,
                    },
                },
                error_code=result.observation.error_code,
            )
            if trace_error is not None:
                ctx.mark_failed(trace_error)
                return ExecutionResult(status="failed", error_code=trace_error, produced_evidence_ids=produced)
            if not result.observation.ok:
                error_code = result.observation.error_code or "TOOL_EXECUTION_FAILED"
                ctx.mark_failed(error_code)
                return ExecutionResult(status="failed", error_code=error_code, produced_evidence_ids=produced)
            max_query = int(ctx.budget.get("max_query", 0))
            if action.kind in DATA_ACTION_KINDS and (
                ctx.query_count > max_query
                or (ctx.query_count >= max_query and _has_remaining_data_action(plan.actions[index + 1 :]))
            ):
                ctx.mark_failed("BUDGET_EXCEEDED")
                return ExecutionResult(status="failed", error_code="BUDGET_EXCEEDED", produced_evidence_ids=produced)

            scope_error = _add_evidence(graph, result.evidence_ids)
            if scope_error is not None:
                ctx.mark_failed(scope_error)
                return ExecutionResult(status="failed", error_code=scope_error, produced_evidence_ids=produced)
            for evidence_id in result.evidence_ids:
                if evidence_id not in produced:
                    produced.append(evidence_id)

            if _is_no_anomaly_stop(action, result.observation.payload, result.observation.error_code):
                return ExecutionResult(
                    status="no_anomaly",
                    error_code="NO_ANOMALY_DETECTED",
                    produced_evidence_ids=produced,
                )

        return ExecutionResult(status="succeeded", produced_evidence_ids=produced)

    def _write_trace_step(
        self,
        ctx: RunContext,
        action: RcaAction,
        *,
        output_summary: dict[str, Any],
        error_code: str | None,
    ) -> str | None:
        if self._trace_writer is None:
            return None
        try:
            self._trace_writer.write_step(
                run_id=ctx.run_id,
                node="runtime_plan_executor",
                action=action.kind,
                input_summary={
                    "action_id": action.action_id,
                    "requires": action.requires,
                    "produces": action.produces,
                    "args": action.args,
                },
                output_summary=output_summary,
                error_code=error_code,
            )
        except TraceWriteError as exc:
            return exc.code
        return None


def _add_evidence(graph: EvidenceGraph, evidence_ids: list[str]) -> str | None:
    try:
        graph.add_ids(evidence_ids)
    except ValueError as exc:
        if str(exc) == "EVIDENCE_SCOPE_INVALID":
            return "EVIDENCE_SCOPE_INVALID"
        return "EVIDENCE_GRAPH_INVALID"
    return None


def _sql_audit_count(ctx: RunContext, *, required: bool) -> int | None | str:
    if ctx.repository is None:
        return "SQL_AUDIT_UNAVAILABLE" if required else None
    get_rows = getattr(ctx.repository, "get_sql_audit_rows", None)
    if not callable(get_rows):
        return "SQL_AUDIT_UNAVAILABLE" if required else None
    rows = get_rows(ctx.run_id)
    return len(list(rows or []))


def _authoritative_sql_delta(
    *,
    before: int | None,
    after: int | None,
    declared_sql_count: int,
) -> int | str:
    if before is None or after is None:
        if int(declared_sql_count) != 0:
            return "SQL_AUDIT_UNAVAILABLE"
        return int(declared_sql_count)
    delta = after - before
    if delta != int(declared_sql_count):
        return "TOOL_SQL_COUNT_MISMATCH"
    return delta


def _has_remaining_data_action(actions: list[RcaAction]) -> bool:
    return any(action.kind in DATA_ACTION_KINDS for action in actions)


def _is_no_anomaly_stop(action: RcaAction, payload: dict[str, Any], error_code: str | None) -> bool:
    if action.kind != "detect_anomaly":
        return False
    return payload.get("is_anomaly") is False or error_code == "NO_ANOMALY_DETECTED"

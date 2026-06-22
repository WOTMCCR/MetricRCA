from __future__ import annotations

from datetime import date

from metric_rca.domain.models import Observation
from metric_rca.runtime.plan_executor import RcaPlanExecutor
from metric_rca.runtime.plan_models import GateDecision, RcaAction, RcaPlan
from metric_rca.runtime.run_context import RunContext
from metric_rca.runtime.sdk_tools import ToolExecutionResult


def test_plan_executor_runs_allowed_actions_and_collects_evidence() -> None:
    actions = [
        RcaAction(
            action_id="A1",
            kind="detect_anomaly",
            args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "filters": {}},
            produces=["E1"],
        ),
        RcaAction(
            action_id="A2",
            kind="drilldown_dimension",
            args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "dimension": "channel"},
            requires=["E1"],
            produces=["E2_channel"],
        ),
    ]
    tool = _ToolExecutor(
        {
            "A1": ToolExecutionResult(
                observation=Observation(action_name="detect_anomaly", ok=True, payload={"is_anomaly": True}),
                evidence_ids=["run-1:E1"],
            ),
            "A2": ToolExecutionResult(
                observation=Observation(action_name="drilldown_dimension", ok=True),
                evidence_ids=["run-1:E2_channel"],
            ),
        }
    )

    result = RcaPlanExecutor(action_gate=_Gate(), tool_executor=tool).execute(
        RunContext(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            repository=_AuditRepository([0, 0, 0, 0]),
        ),
        _plan(actions),
    )

    assert result.status == "succeeded"
    assert result.produced_evidence_ids == ["run-1:E1", "run-1:E2_channel"]
    assert tool.calls == ["A1", "A2"]


def test_plan_executor_writes_trace_steps_for_executed_actions() -> None:
    actions = [
        RcaAction(
            action_id="A1",
            kind="detect_anomaly",
            args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "filters": {}},
            produces=["E1"],
        ),
        RcaAction(
            action_id="A2",
            kind="rank_root_causes",
            args={"metric_id": "gmv", "target_date": date(2026, 6, 5)},
            requires=["E1"],
            produces=["E_rank"],
        ),
    ]
    tool = _ToolExecutor(
        {
            "A1": ToolExecutionResult(
                observation=Observation(action_name="detect_anomaly", ok=True, payload={"is_anomaly": True}),
                evidence_ids=["run-1:E1"],
            ),
            "A2": ToolExecutionResult(
                observation=Observation(action_name="rank_root_causes", ok=True, payload={"selected_candidate": {}}),
                evidence_ids=["run-1:E_rank"],
            ),
        }
    )
    trace_writer = _TraceWriter()

    result = RcaPlanExecutor(action_gate=_Gate(), tool_executor=tool, trace_writer=trace_writer).execute(
        RunContext(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            repository=_AuditRepository([0, 0]),
        ),
        _plan(actions),
    )

    assert result.status == "succeeded"
    assert [step["action"] for step in trace_writer.steps] == ["detect_anomaly", "rank_root_causes"]
    assert trace_writer.steps[0]["input_summary"]["action_id"] == "A1"
    assert trace_writer.steps[0]["output_summary"]["evidence_ids"] == ["run-1:E1"]


def test_plan_executor_stops_after_detect_no_anomaly() -> None:
    actions = [
        RcaAction(
            action_id="A1",
            kind="detect_anomaly",
            args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "filters": {}},
            produces=["E1"],
        ),
        RcaAction(
            action_id="A2",
            kind="drilldown_dimension",
            args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "dimension": "channel"},
            requires=["E1"],
        ),
    ]
    tool = _ToolExecutor(
        {
            "A1": ToolExecutionResult(
                observation=Observation(
                    action_name="detect_anomaly",
                    ok=True,
                    payload={"is_anomaly": False},
                    error_code="NO_ANOMALY_DETECTED",
                ),
                evidence_ids=["run-1:E1"],
            )
        }
    )

    result = RcaPlanExecutor(action_gate=_Gate(), tool_executor=tool).execute(
        RunContext(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            repository=_AuditRepository([0, 0]),
        ),
        _plan(actions),
    )

    assert result.status == "no_anomaly"
    assert result.error_code == "NO_ANOMALY_DETECTED"
    assert result.produced_evidence_ids == ["run-1:E1"]
    assert tool.calls == ["A1"]


def test_plan_executor_returns_gate_failure_without_tool_call() -> None:
    action = RcaAction(
        action_id="A1",
        kind="detect_anomaly",
        args={"metric_id": "uv", "target_date": date(2026, 6, 5), "filters": {}},
    )
    tool = _ToolExecutor({})

    result = RcaPlanExecutor(
        action_gate=_Gate({"A1": GateDecision(allowed=False, error_code="METRIC_SCOPE_VIOLATION")}),
        tool_executor=tool,
    ).execute(
        RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5)),
        _plan([action]),
    )

    assert result.status == "failed"
    assert result.error_code == "METRIC_SCOPE_VIOLATION"
    assert tool.calls == []


def test_plan_executor_returns_tool_failure_with_typed_error_code() -> None:
    action = RcaAction(
        action_id="A1",
        kind="detect_anomaly",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "filters": {}},
    )
    tool = _ToolExecutor(
        {
            "A1": ToolExecutionResult(
                observation=Observation(
                    action_name="detect_anomaly",
                    ok=False,
                    error_code="QUERY_SPEC_INVALID",
                )
            )
        }
    )

    result = RcaPlanExecutor(action_gate=_Gate(), tool_executor=tool).execute(
        RunContext(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            repository=_AuditRepository([0, 0]),
        ),
        _plan([action]),
    )

    assert result.status == "failed"
    assert result.error_code == "QUERY_SPEC_INVALID"
    assert tool.calls == ["A1"]


def test_plan_executor_consumes_authoritative_sql_audit_delta_for_budget() -> None:
    actions = [
        RcaAction(
            action_id="A1",
            kind="detect_anomaly",
            args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "filters": {}},
            produces=["E1"],
        ),
        RcaAction(
            action_id="A2",
            kind="drilldown_dimension",
            args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "dimension": "channel"},
            requires=["E1"],
            produces=["E2_channel"],
        ),
    ]
    repository = _AuditRepository([0, 2, 2])
    tool = _ToolExecutor(
        {
            "A1": ToolExecutionResult(
                observation=Observation(action_name="detect_anomaly", ok=True, payload={"is_anomaly": True}),
                evidence_ids=["run-1:E1"],
                sql_count=2,
            ),
            "A2": ToolExecutionResult(
                observation=Observation(action_name="drilldown_dimension", ok=True),
                evidence_ids=["run-1:E2_channel"],
                sql_count=0,
            ),
        }
    )

    result = RcaPlanExecutor(action_gate=_Gate(), tool_executor=tool).execute(
        RunContext(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            budget={"max_steps": 8, "max_query": 2, "max_drilldown_depth": 3},
            repository=repository,
        ),
        _plan(actions),
    )

    assert result.status == "failed"
    assert result.error_code == "BUDGET_EXCEEDED"
    assert tool.calls == ["A1"]


def test_plan_executor_fails_when_declared_sql_count_differs_from_audit_delta() -> None:
    action = RcaAction(
        action_id="A1",
        kind="detect_anomaly",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "filters": {}},
        produces=["E1"],
    )
    repository = _AuditRepository([0, 2])
    tool = _ToolExecutor(
        {
            "A1": ToolExecutionResult(
                observation=Observation(action_name="detect_anomaly", ok=True, payload={"is_anomaly": True}),
                evidence_ids=["run-1:E1"],
                sql_count=1,
            )
        }
    )

    result = RcaPlanExecutor(action_gate=_Gate(), tool_executor=tool).execute(
        RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5), repository=repository),
        _plan([action]),
    )

    assert result.status == "failed"
    assert result.error_code == "TOOL_SQL_COUNT_MISMATCH"
    assert tool.calls == ["A1"]


def test_plan_executor_preserves_tool_error_when_failed_action_audit_delta_differs() -> None:
    action = RcaAction(
        action_id="A1",
        kind="select_signal_element",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "dimension": "channel"},
        produces=["E_select_channel"],
    )
    repository = _AuditRepository([0, 2])
    tool = _ToolExecutor(
        {
            "A1": ToolExecutionResult(
                observation=Observation(
                    action_name="select_signal_element",
                    ok=False,
                    error_code="SYSTEM_TABLE_WRITE_FAILED",
                ),
                sql_count=0,
            )
        }
    )
    trace_writer = _TraceWriter()

    result = RcaPlanExecutor(action_gate=_Gate(), tool_executor=tool, trace_writer=trace_writer).execute(
        RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5), repository=repository),
        _plan([action]),
    )

    assert result.status == "failed"
    assert result.error_code == "SYSTEM_TABLE_WRITE_FAILED"
    assert tool.calls == ["A1"]
    assert trace_writer.steps[0]["error_code"] == "SYSTEM_TABLE_WRITE_FAILED"
    assert trace_writer.steps[0]["output_summary"]["declared_sql_count"] == 0
    assert trace_writer.steps[0]["output_summary"]["sql_audit_delta"] == 2


def test_plan_executor_fails_data_action_without_sql_audit_repository() -> None:
    action = RcaAction(
        action_id="A1",
        kind="detect_anomaly",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "filters": {}},
        produces=["E1"],
    )
    tool = _ToolExecutor(
        {
            "A1": ToolExecutionResult(
                observation=Observation(action_name="detect_anomaly", ok=True, payload={"is_anomaly": True}),
                evidence_ids=["run-1:E1"],
                sql_count=2,
            )
        }
    )

    result = RcaPlanExecutor(action_gate=_Gate(), tool_executor=tool).execute(
        RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5)),
        _plan([action]),
    )

    assert result.status == "failed"
    assert result.error_code == "SQL_AUDIT_UNAVAILABLE"
    assert tool.calls == []


def _plan(actions: list[RcaAction]) -> RcaPlan:
    return RcaPlan(
        run_id="run-1",
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        family="gmv_family",
        actions=actions,
        budget={"max_steps": 8, "max_query": 20, "max_drilldown_depth": 3},
    )


class _Gate:
    def __init__(self, decisions: dict[str, GateDecision] | None = None) -> None:
        self._decisions = decisions or {}

    def validate(self, ctx: RunContext, action: RcaAction, evidence_graph):
        return self._decisions.get(action.action_id, GateDecision(allowed=True))


class _ToolExecutor:
    def __init__(self, results: dict[str, ToolExecutionResult]) -> None:
        self._results = results
        self.calls: list[str] = []

    def execute(self, ctx: RunContext, action: RcaAction, evidence_graph):
        self.calls.append(action.action_id)
        return self._results[action.action_id]


class _TraceWriter:
    def __init__(self) -> None:
        self.steps = []

    def write_step(self, **kwargs):
        self.steps.append(kwargs)


class _AuditRepository:
    def __init__(self, audit_counts: list[int]) -> None:
        self._audit_counts = audit_counts
        self._calls = 0

    def get_sql_audit_rows(self, run_id: str):
        index = min(self._calls, len(self._audit_counts) - 1)
        self._calls += 1
        return [{"audit_id": number} for number in range(self._audit_counts[index])]

    def get_evidence(self, *, run_id: str, evidence_id: str):
        return None

    def get_evidences(self, run_id: str):
        return []

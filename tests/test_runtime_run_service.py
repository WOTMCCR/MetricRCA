from __future__ import annotations

from datetime import date
from typing import Any

from metric_rca.runtime.plan_models import ExecutionResult, RcaAction, RcaPlan
from metric_rca.runtime.run_service import RunService
from metric_rca.services.metric_contracts import MetricServiceError, ParsedIntent


def test_run_service_executes_parse_compile_plan_path() -> None:
    parsed = ParsedIntent(metric_id="gmv", target_date=date(2026, 6, 5), question_family="gmv_drop")
    trace = _TraceWriter()
    repository = _Repository()
    compiler = _PlanCompiler(
        RcaPlan(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            question_family="gmv_drop",
            family="gmv_family",
            actions=[
                RcaAction(
                    action_id="A1",
                    kind="detect_anomaly",
                    args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "filters": {}},
                )
            ],
            budget={"max_steps": 8, "max_query": 12, "max_drilldown_depth": 3},
        )
    )
    executor = _PlanExecutor(ExecutionResult(status="succeeded", produced_evidence_ids=["run-1:E1", "run-1:E_rank"]))

    result = RunService(
        dependencies=_Dependencies(repository=repository, metric_service=_MetricService(parsed), trace_writer=trace),
        plan_compiler=compiler,
        plan_executor=executor,
        report_projector=lambda run_id, status: {"run_id": run_id, "status": status, "metric_id": "gmv"},
        reflection_verifier=lambda run_id, repair_count, parsed_intent: _Reflection(passed=True),
    ).run("why did GMV drop?", run_id="run-1")

    assert result["status"] == "succeeded"
    assert result["report"] == {"run_id": "run-1", "status": "succeeded", "metric_id": "gmv"}
    assert compiler.parsed_intent == parsed
    assert executor.plan is compiler.plan
    assert trace.started == [("run-1", "why did GMV drop?")]
    assert trace.finished[-1] == ("run-1", "succeeded", None)


def test_run_service_returns_no_anomaly_without_reflection_repair() -> None:
    parsed = ParsedIntent(metric_id="gmv", target_date=date(2026, 6, 5), question_family="gmv_drop")
    result = RunService(
        dependencies=_Dependencies(metric_service=_MetricService(parsed), trace_writer=_TraceWriter()),
        plan_compiler=_PlanCompiler(_plan("run-1")),
        plan_executor=_PlanExecutor(ExecutionResult(status="no_anomaly", error_code="NO_ANOMALY_DETECTED")),
        report_projector=lambda run_id, status: {"run_id": run_id, "status": status},
        reflection_verifier=lambda run_id, repair_count, parsed_intent: _Reflection(passed=True),
    ).run("why", run_id="run-1")

    assert result["status"] == "no_anomaly"
    assert result["error_code"] is None
    assert result["report"]["status"] == "no_anomaly"


def test_run_service_fails_parse_errors_with_typed_code() -> None:
    trace = _TraceWriter()
    result = RunService(
        dependencies=_Dependencies(metric_service=_FailingMetricService("PARSE_FAILED"), trace_writer=trace),
        plan_compiler=_PlanCompiler(_plan("run-1")),
        plan_executor=_PlanExecutor(ExecutionResult(status="succeeded")),
        report_projector=lambda run_id, status: {},
        reflection_verifier=lambda run_id, repair_count, parsed_intent: _Reflection(passed=True),
    ).run("   ", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "PARSE_FAILED"
    assert trace.finished[-1] == ("run-1", "failed", "PARSE_FAILED")


def test_run_service_does_not_swallow_untyped_parse_errors() -> None:
    service = RunService(
        dependencies=_Dependencies(metric_service=_UntypedFailingMetricService(), trace_writer=_TraceWriter()),
        plan_compiler=_PlanCompiler(_plan("run-1")),
        plan_executor=_PlanExecutor(ExecutionResult(status="succeeded")),
        report_projector=lambda run_id, status: {},
        reflection_verifier=lambda run_id, repair_count, parsed_intent: _Reflection(passed=True),
    )

    try:
        service.run("why", run_id="run-1")
    except TypeError as exc:
        assert str(exc) == "programmer bug"
    else:
        raise AssertionError("RunService swallowed an untyped parse failure")


def test_run_service_fails_plan_execution_errors_with_typed_code() -> None:
    parsed = ParsedIntent(metric_id="gmv", target_date=date(2026, 6, 5), question_family="gmv_drop")
    trace = _TraceWriter()

    result = RunService(
        dependencies=_Dependencies(metric_service=_MetricService(parsed), trace_writer=trace),
        plan_compiler=_PlanCompiler(_plan("run-1")),
        plan_executor=_PlanExecutor(ExecutionResult(status="failed", error_code="EVIDENCE_MISSING")),
        report_projector=lambda run_id, status: {},
        reflection_verifier=lambda run_id, repair_count, parsed_intent: _Reflection(passed=True),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "EVIDENCE_MISSING"
    assert trace.finished[-1] == ("run-1", "failed", "EVIDENCE_MISSING")


def test_run_service_fails_invalid_reflection_contract_without_empty_payload_fallback() -> None:
    parsed = ParsedIntent(metric_id="gmv", target_date=date(2026, 6, 5), question_family="gmv_drop")
    trace = _TraceWriter()

    result = RunService(
        dependencies=_Dependencies(metric_service=_MetricService(parsed), trace_writer=trace),
        plan_compiler=_PlanCompiler(_plan("run-1")),
        plan_executor=_PlanExecutor(ExecutionResult(status="succeeded")),
        report_projector=lambda run_id, status: {"run_id": run_id, "status": status},
        reflection_verifier=lambda run_id, repair_count, parsed_intent: object(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "REFLECTION_OUTPUT_INVALID"
    assert trace.finished[-1] == ("run-1", "failed", "REFLECTION_OUTPUT_INVALID")


def _plan(run_id: str) -> RcaPlan:
    return RcaPlan(
        run_id=run_id,
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        family="gmv_family",
        actions=[],
        budget={"max_steps": 8, "max_query": 12, "max_drilldown_depth": 3},
    )


class _Dependencies:
    def __init__(
        self,
        *,
        repository: Any | None = None,
        metric_service: Any,
        trace_writer: Any,
        settings: Any | None = None,
        renderer: Any | None = None,
    ) -> None:
        self.repository = repository or _Repository()
        self.metric_service = metric_service
        self.trace_writer = trace_writer
        self.settings = settings or _Settings()
        self.renderer = renderer
        self.memory_repo = None


class _Settings:
    business_today = date(2026, 6, 6)
    target_date = date(2026, 6, 5)
    memory_enabled = False


class _Repository:
    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []

    def get_trace_steps(self, run_id: str) -> list[dict[str, Any]]:
        return []

    def get_evidences(self, run_id: str) -> list[dict[str, Any]]:
        return []

    def get_operation_tasks(self, run_id: str) -> list[dict[str, Any]]:
        return []

    def get_agent_run(self, run_id: str) -> dict[str, Any]:
        return {"run_id": run_id, "target_date": date(2026, 6, 5)}

    def create_operation_task(self, task: dict[str, Any]) -> None:
        self.tasks.append(task)


class _TraceWriter:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.finished: list[tuple[str, str, str | None]] = []

    def start_run(self, *, run_id: str, question: str, target_date: date) -> None:
        self.started.append((run_id, question))

    def set_run_context(self, *, run_id: str, metric_id: str, target_date: date) -> None:
        pass

    def finish_run(self, *, run_id: str, status: str, error_code: str | None, **kwargs: Any) -> None:
        self.finished.append((run_id, status, error_code))


class _MetricService:
    def __init__(self, parsed: ParsedIntent) -> None:
        self._parsed = parsed

    def parse_question(self, question: str, *, business_today: date) -> ParsedIntent:
        return self._parsed


class _FailingMetricService:
    def __init__(self, code: str) -> None:
        self.code = code

    def parse_question(self, question: str, *, business_today: date) -> ParsedIntent:
        raise MetricServiceError(self.code, self.code)


class _UntypedFailingMetricService:
    def parse_question(self, question: str, *, business_today: date) -> ParsedIntent:
        raise TypeError("programmer bug")


class _PlanCompiler:
    def __init__(self, plan: RcaPlan) -> None:
        self.plan = plan
        self.parsed_intent: ParsedIntent | None = None

    def compile(self, *, run_id: str, parsed_intent: ParsedIntent, budget: dict[str, int] | None = None) -> RcaPlan:
        self.parsed_intent = parsed_intent
        return self.plan


class _PlanExecutor:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.plan: RcaPlan | None = None

    def execute(self, ctx, plan: RcaPlan) -> ExecutionResult:
        self.plan = plan
        return self.result


class _Reflection:
    def __init__(self, *, passed: bool) -> None:
        self.passed = passed
        self.repair_count = 0

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {"passed": self.passed, "repair_count": self.repair_count, "issues": []}

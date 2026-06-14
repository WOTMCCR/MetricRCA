from __future__ import annotations

from pathlib import Path

from tests.test_middleware import _context, _message, _request
from tests.test_orchestrator import _Agent, _FailingMemoryRepo, _FailingMemoryWriteRepo, _Repo, _deps

from metric_rca.agent.middleware import GuardMiddleware
from metric_rca.agent.runner import RunOrchestrator
from metric_rca.agent.tools.runtime import ToolRuntimeError, execute_guarded_plan
from metric_rca.domain.models import SQLPlan


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = [ROOT / "metric_rca" / "services", ROOT / "metric_rca" / "agent"]


def test_runtime_code_does_not_read_anomaly_ground_truth() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _runtime_sources()
        if "anomaly_ground_truth" in path.read_text()
    ]
    assert offenders == []


def test_services_and_tools_have_no_hardcoded_metric_definitions() -> None:
    offenders: list[str] = []
    for path in _runtime_sources():
        source = path.read_text()
        if "METRIC_DEFINITIONS" in source:
            offenders.append(f"{path.relative_to(ROOT)}:METRIC_DEFINITIONS")
        if "MetricDefinition(" in source:
            offenders.append(f"{path.relative_to(ROOT)}:MetricDefinition(")
    assert offenders == []


def test_llm_unavailable_fails_before_agent_loop() -> None:
    repo = _Repo()
    result = RunOrchestrator(
        dependencies=_deps(repo, llm_api_key=None),
        agent_factory=lambda **kwargs: _Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "LLM_REQUIRED_UNAVAILABLE"


def test_illegal_tool_args_observation_tool_not_executed() -> None:
    writer = _TraceWriter()
    middleware = GuardMiddleware(_context(writer))
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]})

    result = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05", "illegal": "x"}),
        handler,
    )

    assert "ACTION_SCHEMA_INVALID" in result.content
    assert called is False


def test_budget_exhausted_then_llm_attempts_data_tool_run_failed() -> None:
    writer = _TraceWriter()
    middleware = GuardMiddleware(_context(writer, max_query=0))

    first = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]}),
    )
    second = middleware.wrap_tool_call(
        _request("drilldown_dimension", {"metric_id": "gmv", "target_date": "2026-06-05", "dimension": "channel", "evidence_ids": ["run-1:E1"]}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E2"]}),
    )

    assert "BUDGET_EXCEEDED" in first.content
    assert "query budget exhausted; call rank_root_causes or stop" in first.content
    assert "data tool attempted after budget exhaustion" in second.content
    assert middleware.context.failed is True


def test_sql_guard_rejection_cannot_be_bypassed() -> None:
    plan = SQLPlan(sql="SELECT * FROM fact_order", sql_hash="x", guard_status="rejected", guard_errors=["star"])
    repo = _ExecutingRepo()

    try:
        execute_guarded_plan(repository=repo, plan=plan, run_id="run-1")
    except ToolRuntimeError as exc:
        assert exc.code == "SQL_GUARD_REJECTED"
    else:
        raise AssertionError("expected SQL_GUARD_REJECTED")

    assert repo.executed is False


def test_no_anomaly_with_drilldown_or_rank_trace_fails() -> None:
    for action in ["rank_root_causes", "fetch_related_signal"]:
        repo = _Repo()

        class Agent(_Agent):
            def invoke(self, *args, **kwargs):
                repo.add_evidence("run-1", "E1", {"is_anomaly": False})
                repo.trace_steps.append({"run_id": "run-1", "seq": 2, "node": "tool_call", "action": action})
                return {}

        result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

        assert result["status"] == "failed"
        assert result["error_code"] == "NO_ANOMALY_CONTRACT_VIOLATED"


def test_reflection_repair_exhausted_no_report() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_evidence("run-1", "E1", {"is_anomaly": True})
            return {}

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert result.get("report") is None


def test_memory_read_write_failure_fails_run() -> None:
    repo = _Repo()
    read_result = RunOrchestrator(
        dependencies=_deps(repo, memory_required=True, memory_repo=_FailingMemoryRepo()),
        agent_factory=lambda **kwargs: _Agent(),
    ).run("why", run_id="run-1")

    assert read_result["status"] == "failed"
    assert read_result["error_code"] == "MEMORY_READ_FAILED"

    write_repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            write_repo.add_valid_evidences("run-2")
            return {}

    write_result = RunOrchestrator(
        dependencies=_deps(write_repo, memory_required=True, memory_repo=_FailingMemoryWriteRepo()),
        agent_factory=lambda **kwargs: Agent(),
    ).run("why", run_id="run-2")

    assert write_result["status"] == "failed"
    assert write_result["error_code"] == "MEMORY_WRITE_FAILED"


def test_empty_result_set_no_attribute_or_rank() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def __init__(self, middleware):
            self.middleware = middleware

        def invoke(self, *args, **kwargs):
            self.middleware.wrap_tool_call(
                _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
                lambda request: _message(
                    request,
                    {"observation": {"ok": False, "error_code": "INSUFFICIENT_BASELINE_DATA"}, "evidence_ids": []},
                ),
            )
            repo.add_valid_evidences("run-1")
            return {}

    def factory(**kwargs):
        return Agent(kwargs["middleware"][0])

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=factory).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "INSUFFICIENT_BASELINE_DATA"
    assert "report" not in result


def _runtime_sources() -> list[Path]:
    return [
        path
        for directory in RUNTIME_DIRS
        if directory.exists()
        for path in directory.rglob("*.py")
    ]


class _TraceWriter:
    def __init__(self) -> None:
        self.steps = []

    def write_step(self, **kwargs) -> None:
        self.steps.append(kwargs)


class _ExecutingRepo:
    executed = False

    def execute_plan(self, *args, **kwargs):
        self.executed = True
        return []

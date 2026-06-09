from __future__ import annotations

from pathlib import Path

import pytest

from metric_rca.agent.graph import route_after_execute_tool
from metric_rca.agent.nodes.create_tasks import create_tasks
from metric_rca.agent.nodes.read_memory import read_memory
from metric_rca.agent.nodes.write_memory import write_memory
from metric_rca.agent.react import next_action, validate_action
from metric_rca.agent.tools.runtime import ToolRuntimeError, execute_guarded_plan
from metric_rca.config.settings import Settings
from metric_rca.domain.models import AgentAction, Observation, SQLPlan


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = [ROOT / "metric_rca" / "services", ROOT / "metric_rca" / "agent"]


def _runtime_sources() -> list[Path]:
    return [
        path
        for directory in RUNTIME_DIRS
        if directory.exists()
        for path in directory.rglob("*.py")
    ]


def test_runtime_code_does_not_read_anomaly_ground_truth() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _runtime_sources()
        if "anomaly_ground_truth" in path.read_text()
    ]
    assert offenders == []


def test_services_do_not_import_db_or_repository_modules() -> None:
    forbidden = ["MetricRepository", "SQLRenderer", "SQLGuard", "create_engine", "pandas.read_sql", "pymysql"]
    offenders: list[str] = []
    for path in (ROOT / "metric_rca" / "services").glob("*.py"):
        source = path.read_text()
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(ROOT)}:{token}")
    assert offenders == []


def test_tools_and_services_have_no_direct_sql_or_broad_continue_fallbacks() -> None:
    forbidden = ["read_sql", "create_engine", "pymysql", ".execute(", "except Exception", "continue"]
    offenders: list[str] = []
    for path in _runtime_sources():
        source = path.read_text()
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(ROOT)}:{token}")
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


def test_services_and_tools_have_no_hardcoded_schema_context() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _runtime_sources()
        if "SCHEMA_CONTEXT" in path.read_text()
    ]
    assert offenders == []


def test_services_and_tools_have_no_hardcoded_dimension_values() -> None:
    forbidden = [
        "_CHANNELS",
        "_CATEGORIES",
        "paid_ads",
        "organic",
        "affiliate",
        "electronics",
        "fashion",
        "home",
    ]
    offenders: list[str] = []
    for path in _runtime_sources():
        source = path.read_text()
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(ROOT)}:{token}")
    assert offenders == []


def test_gmv_decomposition_does_not_reference_order_count_or_pay_orders() -> None:
    source = (ROOT / "metric_rca" / "services" / "attribution_service.py").read_text()
    assert "pay_orders" not in source
    assert "order_count" not in source


def test_llm_required_unavailable_fails() -> None:
    action = next_action(
        {
            "run_id": "run-1",
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "parsed_spec": {"filters": {}},
            "actions": [],
            "observations": [],
            "evidences": [],
            "step_count": 0,
        },
        settings=Settings.model_construct(llm_enabled=True, llm_required=True, llm_provider=None, llm_api_key=None),
        metric_service=_MetricService(),
    )

    assert action.action == "finish"
    assert action.args["error_code"] == "LLM_REQUIRED_UNAVAILABLE"


def test_illegal_action_records_error_and_does_not_execute_tool() -> None:
    validated, observation = validate_action(AgentAction(action="unsafe_action", args={}))

    assert validated is None
    assert observation is not None
    assert observation.ok is False
    assert observation.error_code == "ACTION_SCHEMA_INVALID"


def test_memory_required_read_failure_fails_run() -> None:
    result = read_memory(
        {"run_id": "run-1", "metric_id": "gmv", "parsed_spec": {"dimension": "channel"}},
        dependencies=_Dependencies(Settings.model_construct(memory_enabled=True)),
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "MEMORY_READ_FAILED"


def test_memory_required_write_failure_fails_run() -> None:
    result = write_memory(
        {"run_id": "run-1", "status": "succeeded"},
        dependencies=_Dependencies(Settings.model_construct(memory_enabled=True)),
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "MEMORY_WRITE_FAILED"


def test_missing_trace_writer_fails_typed_not_silent_noop() -> None:
    deps = _Dependencies(Settings.model_construct(memory_enabled=False))
    deps.trace_writer = None

    result = read_memory(
        {"run_id": "run-1", "metric_id": "gmv", "parsed_spec": {"dimension": "channel"}},
        dependencies=deps,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "SYSTEM_TABLE_WRITE_FAILED"


def test_empty_result_does_not_enter_attribute_rank() -> None:
    state = {
        "observations": [Observation(action_name="calculate_contribution", ok=False, error_code="NO_CURRENT_DATA")],
        "error_code": "NO_CURRENT_DATA",
    }

    assert route_after_execute_tool(state, dependencies=_Dependencies()) == "error_return"


def test_sql_execution_failure_routes_error_return() -> None:
    state = {
        "observations": [
            Observation(action_name="detect_anomaly", ok=False, error_code="SQL_EXECUTION_FAILED")
        ],
        "error_code": "SQL_EXECUTION_FAILED",
    }

    assert route_after_execute_tool(state, dependencies=_Dependencies()) == "error_return"


def test_sql_guard_rejection_cannot_bypass_renderer() -> None:
    repo = _Repo()
    rejected = SQLPlan(sql="SELECT * FROM fact_order", sql_hash="0" * 64, guard_status="rejected")

    with pytest.raises(ToolRuntimeError) as exc_info:
        execute_guarded_plan(repository=repo, plan=rejected, run_id="run-1")

    assert exc_info.value.code == "SQL_GUARD_REJECTED"
    assert repo.executed == 0


def test_no_anomaly_skips_create_tasks() -> None:
    repo = _Repo()
    result = create_tasks(
        {"run_id": "run-1", "status": "no_anomaly", "candidates": []},
        dependencies=_Dependencies(repository=repo),
    )

    assert result == {}
    assert repo.tasks == []


class _MetricService:
    def get_metric_definition(self, metric_id: str):
        return type("Definition", (), {"allowed_dimensions": ["channel"]})()


class _Dependencies:
    def __init__(self, settings=None, repository=None) -> None:
        self.settings = settings or Settings.model_construct(
            memory_enabled=False,
            llm_enabled=False,
            llm_required=False,
            max_steps=8,
            signal_metric_by_type={"campaign": "gmv"},
        )
        self.repository = repository or _Repo()
        self.metric_service = _MetricService()
        self.renderer = None
        self.trace_writer = _InMemoryTraceWriter()
        self.memory_repo = None


class _Repo:
    def __init__(self) -> None:
        self.executed = 0
        self.tasks: list[dict] = []

    def execute_plan(self, plan: SQLPlan, *, run_id: str):
        self.executed += 1

    def create_operation_task(self, row: dict) -> None:
        self.tasks.append(row)


class _InMemoryTraceWriter:
    def write_step(self, **kwargs) -> None:
        return None

    def finish_run(self, **kwargs) -> None:
        return None

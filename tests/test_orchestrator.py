from __future__ import annotations

from datetime import date, datetime
import importlib
from pathlib import Path
from types import SimpleNamespace

from metric_rca.agent.deep_tools import EXPOSED_TOOL_NAMES
from metric_rca.agent.factory import create_metric_rca_agent
from metric_rca.agent.runner import AgentDependencies, RunOrchestrator
from metric_rca.observability.trace import TraceWriter

ROOT = Path(__file__).resolve().parents[1]


def test_factory_exposes_whitelist_plus_write_todos() -> None:
    repo = _Repo()
    deps = _deps(repo)
    captured = {}

    def fake_agent_factory(**kwargs):
        captured.update(kwargs)
        return _CompiledAgent(EXPOSED_TOOL_NAMES)

    bundle = create_metric_rca_agent(dependencies=deps, run_id="run-1", agent_factory=fake_agent_factory)

    assert bundle.exposed_tool_names == EXPOSED_TOOL_NAMES
    assert {tool.name for tool in captured["tools"]} == EXPOSED_TOOL_NAMES - {"write_todos"}
    assert captured["model"] == "openai:gpt-test"
    assert captured["subagents"] == []


def test_factory_rejects_injected_agent_with_filesystem_tools() -> None:
    repo = _Repo()

    def unsafe_factory(**kwargs):
        return _CompiledAgent(EXPOSED_TOOL_NAMES | {"read_file"})

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=unsafe_factory).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "DEEPAGENTS_FILESYSTEM_TOOLS_UNDISABLEABLE"


def test_real_deepagents_compiled_graph_exposes_only_metric_rca_tools() -> None:
    importlib.import_module("deepagents")
    repo = _Repo()

    bundle = create_metric_rca_agent(dependencies=_deps(repo), run_id="run-real")
    exposed = _compiled_tool_names(bundle.agent)
    print(f"compiled_tool_names={sorted(exposed)}")

    assert exposed == EXPOSED_TOOL_NAMES
    assert {"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"}.isdisjoint(exposed)


def test_legacy_graph_modules_removed() -> None:
    legacy_paths = [
        ROOT / "metric_rca" / "agent" / "graph.py",
        ROOT / "metric_rca" / "agent" / "state.py",
        ROOT / "metric_rca" / "agent" / "react.py",
        ROOT / "metric_rca" / "agent" / "nodes",
        ROOT / "tests" / "test_graph.py",
        ROOT / "tests" / "test_react.py",
    ]

    assert [str(path.relative_to(ROOT)) for path in legacy_paths if path.exists()] == []


def test_factory_construction_error_is_typed_llm_unavailable() -> None:
    repo = _Repo()

    def broken_factory(**kwargs):
        raise RuntimeError("provider transport failed")

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=broken_factory).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "LLM_REQUIRED_UNAVAILABLE"


def test_llm_unavailable_fails_before_agent_loop() -> None:
    repo = _Repo()
    deps = _deps(repo, llm_api_key=None)

    result = RunOrchestrator(dependencies=deps, agent_factory=lambda **kwargs: _Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "LLM_REQUIRED_UNAVAILABLE"
    assert repo.runs["run-1"]["status"] == "failed"


def test_orchestrator_injects_run_context_into_agent_message() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def invoke(self, payload, **kwargs):
            captured["content"] = payload["messages"][0]["content"]
            return {}

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("Why did yesterday GMV drop?", run_id="run-1")

    assert result["status"] == "failed"
    assert "target_date: 2026-06-05" in captured["content"]
    assert "allowed metric_id values:" in captured["content"]
    assert "gmv" in captured["content"]
    assert "Use metric_id exactly as listed above" in captured["content"]


def test_orchestrator_seeds_explicit_question_scope_into_guard_context() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            captured["scope"] = captured["middleware"].context.explicit_filters
            return {}

    def factory(**kwargs):
        captured["middleware"] = kwargs["middleware"][0]
        return Agent()

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=factory).run(
        "Why did yesterday category=electronics GMV drop?",
        run_id="run-1",
    )

    assert result["status"] == "failed"
    assert captured["scope"] == {"category": "electronics"}


def test_no_anomaly_with_drilldown_trace_fails() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_evidence("run-1", "E1", {"is_anomaly": False})
            repo.trace_steps.append({"run_id": "run-1", "seq": 2, "node": "tool_call", "action": "drilldown_dimension"})
            return {}

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "NO_ANOMALY_CONTRACT_VIOLATED"


def test_agent_invoke_unknown_error_is_typed_agent_invoke_failed() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            raise ValueError("provider rejected request")

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "AGENT_INVOKE_FAILED"


def test_agent_invoke_coded_error_preserves_code() -> None:
    repo = _Repo()

    class CodedInvokeError(RuntimeError):
        code = "TRACE_WRITE_FAILED"

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            raise CodedInvokeError("trace writer failed")

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "TRACE_WRITE_FAILED"


def test_orchestrator_persists_token_usage_when_llm_makes_no_tool_call() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            kwargs["config"]["callbacks"][0].context.record_token_usage(
                {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
            )
            return {}

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert repo.trace_steps[-1]["node"] == "llm_call"
    assert repo.trace_steps[-1]["token_usage"] == {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}


def test_repair_guard_failure_stops_before_report() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def __init__(self, middleware):
            self.middleware = middleware
            self.calls = 0

        def invoke(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                repo.add_valid_evidences("run-1", missing_e3=True)
            else:
                repo.add_valid_evidences("run-1")
                self.middleware.context.mark_failed("ACTION_SCHEMA_INVALID")
            return {}

    def factory(**kwargs):
        return Agent(kwargs["middleware"][0])

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=factory).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "ACTION_SCHEMA_INVALID"
    assert result.get("report") is None


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


def test_memory_read_failure_fails_run_when_required() -> None:
    repo = _Repo()
    deps = _deps(repo, memory_required=True, memory_repo=_FailingMemoryRepo())

    result = RunOrchestrator(dependencies=deps, agent_factory=lambda **kwargs: _Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "MEMORY_READ_FAILED"


def test_memory_write_failure_fails_run_when_required() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(
        dependencies=_deps(repo, memory_required=True, memory_repo=_FailingMemoryWriteRepo()),
        agent_factory=lambda **kwargs: Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "MEMORY_WRITE_FAILED"


def test_successful_run_creates_operation_task_from_report() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "succeeded"
    assert repo.tasks[0]["root_cause_type"] == "campaign_traffic_drop"


def _deps(repo: "_Repo", *, llm_api_key: str | None = "key", memory_required: bool = False, memory_repo=None) -> AgentDependencies:
    settings = SimpleNamespace(
        llm_provider="openai",
        llm_model="gpt-test",
        llm_api_key=llm_api_key,
        target_date=date(2026, 6, 5),
        max_steps=8,
        max_query=12,
        max_drilldown_depth=2,
        max_repair=1,
        memory_enabled=memory_repo is not None,
        memory_required=memory_required,
        multi_agent_enabled=False,
    )
    return AgentDependencies(
        settings=settings,
        repository=repo,
        metric_service=SimpleNamespace(),
        renderer=SimpleNamespace(),
        trace_writer=TraceWriter(repo),
        memory_repo=memory_repo,
    )


class _Agent:
    nodes = {
        "tools": SimpleNamespace(
            bound=SimpleNamespace(_tools_by_name={name: object() for name in EXPOSED_TOOL_NAMES})
        )
    }

    def invoke(self, *args, **kwargs):
        return {}


class _CompiledAgent(_Agent):
    def __init__(self, tool_names: set[str] | frozenset[str]) -> None:
        self.nodes = {
            "tools": SimpleNamespace(
                bound=SimpleNamespace(_tools_by_name={name: object() for name in tool_names})
            )
        }


def _compiled_tool_names(agent) -> set[str]:
    return set(agent.nodes["tools"].bound._tools_by_name)


class _FailingMemoryRepo:
    def read(self, *args, **kwargs):
        raise RuntimeError("MEMORY_READ_FAILED")


class _FailingMemoryWriteRepo:
    def read(self, *args, **kwargs):
        return None

    def write(self, *args, **kwargs):
        raise RuntimeError("MEMORY_WRITE_FAILED")


class _Repo:
    def __init__(self) -> None:
        self.runs = {}
        self.evidences = {}
        self.trace_steps = []
        self.tasks = []

    def create_agent_run(self, row: dict) -> None:
        self.runs[row["run_id"]] = dict(row)

    def update_agent_run_context(self, *, run_id: str, metric_id: str, target_date) -> None:
        self.runs[run_id]["metric_id"] = metric_id
        self.runs[run_id]["target_date"] = target_date

    def finish_agent_run(self, *, run_id: str, status: str, error_code: str | None, finished_at) -> None:
        self.runs[run_id]["status"] = status
        self.runs[run_id]["error_code"] = error_code
        self.runs[run_id]["finished_at"] = finished_at

    def create_trace_step(self, row: dict) -> None:
        self.trace_steps.append(dict(row))

    def get_trace_steps(self, run_id: str) -> list[dict]:
        return [row for row in self.trace_steps if row.get("run_id") == run_id]

    def get_agent_run(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict | None:
        return self.evidences.get(evidence_id)

    def get_evidences(self, run_id: str) -> list[dict]:
        return [row for row in self.evidences.values() if row["run_id"] == run_id]

    def get_operation_tasks(self, run_id: str) -> list[dict]:
        return list(self.tasks)

    def create_operation_task(self, row: dict) -> None:
        self.tasks.append(dict(row))

    def add_evidence(self, run_id: str, alias: str, result_summary: dict) -> None:
        evidence_id = f"{run_id}:{alias}"
        self.evidences[evidence_id] = {
            "evidence_id": evidence_id,
            "run_id": run_id,
            "query_spec": {
                "metric_id": "gmv",
                "time_range": {"start_date": "2026-06-05", "end_date": "2026-06-05", "tz": "Asia/Tokyo"},
                "group_by": [],
                "filters": {},
                "limit": 1000,
                "purpose": "current",
                "signal_type": "metric",
            },
            "sql_text": "SELECT order_amount FROM fact_order WHERE business_date = :target_date LIMIT 1000",
            "sql_hash": "0" * 64,
            "guard_status": "passed",
            "result_summary": result_summary,
            "data_source": "fact_order",
            "created_at": datetime(2026, 6, 6),
        }

    def add_valid_evidences(self, run_id: str, *, missing_e3: bool = False) -> None:
        if run_id in self.runs:
            self.update_agent_run_context(run_id=run_id, metric_id="gmv", target_date=date(2026, 6, 5))
        candidate = {
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "contribution_pct": 0.9,
            "signal_severity": 0.9,
            "evidence_support": 1.0,
            "reflection_factor": 1.0,
            "eng_confidence": 0.9,
            "verdict": "confirmed",
            "evidence_ids": [f"{run_id}:E1", f"{run_id}:E2", f"{run_id}:E4"]
            if missing_e3
            else [f"{run_id}:E1", f"{run_id}:E2", f"{run_id}:E3", f"{run_id}:E4"],
        }
        self.add_evidence(run_id, "E1", {"metric_id": "gmv", "target_date": "2026-06-05", "is_anomaly": True, "value": 0.9})
        self.add_evidence(run_id, "E2", {"metric_id": "gmv", "target_date": "2026-06-05", "value": 0.9})
        if not missing_e3:
            self.add_evidence(
                run_id,
                "E3",
                {
                    "metric_id": "gmv",
                    "target_date": "2026-06-05",
                    "signal_type": "campaign",
                    "signal_metric_id": "gmv",
                    "dimension": "channel",
                    "element": "paid_ads",
                    "value": 0.9,
                },
            )
        self.add_evidence(
            run_id,
            "E4",
            {
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "selected_candidate": candidate,
                "candidates": [candidate],
                "value": 0.9,
            },
        )

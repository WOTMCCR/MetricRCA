from __future__ import annotations

from datetime import date, datetime
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.outputs import LLMResult

from metric_rca.agent.deep_tools import EXPOSED_TOOL_NAMES
from metric_rca.agent.deep_tools import PLANNING_TOOL_NAME, TOOL_ARG_SCHEMAS
from metric_rca.agent.factory import create_metric_rca_agent
from metric_rca.agent.prompts import EXPERT_SYSTEM_PROMPT
from metric_rca.agent.runner import AgentDependencies, RunOrchestrator, run_rca
from metric_rca.services.metric_contracts import ParsedIntent
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
    assert bundle.guard_context.tool_arg_schemas == dict(TOOL_ARG_SCHEMAS)


def test_tool_arg_schema_registry_covers_every_exposed_metric_rca_tool() -> None:
    assert set(TOOL_ARG_SCHEMAS) == EXPOSED_TOOL_NAMES - {PLANNING_TOOL_NAME}


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


def test_run_context_write_failure_returns_typed_failed_run() -> None:
    repo = _ContextFailingRepo()

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: _Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "SYSTEM_TABLE_WRITE_FAILED"
    assert repo.runs["run-1"]["status"] == "failed"


def test_orchestrator_injects_run_context_into_agent_message() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def invoke(self, payload, **kwargs):
            captured.setdefault("content", payload["messages"][0]["content"])
            return {}

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("Why did yesterday GMV drop?", run_id="run-1")

    assert result["status"] == "failed"
    assert "target_date: 2026-06-05" in captured["content"]
    assert "parsed target metric_id: gmv" in captured["content"]
    assert "Discovery policy is mandatory" in captured["content"]
    assert "allowed metric_id values:" in captured["content"]
    assert "gmv" in captured["content"]
    assert "Every metric_id argument MUST equal the parsed target metric_id" in captured["content"]


def test_orchestrator_anchors_run_metric_to_parsed_intent_before_agent_loop() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def invoke(self, payload, **kwargs):
            captured.setdefault("content", payload["messages"][0]["content"])
            captured["target_metric_id"] = captured["middleware"].context.target_metric_id
            return {}

    def factory(**kwargs):
        captured["middleware"] = kwargs["middleware"][0]
        return Agent()

    result = RunOrchestrator(
        dependencies=_deps(repo, metric_service=_MetricService(metric_id="pay_cvr")),
        agent_factory=factory,
    ).run("Why did conversion rate drop yesterday?", run_id="run-1")

    assert result["status"] == "failed"
    assert repo.runs["run-1"]["metric_id"] == "pay_cvr"
    assert captured["target_metric_id"] == "pay_cvr"
    assert "parsed target metric_id: pay_cvr" in captured["content"]


def test_orchestrator_does_not_parse_literal_question_scope_into_guard_context() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def invoke(self, payload, **kwargs):
            captured.setdefault("content", payload["messages"][0]["content"])
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
    assert captured["scope"] == {}
    assert "explicit or parsed question filters: none" in captured["content"]


def test_orchestrator_seeds_parsed_natural_scope_into_guard_context_and_message() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def invoke(self, payload, **kwargs):
            captured.setdefault("content", payload["messages"][0]["content"])
            captured["scope"] = captured["middleware"].context.explicit_filters
            return {}

    def factory(**kwargs):
        captured["middleware"] = kwargs["middleware"][0]
        return Agent()

    result = RunOrchestrator(
        dependencies=_deps(
            repo,
            metric_service=_MetricService(
                metric_id="gmv",
                dimension="category",
                element="electronics",
            ),
        ),
        agent_factory=factory,
    ).run("Why did electronics GMV fall yesterday?", run_id="run-1")

    assert result["status"] == "failed"
    assert captured["scope"] == {"category": "electronics"}
    assert "explicit or parsed question filters: category=electronics" in captured["content"]


def test_orchestrator_seeds_discovery_policy_from_parsed_intent() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def invoke(self, payload, **kwargs):
            context = captured["middleware"].context
            captured.setdefault("content", payload["messages"][0]["content"])
            captured["policy"] = context.discovery_policy
            return {}

    def factory(**kwargs):
        captured["middleware"] = kwargs["middleware"][0]
        return Agent()

    result = RunOrchestrator(
        dependencies=_deps(repo, metric_service=_MetricService(analysis_strategy="product_first")),
        agent_factory=factory,
    ).run("Why did yesterday's GMV decline in merchandise sales?", run_id="run-1")

    assert result["status"] == "failed"
    policy = captured["policy"]
    assert policy.required_drilldowns == ("channel", "category", "product")
    assert policy.first_signal_dimension == "product"
    assert policy.first_signal_type == "inventory"
    assert policy.enforce_first_signal_top_candidate is True
    assert "parsed analysis_strategy: product_first" in captured["content"]
    assert "first_signal=product:inventory" in captured["content"]


def test_orchestrator_standard_gmv_policy_uses_channel_campaign_first_signal() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def invoke(self, payload, **kwargs):
            context = captured["middleware"].context
            captured.setdefault("content", payload["messages"][0]["content"])
            captured["policy"] = context.discovery_policy
            return {}

    def factory(**kwargs):
        captured["middleware"] = kwargs["middleware"][0]
        return Agent()

    result = RunOrchestrator(
        dependencies=_deps(repo, metric_service=_MetricService(analysis_strategy="standard")),
        agent_factory=factory,
    ).run("Why did overall GMV fall yesterday?", run_id="run-1")

    assert result["status"] == "failed"
    policy = captured["policy"]
    assert policy.required_drilldowns == ("channel", "category", "product")
    assert policy.first_signal_dimension == "channel"
    assert policy.first_signal_type == "campaign"
    assert policy.enforce_first_signal_top_candidate is True
    assert "parsed analysis_strategy: standard" in captured["content"]
    assert "first_signal=channel:campaign" in captured["content"]


def test_expert_prompt_guides_broad_rate_family_discovery() -> None:
    prompt = EXPERT_SYSTEM_PROMPT

    assert "broad pay_cvr or conversion-rate questions" in prompt
    assert "drilldown_dimension for device" in prompt
    assert "signal_type=conversion" in prompt
    assert "broad refund_rate or refund-rate questions" in prompt
    assert "drilldown_dimension for product" in prompt
    assert "signal_type=refund_quality" in prompt
    assert "broad uv or traffic questions" in prompt
    assert "drilldown_dimension for channel" in prompt
    assert "signal_type=campaign" in prompt


def test_orchestrator_seeds_first_signal_element_from_parsed_intent() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def invoke(self, payload, **kwargs):
            context = captured["middleware"].context
            captured.setdefault("content", payload["messages"][0]["content"])
            captured["policy"] = context.discovery_policy
            return {}

    def factory(**kwargs):
        captured["middleware"] = kwargs["middleware"][0]
        return Agent()

    result = RunOrchestrator(
        dependencies=_deps(repo, metric_service=_MetricService(analysis_strategy="organic_first")),
        agent_factory=factory,
    ).run("Why did yesterday's GMV fall despite stable merchandising?", run_id="run-1")

    assert result["status"] == "failed"
    policy = captured["policy"]
    assert policy.required_drilldowns == ("channel", "category", "product")
    assert policy.first_signal_dimension == "channel"
    assert policy.first_signal_type == "campaign"
    assert policy.first_signal_element == "organic"
    assert policy.enforce_first_signal_top_candidate is False
    assert "parsed analysis_strategy: organic_first" in captured["content"]
    assert "first_signal=channel:campaign" in captured["content"]
    assert "first_signal_element=organic" in captured["content"]


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


def test_no_anomaly_without_projected_report_fails_fast() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_evidence("run-1", "E1", {"is_anomaly": False})
            return {}

    class NoReportOrchestrator(RunOrchestrator):
        def _project_report(self, run_id: str, *, status: str) -> dict[str, Any] | None:
            assert status == "no_anomaly"
            return None

    result = NoReportOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "REPORT_PROJECTION_FAILED"
    assert repo.runs["run-1"]["status"] == "failed"


def test_agent_invoke_unknown_error_is_typed_agent_invoke_failed() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            raise ValueError("provider rejected request")

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "AGENT_INVOKE_FAILED"


def test_agent_invoke_provider_rate_limit_is_retryable_typed_error() -> None:
    repo = _Repo()

    class ProviderRateLimit(RuntimeError):
        status_code = 429

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            raise ProviderRateLimit("too many requests")

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "RATE_LIMIT_EXCEEDED"
    assert repo.runs["run-1"]["error_code"] == "RATE_LIMIT_EXCEEDED"


def test_agent_invoke_provider_server_code_is_retryable_llm_unavailable() -> None:
    repo = _Repo()

    class ProviderServerError(RuntimeError):
        code = "server_error"

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            raise ProviderServerError("internal server error")

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "LLM_REQUIRED_UNAVAILABLE"
    assert repo.runs["run-1"]["error_code"] == "LLM_REQUIRED_UNAVAILABLE"


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


def test_typed_system_error_with_timeout_text_is_not_reclassified_as_llm_transient() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            raise RuntimeError("SYSTEM_TABLE_WRITE_FAILED: TimeoutError: QueuePool limit timeout")

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "SYSTEM_TABLE_WRITE_FAILED"
    assert repo.runs["run-1"]["error_code"] == "SYSTEM_TABLE_WRITE_FAILED"


def test_run_rca_closes_owned_memory_repository(monkeypatch) -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()
    memory_repo.closed = False

    def close_memory_repo():
        memory_repo.closed = True

    memory_repo.close = close_memory_repo

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_valid_evidences("run-1")
            return {}

    settings = SimpleNamespace(
        llm_provider="openai",
        llm_model="gpt-test",
        llm_api_key="key",
        business_today=date(2026, 6, 6),
        target_date=date(2026, 6, 5),
        max_steps=8,
        max_query=12,
        max_drilldown_depth=2,
        max_repair=1,
        memory_enabled=True,
        memory_required=False,
        multi_agent_enabled=False,
    )
    monkeypatch.setattr(
        "metric_rca.agent.runner.MemoryRepository.from_settings",
        lambda *args, **kwargs: memory_repo,
    )

    result = run_rca(
        "why",
        run_id="run-1",
        settings=settings,
        repository=repo,
        metric_service=_MetricService(),
        trace_writer=TraceWriter(repo),
        agent_factory=lambda **kwargs: Agent(),
    )

    assert result["status"] == "succeeded"
    assert memory_repo.closed is True


def test_terminal_ranked_evidence_allows_transient_llm_error_to_finish_report() -> None:
    repo = _Repo()

    class RateLimitError(RuntimeError):
        code = "rate_limit_exceeded"

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_valid_evidences("run-1")
            e4_summary = repo.evidences["run-1:E4"]["result_summary"]
            repo.add_evidence(
                "run-1",
                "E_rank",
                {
                    "metric_id": "gmv",
                    "ranker": "adtributor_internal",
                    "selected_candidate": e4_summary["selected_candidate"],
                    "candidates": e4_summary["candidates"],
                },
            )
            raise RateLimitError("rate limit after terminal tool evidence")

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "succeeded"
    assert result["error_code"] is None
    assert repo.tasks[0]["root_cause_type"] == "campaign_traffic_drop"


def test_transient_llm_error_without_terminal_evidence_still_fails_fast() -> None:
    repo = _Repo()

    class RateLimitError(RuntimeError):
        code = "rate_limit_exceeded"

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_evidence("run-1", "E1", {"metric_id": "gmv", "target_date": "2026-06-05", "is_anomaly": True})
            raise RateLimitError("rate limit before rank evidence")

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "RATE_LIMIT_EXCEEDED"
    assert repo.tasks == []


def test_reflection_repair_constrains_next_tool_to_suggested_action() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def __init__(self, middleware):
            self.middleware = middleware
            self.calls = 0

        def invoke(self, payload, **kwargs):
            self.calls += 1
            if self.calls == 1:
                repo.add_valid_evidences("run-1")
                e4_summary = repo.evidences["run-1:E4"]["result_summary"]
                e4_summary["selected_candidate"]["contribution_pct"] = 0.4
                e4_summary["candidates"][0]["contribution_pct"] = 0.4
                return {}
            captured["repair_content"] = payload["messages"][0]["content"]
            captured["required_repair_action"] = self.middleware.context.required_repair_action
            e4_summary = repo.evidences["run-1:E4"]["result_summary"]
            e4_summary["selected_candidate"]["contribution_pct"] = 0.9
            e4_summary["candidates"][0]["contribution_pct"] = 0.9
            repo.add_evidence(
                "run-1",
                "E_rank",
                {
                    "metric_id": "gmv",
                    "ranker": "adtributor_internal",
                    "selected_candidate": e4_summary["selected_candidate"],
                    "candidates": e4_summary["candidates"],
                },
            )
            return {}

    def factory(**kwargs):
        return Agent(kwargs["middleware"][0])

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=factory).run("why", run_id="run-1")

    assert result["status"] == "succeeded"
    assert captured["required_repair_action"] == "rank_root_causes"
    assert "Only call rank_root_causes" in captured["repair_content"]
    assert "Call exactly this tool with exactly these JSON args: rank_root_causes" in captured["repair_content"]
    assert '"target_date": "2026-06-05"' in captured["repair_content"]
    assert "datetime.date(" not in captured["repair_content"]
    assert "Do not answer in text" in captured["repair_content"]
    assert "Do not call detect_anomaly" in captured["repair_content"]


def test_reflection_repair_recovers_when_initial_agent_makes_no_tool_calls() -> None:
    repo = _Repo()
    captured = {}

    class Agent(_Agent):
        def __init__(self, middleware):
            self.middleware = middleware
            self.calls = 0

        def invoke(self, payload, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {}
            captured["repair_content"] = payload["messages"][0]["content"]
            captured["required_repair_action"] = self.middleware.context.required_repair_action
            repo.add_valid_evidences("run-1")
            return {}

    def factory(**kwargs):
        return Agent(kwargs["middleware"][0])

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=factory).run("why", run_id="run-1")

    assert result["status"] == "succeeded"
    assert captured["required_repair_action"] == "detect_anomaly"
    assert "Only call detect_anomaly" in captured["repair_content"]
    assert "Call exactly this tool with exactly these JSON args: detect_anomaly" in captured["repair_content"]
    assert "Do not call detect_anomaly or drilldown_dimension during repair" not in captured["repair_content"]


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


def test_memory_repo_without_four_layer_read_interface_fails_no_legacy_fallback() -> None:
    repo = _Repo()
    deps = _deps(repo, memory_required=True, memory_repo=_LegacyMemoryRepo())

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


def test_memory_write_on_finalize_false_preserves_memory_reads_but_skips_run_writes() -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo, memory_write_on_finalize=False),
        agent_factory=lambda **kwargs: Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "succeeded"
    assert {"mem_key": "gmv|semantic", "layers": ("semantic",)} in memory_repo.reads
    assert {"mem_key": "gmv|run", "layers": ("episodic", "reflection", "case")} in memory_repo.reads
    assert memory_repo.writes == []


def test_memory_write_on_finalize_false_skips_failure_reflection_memory() -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()

    result = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo, memory_write_on_finalize=False),
        agent_factory=lambda **kwargs: _Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert "secondary_error_code" not in result
    assert memory_repo.writes == []


def test_reflection_memory_write_failure_preserves_primary_failure_code() -> None:
    repo = _Repo()

    result = RunOrchestrator(
        dependencies=_deps(repo, memory_required=True, memory_repo=_FailingMemoryWriteRepo()),
        agent_factory=lambda **kwargs: _Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert result["secondary_error_code"] == "MEMORY_WRITE_FAILED"
    assert repo.runs["run-1"]["error_code"] == "REFLECTION_REPAIR_FAILED"


def test_post_parse_failure_uses_parsed_metric_for_reflection_memory_key() -> None:
    repo = _ContextFailingRepo()
    memory_repo = _RecordingMemoryRepo()

    result = RunOrchestrator(
        dependencies=_deps(
            repo,
            memory_repo=memory_repo,
            metric_service=_MetricService(metric_id="net_gmv"),
        ),
        agent_factory=lambda **kwargs: _Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "SYSTEM_TABLE_WRITE_FAILED"
    assert "secondary_error_code" not in result
    assert memory_repo.writes[0]["layer"] == "reflection"
    assert memory_repo.writes[0]["mem_key"] == "net_gmv|run"
    assert memory_repo.writes[0]["payload"]["metric_id"] == "net_gmv"


def test_successful_run_writes_episodic_memory_with_verified_root_cause_summary() -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo),
        agent_factory=lambda **kwargs: Agent(),
    ).run("why", run_id="run-1")

    episodic_writes = [row for row in memory_repo.writes if row["layer"] == "episodic"]
    assert result["status"] == "succeeded"
    assert len(episodic_writes) == 1
    assert episodic_writes[0]["payload"] == {
        "run_id": "run-1",
        "metric_id": "gmv",
        "dimension": "channel",
        "root_cause_type": "campaign_traffic_drop",
        "verdict": "confirmed",
    }


def test_successful_scoped_run_writes_scope_into_episodic_memory() -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(
        dependencies=_deps(
            repo,
            memory_repo=memory_repo,
            metric_service=_MetricService(metric_id="gmv", dimension="category", element="electronics"),
        ),
        agent_factory=lambda **kwargs: Agent(),
    ).run("Why did electronics GMV fall yesterday?", run_id="run-1")

    episodic_writes = [row for row in memory_repo.writes if row["layer"] == "episodic"]
    assert result["status"] == "succeeded"
    assert episodic_writes[0]["payload"]["filters"] == {"category": "electronics"}


def test_episodic_memory_is_written_only_after_run_finishes_successfully() -> None:
    repo = _Repo()
    memory_repo = _StatusCheckingMemoryRepo(repo)

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo),
        agent_factory=lambda **kwargs: Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "succeeded"
    assert memory_repo.writes[0]["layer"] == "episodic"
    assert memory_repo.statuses_at_write == ["succeeded"]


def test_failed_run_writes_reflection_memory_without_episodic_summary() -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()

    result = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo),
        agent_factory=lambda **kwargs: _Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert [row["layer"] for row in memory_repo.writes] == ["reflection"]
    assert memory_repo.writes[0]["payload"]["run_id"] == "run-1"
    assert memory_repo.writes[0]["payload"]["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert memory_repo.writes[0]["payload"]["reflection_issues"]
    assert memory_repo.writes[0]["payload"]["reflection_issues"][0]["check"] == "evidence_coverage"


def test_reflection_repair_failure_uses_parsed_metric_for_memory_key() -> None:
    repo = _RunContextDroppingRepo()
    memory_repo = _RecordingMemoryRepo()

    result = RunOrchestrator(
        dependencies=_deps(
            repo,
            memory_repo=memory_repo,
            metric_service=_MetricService(metric_id="net_gmv"),
        ),
        agent_factory=lambda **kwargs: _Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert "secondary_error_code" not in result
    assert memory_repo.writes[0]["layer"] == "reflection"
    assert memory_repo.writes[0]["mem_key"] == "net_gmv|run"
    assert memory_repo.writes[0]["payload"]["metric_id"] == "net_gmv"


def test_agent_invoke_failure_uses_parsed_metric_for_reflection_memory_key() -> None:
    repo = _RunContextDroppingRepo()
    memory_repo = _RecordingMemoryRepo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            raise RuntimeError("LLM_REQUIRED_UNAVAILABLE: provider down")

    result = RunOrchestrator(
        dependencies=_deps(
            repo,
            memory_repo=memory_repo,
            metric_service=_MetricService(metric_id="net_gmv"),
        ),
        agent_factory=lambda **kwargs: Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "LLM_REQUIRED_UNAVAILABLE"
    assert "secondary_error_code" not in result
    assert memory_repo.writes[0]["layer"] == "reflection"
    assert memory_repo.writes[0]["mem_key"] == "net_gmv|run"
    assert memory_repo.writes[0]["payload"]["metric_id"] == "net_gmv"


def test_reflection_state_carries_parsed_filters_for_scoped_repair() -> None:
    repo = _RunContextDroppingRepo()
    parsed_intent = _MetricService(
        metric_id="gmv",
        dimension="category",
        element="electronics",
    ).parse_question("why", business_today=date(2026, 6, 6))
    orchestrator = RunOrchestrator(
        dependencies=_deps(repo, metric_service=_MetricService(metric_id="gmv")),
        agent_factory=lambda **kwargs: _Agent(),
    )
    repo.create_agent_run(
        {
            "run_id": "run-1",
            "question": "why",
            "metric_id": None,
            "target_date": None,
            "status": "running",
            "error_code": None,
            "created_at": datetime(2026, 6, 6),
            "finished_at": None,
        }
    )

    state = orchestrator._reflection_state("run-1", repair_count=0, parsed_intent=parsed_intent)

    assert state["metric_id"] == "gmv"
    assert state["target_date"] == date(2026, 6, 5)
    assert state["parsed_spec"]["filters"] == {"category": "electronics"}


def test_reflection_memory_uses_metric_run_key_and_is_retrievable() -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()
    orchestrator = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo),
        agent_factory=lambda **kwargs: _Agent(),
    )
    repo.create_agent_run(
        {
            "run_id": "run-1",
            "question": "why",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "status": "running",
            "error_code": None,
            "created_at": datetime(2026, 6, 6),
            "finished_at": None,
        }
    )

    orchestrator._write_reflection_memory("run-1", "RATE_LIMIT_EXCEEDED", {"gap_description": "provider transient"})
    hits = orchestrator._read_required_memory(
        "run-1",
        parsed_intent=_MetricService(metric_id="gmv").parse_question("why", business_today=date(2026, 6, 6)),
    )

    assert memory_repo.writes[0]["mem_key"] == "gmv|run"
    assert hits == [memory_repo.writes[0]]
    assert {"mem_key": "gmv|run", "layers": ("episodic", "reflection", "case")} in memory_repo.reads


def test_memory_read_filters_scoped_run_records_by_parsed_scope() -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()
    memory_repo.write(
        {
            "layer": "episodic",
            "mem_key": "gmv|run",
            "payload": {
                "run_id": "previous-1",
                "metric_id": "gmv",
                "filters": {"category": "electronics"},
            },
            "confidence": 0.8,
            "source": "reflection_verified",
        }
    )
    memory_repo.write(
        {
            "layer": "episodic",
            "mem_key": "gmv|run",
            "payload": {
                "run_id": "previous-2",
                "metric_id": "gmv",
                "filters": {"category": "fashion"},
            },
            "confidence": 0.8,
            "source": "reflection_verified",
        }
    )
    orchestrator = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo),
        agent_factory=lambda **kwargs: _Agent(),
    )

    hits = orchestrator._read_required_memory(
        "run-1",
        parsed_intent=_MetricService(
            metric_id="gmv",
            dimension="category",
            element="electronics",
        ).parse_question("why", business_today=date(2026, 6, 6)),
    )

    assert [hit["payload"]["run_id"] for hit in hits] == ["previous-1"]


def test_memory_read_writes_auditable_trace_step() -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()
    orchestrator = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo),
        agent_factory=lambda **kwargs: _Agent(),
    )
    repo.create_agent_run(
        {
            "run_id": "run-1",
            "question": "why",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "status": "running",
            "error_code": None,
            "created_at": datetime(2026, 6, 6),
            "finished_at": None,
        }
    )
    memory_repo.write(
        {
            "memory_id": "mem-semantic",
            "layer": "semantic",
            "mem_key": "gmv|semantic",
            "payload": {"metric_id": "gmv", "dimension": "channel"},
            "confidence": 0.91,
            "source": "system_verified",
        }
    )

    orchestrator._read_required_memory(
        "run-1",
        parsed_intent=_MetricService(metric_id="gmv").parse_question("why", business_today=date(2026, 6, 6)),
    )

    trace_steps = repo.get_trace_steps("run-1")
    memory_steps = [row for row in trace_steps if row["node"] == "memory_read"]
    assert len(memory_steps) == 1
    assert memory_steps[0]["output_summary"]["hit_count"] == 1
    assert memory_steps[0]["output_summary"]["hits"] == [
        {
                "memory_id": "mem-semantic",
                "layer": "semantic",
                "mem_key": "gmv|semantic",
                "filters": {},
                "confidence": 0.91,
                "source": "system_verified",
            }
    ]


def test_reflection_memory_without_metric_id_fails_instead_of_unretrievable_key() -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()
    orchestrator = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo),
        agent_factory=lambda **kwargs: _Agent(),
    )

    try:
        orchestrator._write_reflection_memory("run-1", "LLM_REQUIRED_UNAVAILABLE")
    except RuntimeError as exc:
        assert str(exc) == "MEMORY_WRITE_FAILED: reflection memory requires metric_id"
    else:
        raise AssertionError("reflection memory write without metric_id should fail")

    assert memory_repo.writes == []


def test_memory_context_text_includes_confidence_scores() -> None:
    from metric_rca.agent.runner import _memory_context_text

    text = _memory_context_text(
        [
            {
                "layer": "semantic",
                "mem_key": "gmv|semantic",
                "metric_id": "gmv",
                "dimension": "channel",
                "filters": {"channel": "paid_ads"},
                "confidence": 0.91,
            }
        ]
    )

    assert "confidence" in text
    assert "0.91" in text
    assert "filters" in text
    assert "paid_ads" in text


def test_repaired_run_writes_reflection_memory_with_repair_count() -> None:
    repo = _Repo()
    memory_repo = _RecordingMemoryRepo()

    class Agent(_Agent):
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                repo.add_valid_evidences("run-1", missing_e3=True)
                return {}
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo),
        agent_factory=lambda **kwargs: Agent(),
    ).run("why", run_id="run-1")

    reflection_writes = [row for row in memory_repo.writes if row["layer"] == "reflection"]
    assert result["status"] == "succeeded"
    assert len(reflection_writes) == 1
    assert reflection_writes[0]["payload"]["run_id"] == "run-1"
    assert reflection_writes[0]["payload"]["repair_count"] == 1
    assert reflection_writes[0]["payload"]["reflection_issues"]
    assert reflection_writes[0]["payload"]["reflection_issues"][0]["check"] == "required_evidence_present"


def test_repaired_run_does_not_write_episodic_before_reflection_memory() -> None:
    repo = _Repo()
    memory_repo = _FailingReflectionMemoryRepo()

    class Agent(_Agent):
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                repo.add_valid_evidences("run-1", missing_e3=True)
                return {}
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(
        dependencies=_deps(repo, memory_repo=memory_repo),
        agent_factory=lambda **kwargs: Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "MEMORY_WRITE_FAILED"
    assert memory_repo.writes == []


def test_successful_run_creates_operation_task_from_report() -> None:
    repo = _Repo()

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "succeeded"
    assert repo.tasks[0]["root_cause_type"] == "campaign_traffic_drop"


def test_final_token_usage_trace_failure_is_not_retried_outside_repository() -> None:
    repo = _FlakyFinalTokenTraceRepo(failures_before_success=1)

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            callback = kwargs["config"]["callbacks"][0]
            callback.on_llm_end(
                LLMResult(
                    generations=[],
                    llm_output={"token_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
                )
            )
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "SYSTEM_TABLE_WRITE_FAILED"
    assert repo.final_token_trace_failures == 1
    assert repo.tasks == []


def test_final_token_usage_trace_repeated_failure_still_fails_on_first_write_boundary() -> None:
    repo = _FlakyFinalTokenTraceRepo(failures_before_success=99)

    class Agent(_Agent):
        def invoke(self, *args, **kwargs):
            callback = kwargs["config"]["callbacks"][0]
            callback.on_llm_end(
                LLMResult(
                    generations=[],
                    llm_output={"token_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
                )
            )
            repo.add_valid_evidences("run-1")
            return {}

    result = RunOrchestrator(dependencies=_deps(repo), agent_factory=lambda **kwargs: Agent()).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "SYSTEM_TABLE_WRITE_FAILED"
    assert repo.final_token_trace_failures == 1
    assert repo.tasks == []


def _deps(
    repo: "_Repo",
    *,
    llm_api_key: str | None = "key",
    memory_required: bool = False,
    memory_repo=None,
    metric_service=None,
    memory_write_on_finalize: bool = True,
) -> AgentDependencies:
    settings = SimpleNamespace(
        llm_provider="openai",
        llm_model="gpt-test",
        llm_api_key=llm_api_key,
        business_today=date(2026, 6, 6),
        target_date=date(2026, 6, 5),
        max_steps=8,
        max_query=12,
        max_drilldown_depth=2,
        max_repair=1,
        memory_enabled=memory_repo is not None,
        memory_required=memory_required,
        memory_write_on_finalize=memory_write_on_finalize,
        multi_agent_enabled=False,
    )
    return AgentDependencies(
        settings=settings,
        repository=repo,
        metric_service=metric_service or _MetricService(),
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
    def read_layers(self, *args, **kwargs):
        raise RuntimeError("MEMORY_READ_FAILED")


class _LegacyMemoryRepo:
    def read(self, *args, **kwargs):
        return []


class _FailingMemoryWriteRepo:
    def read_layers(self, *args, **kwargs):
        return []

    def write(self, *args, **kwargs):
        raise RuntimeError("MEMORY_WRITE_FAILED")


class _RecordingMemoryRepo:
    def __init__(self) -> None:
        self.reads: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []

    def read(self, mem_key: str, *, layer: str = "case") -> list[dict[str, Any]]:
        self.reads.append({"mem_key": mem_key, "layer": layer})
        return []

    def read_layers(self, mem_key: str, *, layers: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        self.reads.append({"mem_key": mem_key, "layers": layers})
        allowed_layers = set(layers or ())
        return [
            dict(row)
            for row in self.writes
            if row.get("mem_key") == mem_key and (not allowed_layers or row.get("layer") in allowed_layers)
        ]

    def write(self, record: dict[str, Any]) -> None:
        self.writes.append(dict(record))


class _FailingReflectionMemoryRepo(_RecordingMemoryRepo):
    def write(self, record: dict[str, Any]) -> None:
        if record.get("layer") == "reflection":
            raise RuntimeError("MEMORY_WRITE_FAILED")
        super().write(record)


class _StatusCheckingMemoryRepo(_RecordingMemoryRepo):
    def __init__(self, repo: "_Repo") -> None:
        super().__init__()
        self.repo = repo
        self.statuses_at_write: list[str] = []

    def write(self, record: dict[str, Any]) -> None:
        if record.get("layer") == "episodic":
            status = self.repo.runs[record["payload"]["run_id"]]["status"]
            self.statuses_at_write.append(status)
            if status != "succeeded":
                raise RuntimeError("MEMORY_WRITE_FAILED: episodic memory before terminal success")
        super().write(record)


class _MetricService:
    def __init__(
        self,
        *,
        metric_id: str = "gmv",
        dimension: str | None = None,
        element: str | None = None,
        analysis_strategy: str = "standard",
    ) -> None:
        self.metric_id = metric_id
        self.dimension = dimension
        self.element = element
        self.analysis_strategy = analysis_strategy

    def parse_question(self, question: str, *, business_today: date) -> ParsedIntent:
        family = "pay_cvr_drop" if self.metric_id == "pay_cvr" else "gmv_drop"
        filters = {self.dimension: self.element} if self.dimension is not None and self.element is not None else {}
        return ParsedIntent(
            metric_id=self.metric_id,
            target_date=date(2026, 6, 5),
            question_family=family,
            analysis_strategy=self.analysis_strategy,
            dimension=self.dimension,
            element=self.element,
            filters=filters,
        )


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

    def finish_agent_run(
        self,
        *,
        run_id: str,
        status: str,
        error_code: str | None,
        finished_at,
        total_tokens=None,
        total_latency_ms=None,
        token_breakdown=None,
    ) -> None:
        self.runs[run_id]["status"] = status
        self.runs[run_id]["error_code"] = error_code
        self.runs[run_id]["finished_at"] = finished_at
        self.runs[run_id]["total_tokens"] = total_tokens
        self.runs[run_id]["total_latency_ms"] = total_latency_ms
        self.runs[run_id]["token_breakdown"] = token_breakdown

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
            else [f"{run_id}:E1", f"{run_id}:E2", f"{run_id}:E3", f"{run_id}:E4", f"{run_id}:E_rank"],
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
        if not missing_e3:
            self.add_evidence(
                run_id,
                "E_rank",
                {
                    "metric_id": "gmv",
                    "target_date": "2026-06-05",
                    "ranker": "v1",
                    "selected_candidate": candidate,
                    "candidates": [candidate],
                    "value": 0.9,
                },
            )


class _ContextFailingRepo(_Repo):
    def update_agent_run_context(self, *, run_id: str, metric_id: str, target_date) -> None:
        raise RuntimeError("SYSTEM_TABLE_WRITE_FAILED")


class _RunContextDroppingRepo(_Repo):
    def update_agent_run_context(self, *, run_id: str, metric_id: str, target_date) -> None:
        self.runs[run_id]["target_date"] = target_date


class _FlakyFinalTokenTraceRepo(_Repo):
    def __init__(self, *, failures_before_success: int) -> None:
        super().__init__()
        self.failures_before_success = failures_before_success
        self.final_token_trace_failures = 0

    def create_trace_step(self, row: dict) -> None:
        if row.get("node") == "llm_call" and self.final_token_trace_failures < self.failures_before_success:
            self.final_token_trace_failures += 1
            raise RuntimeError("SYSTEM_TABLE_WRITE_FAILED")
        super().create_trace_step(row)

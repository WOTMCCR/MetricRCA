from __future__ import annotations

from metric_rca.intelligence.agent_runtime import AgentRuntimeConfig
from metric_rca.intelligence.agent_runtime import AgentRuntimeError
from metric_rca.intelligence.openai_agents_runtime import OpenAIAgentsRuntime, _build_run_config
from metric_rca.services.intent_planner import _LLMIntentOutput


def test_openai_agents_runtime_enables_safe_tracing_when_configured() -> None:
    run_config = _build_run_config(
        AgentRuntimeConfig(
            provider="deepseek",
            model="deepseek-chat",
            api_key="deepseek-key",
            base_url="https://api.deepseek.com",
            temperature=0.0,
            agent_tracing_enabled=True,
            agent_trace_group_id="eval-sdk-b6-deepseek",
        )
    )

    assert run_config.tracing_disabled is False
    assert run_config.trace_include_sensitive_data is False
    assert run_config.workflow_name == "MetricRCA"
    assert run_config.group_id == "eval-sdk-b6-deepseek"
    assert run_config.trace_metadata == {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "component": "intent_planner",
    }


def test_openai_agents_runtime_threads_timeout_and_retries_into_sdk_client(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "metric_rca.intelligence.openai_agents_runtime.AsyncOpenAI",
        FakeAsyncOpenAI,
    )

    _build_run_config(
        AgentRuntimeConfig(
            provider="deepseek",
            model="deepseek-chat",
            api_key="deepseek-key",
            base_url="https://api.deepseek.com",
            timeout=7,
            max_retries=0,
        )
    )

    assert captured["api_key"] == "deepseek-key"
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["timeout"] == 7
    assert captured["max_retries"] == 0


def test_openai_agents_runtime_json_mode_validates_text_output() -> None:
    class FakeResult:
        final_output = (
            '{"error_code": null, "metric_id": "gmv", "target_date": "2026-06-05", '
            '"question_family": "gmv_drop", "analysis_strategy": "channel_first", '
            '"dimension": null, "element": null, "filters": []}'
        )

    class FakeRunner:
        calls: list[dict[str, object]] = []

        @classmethod
        def run_sync(cls, agent: object, user_input: str, *, max_turns: int, run_config: object) -> FakeResult:
            cls.calls.append(
                {
                    "agent": agent,
                    "user_input": user_input,
                    "max_turns": max_turns,
                    "run_config": run_config,
                }
            )
            return FakeResult()

    runtime = OpenAIAgentsRuntime(
        AgentRuntimeConfig(
            provider="deepseek",
            model="deepseek-chat",
            api_key="deepseek-key",
            base_url="https://api.deepseek.com",
            structured_output_method="json_mode",
        ),
        runner=FakeRunner,
    )

    output = runtime.run_structured(
        name="metric_rca_intent_agent",
        instructions="Parse intent.",
        user_input="Something seems off with sales",
        output_type=_LLMIntentOutput,
        max_turns=1,
    )

    agent = FakeRunner.calls[0]["agent"]
    assert getattr(agent, "output_type") is str
    assert "OUTPUT JSON SCHEMA" in str(getattr(agent, "instructions"))
    assert output.metric_id == "gmv"
    assert output.analysis_strategy == "channel_first"


def test_openai_agents_runtime_json_mode_rejects_invalid_json() -> None:
    class FakeResult:
        final_output = "not json"

    class FakeRunner:
        @classmethod
        def run_sync(cls, agent: object, user_input: str, *, max_turns: int, run_config: object) -> FakeResult:
            return FakeResult()

    runtime = OpenAIAgentsRuntime(
        AgentRuntimeConfig(
            provider="deepseek",
            model="deepseek-chat",
            api_key="deepseek-key",
            base_url="https://api.deepseek.com",
            structured_output_method="json_mode",
        ),
        runner=FakeRunner,
    )

    try:
        runtime.run_structured(
            name="metric_rca_intent_agent",
            instructions="Parse intent.",
            user_input="Something seems off with sales",
            output_type=_LLMIntentOutput,
            max_turns=1,
        )
    except AgentRuntimeError as exc:
        assert exc.code == "MODEL_BEHAVIOR_ERROR"
    else:
        raise AssertionError("invalid json must raise AgentRuntimeError")

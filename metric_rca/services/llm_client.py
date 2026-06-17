"""Agent runtime construction compatibility helpers."""

from __future__ import annotations

from metric_rca.intelligence.agent_runtime import (
    AgentRuntime,
    AgentRuntimeConfig,
    AgentRuntimeConfigError,
    create_agent_runtime,
)


LLMClientConfigError = AgentRuntimeConfigError


def build_agent_runtime(
    *,
    provider: str | None,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    temperature: float | None = None,
    timeout: float | int | None = None,
    max_retries: int | None = None,
    max_completion_tokens: int | None = None,
    parallel_tool_calls: bool | None = None,
    structured_output_method: str = "json_schema",
    agent_tracing_enabled: bool = False,
    agent_trace_group_id: str | None = None,
) -> AgentRuntime:
    return create_agent_runtime(
        AgentRuntimeConfig(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
            max_completion_tokens=max_completion_tokens,
            parallel_tool_calls=parallel_tool_calls,
            structured_output_method=structured_output_method,  # type: ignore[arg-type]
            agent_tracing_enabled=agent_tracing_enabled,
            agent_trace_group_id=agent_trace_group_id,
        )
    )

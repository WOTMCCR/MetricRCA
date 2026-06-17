"""Provider-neutral agent runtime boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar


StructuredOutputT = TypeVar("StructuredOutputT")

OPENAI_COMPATIBLE_PROVIDERS = frozenset({"openai", "openai-compatible", "deepseek"})


class AgentRuntimeConfigError(RuntimeError):
    """Typed configuration failure before any model request is attempted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class AgentRuntimeError(RuntimeError):
    """Typed runtime failure from an agent provider adapter."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AgentRuntimeConfig:
    provider: str | None
    model: str | None
    api_key: str | None
    base_url: str | None = None
    temperature: float | None = None
    timeout: float | int | None = None
    max_retries: int | None = None
    max_completion_tokens: int | None = None
    parallel_tool_calls: bool | None = None
    structured_output_method: Literal["json_schema", "json_mode", "function_calling"] = "json_schema"
    agent_tracing_enabled: bool = False
    agent_trace_group_id: str | None = None


class AgentRuntime(Protocol):
    """Minimal structured-output contract consumed by business code."""

    def run_structured(
        self,
        *,
        name: str,
        instructions: str,
        user_input: str,
        output_type: type[StructuredOutputT],
        max_turns: int = 1,
    ) -> StructuredOutputT:
        ...


def create_agent_runtime(config: AgentRuntimeConfig) -> AgentRuntime:
    """Build the configured runtime adapter without exposing SDK types upstream."""

    if config.provider not in OPENAI_COMPATIBLE_PROVIDERS:
        raise AgentRuntimeConfigError("LLM_PROVIDER_UNSUPPORTED", "provider must be openai-compatible")
    if not config.model or not config.api_key:
        raise AgentRuntimeConfigError("LLM_REQUIRED_UNAVAILABLE", "model and API key are required")
    if config.provider != "openai" and not config.base_url:
        raise AgentRuntimeConfigError("LLM_BASE_URL_REQUIRED", "OpenAI-compatible providers require llm_base_url")

    from metric_rca.intelligence.openai_agents_runtime import OpenAIAgentsRuntime

    return OpenAIAgentsRuntime(config)

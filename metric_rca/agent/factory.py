"""Compatibility factory for the OpenAI Agents SDK runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from metric_rca.runtime.sdk_tools import RCA_TOOL_NAMES, ToolExecutor, build_default_tool_handlers


FILESYSTEM_TOOL_NAMES = frozenset({"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"})


class AgentFactoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MetricRCAAgentBundle:
    agent: Any
    expert_agents: dict[str, Any]
    tools: dict[str, Any]
    middleware: None
    guard_context: None
    token_usage_callback: None
    exposed_tool_names: frozenset[str]

    def agent_for_family(self, family: str | None) -> Any:
        _ = family
        return self.agent


@dataclass(frozen=True)
class MetricRCARuntimeAgent:
    name: str
    tool_executor: ToolExecutor
    exposed_tool_names: frozenset[str]


def create_metric_rca_agent(
    *,
    dependencies: Any,
    run_id: str,
    agent_factory: Any | None = None,
) -> MetricRCAAgentBundle:
    _ = run_id
    settings = dependencies.settings
    if not getattr(settings, "llm_provider", None) or not getattr(settings, "llm_model", None) or not getattr(settings, "llm_api_key", None):
        raise AgentFactoryError(
            "LLM_REQUIRED_UNAVAILABLE",
            "OpenAI Agents SDK runtime requires configured LLM provider, model, and API key",
        )

    handlers = build_default_tool_handlers()
    exposed = frozenset(handlers)
    _validate_registered_tool_names(exposed)
    tool_executor = ToolExecutor(dependencies=dependencies, handlers=handlers)
    if agent_factory is not None:
        agent = agent_factory(
            name="metric_rca_runtime",
            model=f"{settings.llm_provider}:{settings.llm_model}",
            tools=handlers,
            tool_executor=tool_executor,
            settings=settings,
        )
        exposed = _registered_tool_names(agent, default=exposed)
        _validate_registered_tool_names(exposed)
    else:
        agent = MetricRCARuntimeAgent(
            name="metric_rca_runtime",
            tool_executor=tool_executor,
            exposed_tool_names=exposed,
        )
    return MetricRCAAgentBundle(
        agent=agent,
        expert_agents={},
        tools=handlers,
        middleware=None,
        guard_context=None,
        token_usage_callback=None,
        exposed_tool_names=exposed,
    )


def _registered_tool_names(agent: Any, *, default: frozenset[str]) -> frozenset[str]:
    exposed = getattr(agent, "exposed_tool_names", None)
    if exposed is None:
        exposed = getattr(agent, "tool_names", None)
    if exposed is None:
        return default
    return frozenset(str(name) for name in exposed)


def _validate_registered_tool_names(exposed: frozenset[str]) -> None:
    leaked = FILESYSTEM_TOOL_NAMES & exposed
    if leaked:
        raise AgentFactoryError(
            "SDK_ACTION_SPACE_INVALID",
            f"filesystem tools leaked into runtime registry: {sorted(leaked)}",
        )
    if exposed != RCA_TOOL_NAMES:
        raise AgentFactoryError(
            "SDK_ACTION_SPACE_INVALID",
            f"runtime registry exposed {sorted(exposed)}, expected {sorted(RCA_TOOL_NAMES)}",
        )

"""Factory for the P6 deepagents MetricRCA agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from metric_rca.agent.deep_tools import (
    CalculateContributionIn,
    DetectAnomalyIn,
    DrilldownDimensionIn,
    EXPOSED_TOOL_NAMES,
    FetchRelatedSignalIn,
    RankRootCausesIn,
    build_metric_rca_tools,
)
from langchain_core.exceptions import LangChainException
from openai import OpenAIError

from metric_rca.agent.middleware import GuardMiddleware, MetricRCATokenUsageCallback, RunGuardContext
from metric_rca.agent.prompts import EXPERT_SYSTEM_PROMPT
from metric_rca.agent.subagents import build_subagents


FILESYSTEM_TOOL_NAMES = frozenset({"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"})


class AgentFactoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MetricRCAAgentBundle:
    agent: Any
    tools: list[Any]
    middleware: GuardMiddleware
    guard_context: RunGuardContext
    token_usage_callback: MetricRCATokenUsageCallback
    exposed_tool_names: frozenset[str]


def create_metric_rca_agent(
    *,
    dependencies: Any,
    run_id: str,
    agent_factory: Any | None = None,
) -> MetricRCAAgentBundle:
    settings = dependencies.settings
    model_name = getattr(settings, "llm_model", None)
    if not model_name or not getattr(settings, "llm_provider", None) or not getattr(settings, "llm_api_key", None):
        raise AgentFactoryError("LLM_REQUIRED_UNAVAILABLE", "deepagents requires configured LLM provider, model, and API key")

    if agent_factory is None:
        agent_factory = _create_filesystem_free_deep_agent

    tools = build_metric_rca_tools(dependencies=dependencies, run_id=run_id)
    tool_arg_schemas = {
        "detect_anomaly": DetectAnomalyIn,
        "drilldown_dimension": DrilldownDimensionIn,
        "fetch_related_signal": FetchRelatedSignalIn,
        "calculate_contribution": CalculateContributionIn,
        "rank_root_causes": RankRootCausesIn,
    }
    guard_context = RunGuardContext(
        run_id=run_id,
        settings=settings,
        trace_writer=dependencies.trace_writer,
        repository=dependencies.repository,
        tool_arg_schemas=tool_arg_schemas,
    )
    middleware = GuardMiddleware(guard_context)
    token_usage_callback = MetricRCATokenUsageCallback(guard_context)
    subagents = build_subagents(settings=settings, tools=tools, middleware=[middleware])
    try:
        agent = agent_factory(
            model=f"{settings.llm_provider}:{model_name}",
            tools=tools,
            system_prompt=EXPERT_SYSTEM_PROMPT,
            middleware=[middleware],
            subagents=subagents,
        )
        exposed = _compiled_tool_names(agent)
        _validate_compiled_tool_names(exposed)
    except (OpenAIError, LangChainException, RuntimeError, ValueError, TypeError) as exc:
        if isinstance(exc, AgentFactoryError):
            raise
        raise AgentFactoryError("LLM_REQUIRED_UNAVAILABLE", "deepagents agent construction failed") from exc
    return MetricRCAAgentBundle(
        agent=agent,
        tools=tools,
        middleware=middleware,
        guard_context=guard_context,
        token_usage_callback=token_usage_callback,
        exposed_tool_names=exposed,
    )


def _create_filesystem_free_deep_agent(
    *,
    model: str,
    tools: list[Any],
    system_prompt: str,
    middleware: list[Any],
    subagents: list[dict[str, Any]],
) -> Any:
    if subagents:
        raise AgentFactoryError("MULTI_AGENT_P9_SCOPE", "subagents are disabled until P9")
    try:
        from deepagents.graph import (
            BASE_AGENT_PROMPT,
            AnthropicPromptCachingMiddleware,
            PatchToolCallsMiddleware,
            SummarizationMiddleware,
            TodoListMiddleware,
        )
        from langchain.agents import create_agent
    except ModuleNotFoundError as exc:
        raise AgentFactoryError("LLM_REQUIRED_UNAVAILABLE", "deepagents dependency is unavailable") from exc

    deepagent_middleware = [
        TodoListMiddleware(),
        SummarizationMiddleware(
            model=model,
            trigger=("tokens", 170000),
            keep=("messages", 6),
            trim_tokens_to_summarize=None,
        ),
        AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
        PatchToolCallsMiddleware(),
        *middleware,
    ]
    return create_agent(
        model,
        tools=tools,
        system_prompt=f"{system_prompt}\n\n{BASE_AGENT_PROMPT}",
        middleware=deepagent_middleware,
    ).with_config({"recursion_limit": 1000})


def _compiled_tool_names(agent: Any) -> frozenset[str]:
    try:
        tools_by_name = agent.nodes["tools"].bound._tools_by_name
    except (AttributeError, KeyError, TypeError) as exc:
        raise AgentFactoryError(
            "DEEPAGENTS_TOOL_INTROSPECTION_FAILED",
            "compiled deepagents graph does not expose an introspectable ToolNode",
        ) from exc
    return frozenset(str(name) for name in tools_by_name)


def _validate_compiled_tool_names(exposed: frozenset[str]) -> None:
    leaked = FILESYSTEM_TOOL_NAMES & exposed
    if leaked:
        raise AgentFactoryError(
            "DEEPAGENTS_FILESYSTEM_TOOLS_UNDISABLEABLE",
            f"filesystem tools leaked into compiled graph: {sorted(leaked)}",
        )
    if exposed != EXPOSED_TOOL_NAMES:
        raise AgentFactoryError(
            "DEEPAGENTS_ACTION_SPACE_INVALID",
            f"compiled graph exposed {sorted(exposed)}, expected {sorted(EXPOSED_TOOL_NAMES)}",
        )

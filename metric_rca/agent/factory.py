"""Factory for the P6 deepagents MetricRCA agent."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import Parameter, signature
from typing import Any

from metric_rca.agent.deep_tools import (
    CalculateContributionIn,
    DetectAnomalyIn,
    DrilldownDimensionIn,
    FetchRelatedSignalIn,
    RankRootCausesIn,
    build_metric_rca_tools,
)
from langchain_core.exceptions import LangChainException
from openai import OpenAIError

from metric_rca.agent.middleware import GuardMiddleware, MetricRCATokenUsageCallback, RunGuardContext
from metric_rca.agent.prompts import EXPERT_SYSTEM_PROMPT
from metric_rca.agent.subagents import build_subagents


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
        try:
            from deepagents import create_deep_agent as agent_factory
        except ModuleNotFoundError as exc:
            raise AgentFactoryError("LLM_REQUIRED_UNAVAILABLE", "deepagents dependency is unavailable") from exc

    if not _supports_kwarg(agent_factory, "builtin_tools"):
        raise AgentFactoryError(
            "DEEPAGENTS_FILESYSTEM_TOOLS_UNDISABLEABLE",
            "pinned deepagents API does not support disabling built-in filesystem tools",
        )

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
            permissions=[],
            builtin_tools=[],
        )
    except (OpenAIError, LangChainException, RuntimeError, ValueError, TypeError) as exc:
        raise AgentFactoryError("LLM_REQUIRED_UNAVAILABLE", "deepagents agent construction failed") from exc
    exposed = frozenset(tool.name for tool in tools) | {"write_todos"}
    return MetricRCAAgentBundle(
        agent=agent,
        tools=tools,
        middleware=middleware,
        guard_context=guard_context,
        token_usage_callback=token_usage_callback,
        exposed_tool_names=exposed,
    )


def _supports_kwarg(callable_obj: Any, name: str) -> bool:
    try:
        params = signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(param.kind == Parameter.VAR_KEYWORD or param.name == name for param in params)

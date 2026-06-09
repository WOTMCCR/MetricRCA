"""Real LangGraph StateGraph orchestration for Matrix P3A."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from metric_rca.agent.nodes.attribute_rank import attribute_rank
from metric_rca.agent.nodes.create_tasks import create_tasks
from metric_rca.agent.nodes.error_return import error_return
from metric_rca.agent.nodes.execute_tool import execute_tool
from metric_rca.agent.nodes.generate_report import generate_report
from metric_rca.agent.nodes.parse_question import parse_question
from metric_rca.agent.nodes.plan_init import plan_init
from metric_rca.agent.nodes.react_step import react_step
from metric_rca.agent.nodes.read_memory import read_memory
from metric_rca.agent.nodes.reflection_verify import reflection_verify
from metric_rca.agent.nodes.write_memory import write_memory
from metric_rca.agent.state import RCAState
from metric_rca.config.settings import Settings, get_settings
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.observability.trace import TraceWriteError, TraceWriter
from metric_rca.repositories.metadata_repository import MetadataRepository
from metric_rca.repositories.metric_repository import MetricRepository
from metric_rca.services.metric_service import MetricService


@dataclass
class GraphDependencies:
    settings: Any
    repository: Any
    metric_service: Any
    renderer: Any
    trace_writer: Any
    memory_repo: Any = None


def build_dependencies(
    *,
    settings: Settings | None = None,
    repository: Any | None = None,
    metric_service: Any | None = None,
    renderer: Any | None = None,
    trace_writer: Any | None = None,
    memory_repo: Any | None = None,
) -> GraphDependencies:
    resolved_settings = settings or get_settings()
    resolved_repository = repository or MetricRepository.from_settings(resolved_settings)
    resolved_metric_service = metric_service or MetricService(
        MetadataRepository.from_settings(resolved_settings),
        settings=resolved_settings,
    )
    resolved_renderer = renderer or SQLRenderer()
    resolved_trace_writer = trace_writer or TraceWriter(resolved_repository)
    return GraphDependencies(
        settings=resolved_settings,
        repository=resolved_repository,
        metric_service=resolved_metric_service,
        renderer=resolved_renderer,
        trace_writer=resolved_trace_writer,
        memory_repo=memory_repo,
    )


def build_state_graph(*, dependencies: Any) -> StateGraph:
    builder = StateGraph(RCAState)
    builder.add_node("parse_question", lambda state: parse_question(state, dependencies=dependencies))
    builder.add_node("read_memory", lambda state: read_memory(state, dependencies=dependencies))
    builder.add_node("plan_init", lambda state: plan_init(state, dependencies=dependencies))
    builder.add_node("react_step", lambda state: react_step(state, dependencies=dependencies))
    builder.add_node("execute_tool", lambda state: execute_tool(state, dependencies=dependencies))
    builder.add_node("attribute_rank", lambda state: attribute_rank(state, dependencies=dependencies))
    builder.add_node("reflection_verify", lambda state: reflection_verify(state, dependencies=dependencies))
    builder.add_node("generate_report", lambda state: generate_report(state, dependencies=dependencies))
    builder.add_node("create_tasks", lambda state: create_tasks(state, dependencies=dependencies))
    builder.add_node("write_memory", lambda state: write_memory(state, dependencies=dependencies))
    builder.add_node("error_return", lambda state: error_return(state, dependencies=dependencies))

    builder.add_edge(START, "parse_question")
    builder.add_conditional_edges(
        "parse_question",
        lambda state: route_after_parse(state, dependencies=dependencies),
        {"read_memory": "read_memory", "error_return": "error_return"},
    )
    builder.add_conditional_edges(
        "read_memory",
        lambda state: route_after_read_memory(state, dependencies=dependencies),
        {"plan_init": "plan_init", "error_return": "error_return"},
    )
    builder.add_edge("plan_init", "react_step")
    builder.add_conditional_edges(
        "react_step",
        lambda state: route_after_react(state, dependencies=dependencies),
        {
            "execute_tool": "execute_tool",
            "attribute_rank": "attribute_rank",
            "generate_report": "generate_report",
            "error_return": "error_return",
        },
    )
    builder.add_conditional_edges(
        "execute_tool",
        lambda state: route_after_execute_tool(state, dependencies=dependencies),
        {"react_step": "react_step", "error_return": "error_return"},
    )
    builder.add_edge("attribute_rank", "reflection_verify")
    builder.add_conditional_edges(
        "reflection_verify",
        lambda state: route_after_reflection(state, dependencies=dependencies),
        {
            "generate_report": "generate_report",
            "react_step": "react_step",
            "error_return": "error_return",
        },
    )
    builder.add_conditional_edges(
        "generate_report",
        lambda state: route_after_generate_report(state, dependencies=dependencies),
        {"create_tasks": "create_tasks", "write_memory": "write_memory"},
    )
    builder.add_conditional_edges(
        "create_tasks",
        lambda state: route_after_create_tasks(state, dependencies=dependencies),
        {"write_memory": "write_memory", "error_return": "error_return"},
    )
    builder.add_edge("error_return", "write_memory")
    builder.add_edge("write_memory", END)
    return builder


def compile_graph(**kwargs):
    dependencies = kwargs.get("dependencies") or build_dependencies(
        settings=kwargs.get("settings"),
        repository=kwargs.get("repository"),
        metric_service=kwargs.get("metric_service"),
        renderer=kwargs.get("renderer"),
        trace_writer=kwargs.get("trace_writer"),
        memory_repo=kwargs.get("memory_repo"),
    )
    return build_state_graph(dependencies=dependencies).compile()


def run_rca(
    question: str,
    *,
    run_id: str | None = None,
    settings: Settings | None = None,
    repository: Any | None = None,
    metric_service: Any | None = None,
    renderer: Any | None = None,
    trace_writer: Any | None = None,
    memory_repo: Any | None = None,
    dependencies: Any | None = None,
) -> dict[str, Any]:
    resolved_run_id = run_id or f"run-{uuid4().hex}"
    deps = dependencies or build_dependencies(
        settings=settings,
        repository=repository,
        metric_service=metric_service,
        renderer=renderer,
        trace_writer=trace_writer,
        memory_repo=memory_repo,
    )
    compiled = compile_graph(dependencies=deps)
    if getattr(deps, "trace_writer", None) is not None:
        try:
            deps.trace_writer.start_run(
                run_id=resolved_run_id,
                question=question,
                target_date=deps.settings.target_date,
            )
        except TraceWriteError as exc:
            return {
                "run_id": resolved_run_id,
                "question": question,
                "status": "failed",
                "error_code": exc.code,
            }
    initial_state = {
        "run_id": resolved_run_id,
        "question": question,
        "target_date": deps.settings.target_date,
        "metric_id": None,
        "parsed_spec": None,
        "memory_hits": [],
        "actions": [],
        "observations": [],
        "evidences": [],
        "candidates": [],
        "step_count": 0,
        "query_count": 0,
        "drilldown_depth": 0,
        "repair_count": 0,
        "status": "running",
        "error_code": None,
    }
    return compiled.invoke(initial_state)


def route_after_parse(state: dict[str, Any], *, dependencies: Any) -> str:
    return "error_return" if state.get("error_code") else "read_memory"


def route_after_read_memory(state: dict[str, Any], *, dependencies: Any) -> str:
    return "error_return" if state.get("error_code") else "plan_init"


def route_after_react(state: dict[str, Any], *, dependencies: Any) -> str:
    if state.get("error_code"):
        return "error_return"
    action = state.get("actions", [])[-1]
    action_name = getattr(action, "action", None) or action.get("action")
    action_args = getattr(action, "args", None) or action.get("args", {})
    if action_name == "finish":
        if action_args.get("status") == "no_anomaly" or state.get("status") == "no_anomaly":
            return "generate_report"
        if action_args.get("status") == "failed":
            return "error_return"
        return "attribute_rank"
    return "execute_tool"


def route_after_execute_tool(state: dict[str, Any], *, dependencies: Any) -> str:
    if state.get("error_code"):
        return "error_return"
    return "react_step"


def route_after_reflection(state: dict[str, Any], *, dependencies: Any) -> str:
    reflection = state.get("reflection")
    if reflection is not None and getattr(reflection, "passed", False):
        return "generate_report"
    repair_count = int(state.get("repair_count") or 0)
    max_repair = int(getattr(dependencies.settings, "max_repair", 1))
    issues = getattr(reflection, "issues", []) if reflection is not None else []
    if repair_count < max_repair and any(getattr(issue, "suggested_action", None) is not None for issue in issues):
        return "react_step"
    return "error_return"


def route_after_generate_report(state: dict[str, Any], *, dependencies: Any) -> str:
    if state.get("status") == "no_anomaly":
        return "write_memory"
    return "create_tasks" if state.get("candidates") else "write_memory"


def route_after_create_tasks(state: dict[str, Any], *, dependencies: Any) -> str:
    return "error_return" if state.get("error_code") else "write_memory"

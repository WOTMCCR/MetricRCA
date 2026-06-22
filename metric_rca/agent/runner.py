"""Compatibility runner entrypoint backed by runtime.RunService."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from metric_rca.config.settings import Settings, get_settings
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.memory.memory_repo import MemoryRepository
from metric_rca.observability.trace import TraceWriter
from metric_rca.repositories.metadata_repository import MetadataRepository
from metric_rca.repositories.metric_repository import MetricRepository
from metric_rca.services.metric_service import MetricService


@dataclass
class AgentDependencies:
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
) -> AgentDependencies:
    resolved_settings = settings or get_settings()
    resolved_repository = repository or MetricRepository.from_settings(resolved_settings)
    resolved_metric_service = metric_service or MetricService(
        MetadataRepository.from_settings(resolved_settings),
        settings=resolved_settings,
    )
    resolved_renderer = renderer or SQLRenderer()
    resolved_trace_writer = trace_writer or TraceWriter(resolved_repository)
    resolved_memory_repo = memory_repo
    if resolved_memory_repo is None and getattr(resolved_settings, "memory_enabled", False):
        resolved_memory_repo = MemoryRepository.from_settings(
            resolved_settings,
            system_repository=resolved_repository,
        )
    return AgentDependencies(
        settings=resolved_settings,
        repository=resolved_repository,
        metric_service=resolved_metric_service,
        renderer=resolved_renderer,
        trace_writer=resolved_trace_writer,
        memory_repo=resolved_memory_repo,
    )


class RunOrchestrator:
    def __init__(self, *, dependencies: AgentDependencies, agent_factory: Any | None = None) -> None:
        self.dependencies = dependencies
        self.agent_factory = agent_factory

    def run(self, question: str, *, run_id: str | None = None) -> dict[str, Any]:
        from metric_rca.runtime.run_service import RunService

        _ = self.agent_factory
        return RunService(dependencies=self.dependencies).run(question, run_id=run_id)


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
    dependencies: AgentDependencies | None = None,
    agent_factory: Any | None = None,
) -> dict[str, Any]:
    owns_memory_repo = (
        dependencies is None
        and memory_repo is None
        and getattr(settings or get_settings(), "memory_enabled", False)
    )
    deps = dependencies or build_dependencies(
        settings=settings,
        repository=repository,
        metric_service=metric_service,
        renderer=renderer,
        trace_writer=trace_writer,
        memory_repo=memory_repo,
    )
    try:
        return RunOrchestrator(dependencies=deps, agent_factory=agent_factory).run(question, run_id=run_id)
    finally:
        if owns_memory_repo:
            close = getattr(deps.memory_repo, "close", None)
            if callable(close):
                close()

"""Typed dependency contracts for the deterministic RCA runtime."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from metric_rca.config.settings import Settings
from metric_rca.domain.models import MetricDefinition
from metric_rca.services.metric_contracts import ParsedIntent


class RuntimeRepository(Protocol):
    def get_trace_steps(self, run_id: str) -> list[dict[str, Any]]:
        ...

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, Any] | None:
        ...

    def get_evidences(self, run_id: str) -> list[dict[str, Any]]:
        ...

    def get_operation_tasks(self, run_id: str) -> list[dict[str, Any]]:
        ...

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        ...

    def create_operation_task(self, task: dict[str, Any]) -> None:
        ...

    def create_evidence(self, row: dict[str, Any]) -> None:
        ...

    def update_evidence_result_summary(self, *, run_id: str, evidence_id: str, result_summary: dict[str, Any]) -> None:
        ...

    def execute_plan(self, plan: Any, *, run_id: str) -> Any:
        ...


class RuntimeMetricService(Protocol):
    def parse_question(self, question: str, *, business_today: date) -> ParsedIntent:
        ...

    def get_metric_definition(self, metric_id: str) -> MetricDefinition:
        ...


class RuntimeTraceWriter(Protocol):
    def start_run(self, *, run_id: str, question: str, target_date: date) -> None:
        ...

    def set_run_context(self, *, run_id: str, metric_id: str, target_date: date) -> None:
        ...

    def write_step(
        self,
        *,
        run_id: str,
        node: str,
        action: str,
        input_summary: dict[str, Any],
        output_summary: dict[str, Any],
        error_code: str | None,
    ) -> None:
        ...

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        error_code: str | None,
        total_tokens: int | None = None,
        total_latency_ms: int | None = None,
        token_breakdown: dict[str, Any] | None = None,
    ) -> None:
        ...


class RuntimeDependencies(Protocol):
    repository: RuntimeRepository
    metric_service: RuntimeMetricService
    trace_writer: RuntimeTraceWriter
    settings: Settings
    renderer: Any
    memory_repo: Any

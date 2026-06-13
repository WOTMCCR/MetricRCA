"""RunOrchestrator for the P6 deepagents architecture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from langchain_core.exceptions import LangChainException
from openai import OpenAIError

from metric_rca.agent.factory import AgentFactoryError, create_metric_rca_agent
from metric_rca.agent.reflection import verify_reflection
from metric_rca.config.settings import Settings, get_settings
from metric_rca.domain.models import Evidence, QuerySpec, RootCauseCandidate
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.memory.memory_repo import MemoryRepository
from metric_rca.observability.trace import TraceWriteError, TraceWriter
from metric_rca.reporting.projector import build_report_from_persisted_artifacts
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
        resolved_memory_repo = MemoryRepository.from_settings(resolved_settings)
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
        resolved_run_id = run_id or f"run-{uuid4().hex}"
        try:
            self.dependencies.trace_writer.start_run(
                run_id=resolved_run_id,
                question=question,
                target_date=self.dependencies.settings.target_date,
            )
        except TraceWriteError as exc:
            return {"run_id": resolved_run_id, "question": question, "status": "failed", "error_code": exc.code}

        try:
            self._read_required_memory(resolved_run_id)
            bundle = create_metric_rca_agent(
                dependencies=self.dependencies,
                run_id=resolved_run_id,
                agent_factory=self.agent_factory,
            )
        except (AgentFactoryError, RuntimeError, ValueError, TypeError, OpenAIError, LangChainException) as exc:
            code = getattr(exc, "code", None) or _code_from_message(str(exc), "LLM_REQUIRED_UNAVAILABLE")
            return self._fail(resolved_run_id, question, code)

        try:
            bundle.agent.invoke(
                {"messages": [{"role": "user", "content": question}]},
                config={
                    "configurable": {"thread_id": resolved_run_id},
                    "callbacks": [bundle.token_usage_callback],
                },
            )
        except (RuntimeError, ValueError, TypeError, OpenAIError, LangChainException) as exc:
            code = _code_from_message(str(exc), "LLM_REQUIRED_UNAVAILABLE")
            return self._fail(resolved_run_id, question, code)
        try:
            self._flush_pending_token_usage(bundle.guard_context)
        except TraceWriteError as exc:
            return self._fail(resolved_run_id, question, exc.code)

        if bundle.guard_context.failed:
            return self._fail(resolved_run_id, question, bundle.guard_context.error_code or "AGENT_TOOL_FAILED")

        no_anomaly_error = self._no_anomaly_contract_error(resolved_run_id)
        if no_anomaly_error is not None:
            return self._fail(resolved_run_id, question, no_anomaly_error)

        reflection = self._verify(resolved_run_id, repair_count=0)
        if not reflection.passed:
            if _has_repair_action(reflection) and int(getattr(self.dependencies.settings, "max_repair", 1)) > 0:
                try:
                    bundle.agent.invoke(
                        {
                            "messages": [
                                {
                                    "role": "user",
                                    "content": f"Repair Reflection issue using persisted evidence only: {reflection.model_dump(mode='json')}",
                                }
                            ]
                        },
                        config={
                            "configurable": {"thread_id": resolved_run_id},
                            "callbacks": [bundle.token_usage_callback],
                        },
                    )
                except (RuntimeError, ValueError, TypeError, OpenAIError, LangChainException) as exc:
                    return self._fail(resolved_run_id, question, _code_from_message(str(exc), "REFLECTION_REPAIR_FAILED"))
                try:
                    self._flush_pending_token_usage(bundle.guard_context)
                except TraceWriteError as exc:
                    return self._fail(resolved_run_id, question, exc.code)
                if bundle.guard_context.failed:
                    return self._fail(resolved_run_id, question, bundle.guard_context.error_code or "AGENT_TOOL_FAILED")
                reflection = self._verify(resolved_run_id, repair_count=1)
            if not reflection.passed:
                return self._fail(resolved_run_id, question, "REFLECTION_REPAIR_FAILED")

        status = "no_anomaly" if self._is_no_anomaly(resolved_run_id) else "succeeded"
        report = self._project_report(resolved_run_id, status=status)
        if status == "succeeded" and report is None:
            return self._fail(resolved_run_id, question, "REPORT_PROJECTION_FAILED")
        try:
            self._create_required_tasks(resolved_run_id, report)
            self._write_required_memory(resolved_run_id, report)
            self.dependencies.trace_writer.finish_run(run_id=resolved_run_id, status=status, error_code=None)
        except (TraceWriteError, RuntimeError) as exc:
            code = getattr(exc, "code", None) or _code_from_message(str(exc), "MEMORY_WRITE_FAILED")
            return self._fail(resolved_run_id, question, code)
        return {
            "run_id": resolved_run_id,
            "question": question,
            "status": status,
            "error_code": None,
            "reflection": reflection.model_dump(mode="json"),
            "report": report,
        }

    def _verify(self, run_id: str, *, repair_count: int) -> Any:
        state = self._reflection_state(run_id, repair_count=repair_count)
        persisted = {row["evidence_id"]: row for row in self.dependencies.repository.get_evidences(run_id)}
        return verify_reflection(
            state,
            max_repair=int(getattr(self.dependencies.settings, "max_repair", 1)),
            persisted_evidence_by_id=persisted,
        )

    def _reflection_state(self, run_id: str, *, repair_count: int) -> dict[str, Any]:
        evidences = [self._evidence_from_row(row) for row in self.dependencies.repository.get_evidences(run_id)]
        e4 = next((ev for ev in evidences if ev.evidence_id == f"{run_id}:E4"), None)
        candidates = []
        if e4 is not None:
            summary = e4.result_summary or {}
            raw_candidates = summary.get("candidates") or []
            candidates = [RootCauseCandidate.model_validate(item) for item in raw_candidates]
            if not candidates and isinstance(summary.get("selected_candidate"), dict):
                candidates = [RootCauseCandidate.model_validate(summary["selected_candidate"])]
        run = self.dependencies.repository.get_agent_run(run_id) or {}
        trace_nodes = [row.get("action") or row.get("node") for row in self.dependencies.repository.get_trace_steps(run_id)]
        return {
            "run_id": run_id,
            "metric_id": run.get("metric_id"),
            "target_date": run.get("target_date") or getattr(self.dependencies.settings, "target_date"),
            "status": "no_anomaly" if self._is_no_anomaly(run_id) else "running",
            "evidences": evidences,
            "candidates": candidates,
            "repair_count": repair_count,
            "trace_nodes": trace_nodes,
            "operation_tasks": self.dependencies.repository.get_operation_tasks(run_id),
        }

    def _evidence_from_row(self, row: dict[str, Any]) -> Evidence:
        return Evidence(
            evidence_id=row["evidence_id"],
            query_spec=QuerySpec.model_validate(row["query_spec"]),
            sql=row["sql_text"],
            sql_hash=row["sql_hash"],
            guard_status=row["guard_status"],
            result_summary=row["result_summary"],
            data_source=row["data_source"],
            created_at=row.get("created_at") or datetime.now(timezone.utc).replace(tzinfo=None),
        )

    def _is_no_anomaly(self, run_id: str) -> bool:
        e1 = self.dependencies.repository.get_evidence(run_id=run_id, evidence_id=f"{run_id}:E1")
        if e1 is None:
            return False
        summary = e1.get("result_summary") or {}
        return summary.get("is_anomaly") is False or summary.get("error_code") == "NO_ANOMALY_DETECTED"

    def _no_anomaly_contract_error(self, run_id: str) -> str | None:
        if not self._is_no_anomaly(run_id):
            return None
        trace_actions = {
            str(row.get("action") or row.get("node"))
            for row in self.dependencies.repository.get_trace_steps(run_id)
        }
        if trace_actions & {"drilldown_dimension", "fetch_related_signal", "rank_root_causes", "calculate_contribution"}:
            return "NO_ANOMALY_CONTRACT_VIOLATED"
        if self.dependencies.repository.get_operation_tasks(run_id):
            return "NO_ANOMALY_CONTRACT_VIOLATED"
        return None

    def _project_report(self, run_id: str, *, status: str) -> dict[str, Any] | None:
        run = dict(self.dependencies.repository.get_agent_run(run_id) or {})
        run["status"] = status
        return build_report_from_persisted_artifacts(
            agent_run=run,
            evidences=self.dependencies.repository.get_evidences(run_id),
            tasks=self.dependencies.repository.get_operation_tasks(run_id),
        )

    def _read_required_memory(self, run_id: str) -> None:
        if not getattr(self.dependencies.settings, "memory_enabled", False):
            return
        repo = getattr(self.dependencies, "memory_repo", None)
        if repo is None:
            if getattr(self.dependencies.settings, "memory_required", False):
                raise RuntimeError("MEMORY_READ_FAILED: memory repository unavailable")
            return
        try:
            repo.read(f"{run_id}|start", layer="case")
        except RuntimeError as exc:
            if getattr(self.dependencies.settings, "memory_required", False):
                raise RuntimeError("MEMORY_READ_FAILED: required memory read failed") from exc

    def _flush_pending_token_usage(self, guard_context: Any) -> None:
        for usage in guard_context.drain_pending_token_usage():
            self.dependencies.trace_writer.write_step(
                run_id=guard_context.run_id,
                node="llm_call",
                action=None,
                input_summary={},
                output_summary={"token_usage": usage},
                error_code=None,
                token_usage=usage,
            )

    def _create_required_tasks(self, run_id: str, report: dict[str, Any] | None) -> None:
        if report is None or report.get("status") != "succeeded":
            return
        candidate = report.get("top_candidate")
        if not isinstance(candidate, dict):
            return
        root_cause_type = str(candidate.get("root_cause_type") or "")
        if not root_cause_type:
            return
        task = {
            "task_id": f"{run_id}:task:root-cause",
            "run_id": run_id,
            "title": f"Investigate {root_cause_type}",
            "root_cause_type": root_cause_type,
            "payload": {
                "metric_id": report.get("metric_id"),
                "target_date": report.get("target_date"),
                "candidate": candidate,
                "evidence_ids": report.get("evidence_ids") or [],
            },
            "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        try:
            self.dependencies.repository.create_operation_task(task)
        except RuntimeError as exc:
            raise RuntimeError("TASK_WRITE_FAILED: required operation task write failed") from exc

    def _write_required_memory(self, run_id: str, report: dict[str, Any] | None) -> None:
        if not getattr(self.dependencies.settings, "memory_enabled", False):
            return
        repo = getattr(self.dependencies, "memory_repo", None)
        if repo is None:
            if getattr(self.dependencies.settings, "memory_required", False):
                raise RuntimeError("MEMORY_WRITE_FAILED: memory repository unavailable")
            return
        if report is None:
            return
        try:
            repo.write(
                {
                    "layer": "episodic",
                    "key": f"{report.get('metric_id')}|run",
                    "payload": {"run_id": run_id, "status": report.get("status")},
                    "confidence": 0.8,
                    "source": "reflection_verified",
                }
            )
        except RuntimeError as exc:
            if getattr(self.dependencies.settings, "memory_required", False):
                raise RuntimeError("MEMORY_WRITE_FAILED: required memory write failed") from exc

    def _fail(self, run_id: str, question: str, code: str) -> dict[str, Any]:
        try:
            self.dependencies.trace_writer.finish_run(run_id=run_id, status="failed", error_code=code)
        except TraceWriteError as exc:
            return {
                "run_id": run_id,
                "question": question,
                "status": "failed",
                "error_code": code,
                "finalization_error_code": exc.code,
            }
        return {"run_id": run_id, "question": question, "status": "failed", "error_code": code}


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
    deps = dependencies or build_dependencies(
        settings=settings,
        repository=repository,
        metric_service=metric_service,
        renderer=renderer,
        trace_writer=trace_writer,
        memory_repo=memory_repo,
    )
    return RunOrchestrator(dependencies=deps, agent_factory=agent_factory).run(question, run_id=run_id)


def _has_repair_action(reflection: Any) -> bool:
    return any(getattr(issue, "suggested_action", None) is not None for issue in getattr(reflection, "issues", []))


def _code_from_message(message: str, default: str) -> str:
    code = message.split(":", maxsplit=1)[0]
    return code if code and code.isupper() else default

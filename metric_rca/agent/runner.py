"""RunOrchestrator for the P6 deepagents architecture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any
from uuid import uuid4

from langchain_core.exceptions import LangChainException
from openai import OpenAIError

from metric_rca.agent.discovery_policy import DiscoveryPolicy, discovery_policy_from_intent
from metric_rca.agent.factory import AgentFactoryError, create_metric_rca_agent
from metric_rca.agent.reflection import verify_reflection
from metric_rca.agent.subagents import RunOutcome, route_metric_family
from metric_rca.config.settings import Settings, get_settings
from metric_rca.domain.models import Evidence, PHASE1_METRICS, QuerySpec, RootCauseCandidate
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.memory.memory_repo import MemoryRepository
from metric_rca.observability.summary import build_token_summary
from metric_rca.observability.trace import TraceWriteError, TraceWriter
from metric_rca.reporting.projector import build_report_from_persisted_artifacts
from metric_rca.repositories.metadata_repository import MetadataRepository
from metric_rca.repositories.metric_repository import MetricRepository
from metric_rca.services.metric_contracts import ParsedIntent
from metric_rca.services.metric_service import MetricService


LOGGER = logging.getLogger(__name__)


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
        resolved_run_id = run_id or f"run-{uuid4().hex}"
        try:
            self.dependencies.trace_writer.start_run(
                run_id=resolved_run_id,
                question=question,
                target_date=self.dependencies.settings.target_date,
            )
        except TraceWriteError as exc:
            return {"run_id": resolved_run_id, "question": question, "status": "failed", "error_code": exc.code}

        parsed_intent: ParsedIntent | None = None
        try:
            parsed_intent = self.dependencies.metric_service.parse_question(
                question,
                business_today=self.dependencies.settings.business_today,
            )
            self.dependencies.trace_writer.set_run_context(
                run_id=resolved_run_id,
                metric_id=parsed_intent.metric_id,
                target_date=parsed_intent.target_date,
            )
            bundle = create_metric_rca_agent(
                dependencies=self.dependencies,
                run_id=resolved_run_id,
                agent_factory=self.agent_factory,
            )
            bundle.guard_context.explicit_filters = _parsed_intent_scope(parsed_intent)
            bundle.guard_context.target_metric_id = parsed_intent.metric_id
            bundle.guard_context.target_date = parsed_intent.target_date
            bundle.guard_context.discovery_policy = discovery_policy_from_intent(parsed_intent)
            expert_family: str | None = None
            selected_agent = bundle.agent
            if getattr(self.dependencies.settings, "multi_agent_enabled", False):
                expert_family = route_metric_family(parsed_intent.metric_id)
                self._write_triage_route(resolved_run_id, parsed_intent=parsed_intent, family=expert_family)
                selected_agent = bundle.agent_for_family(expert_family)
            memory_hits = self._read_required_memory(resolved_run_id, parsed_intent=parsed_intent)
        except (AgentFactoryError, TraceWriteError, RuntimeError, ValueError, TypeError, OpenAIError, LangChainException) as exc:
            code = _code_from_exception(exc, "LLM_REQUIRED_UNAVAILABLE")
            extra_payload = _failure_payload(parsed_intent)
            return self._fail(resolved_run_id, question, code, extra_payload=extra_payload)

        try:
            agent_result = selected_agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": _agent_user_message(
                                question,
                                self.dependencies.settings,
                                parsed_intent=parsed_intent,
                                memory_hits=memory_hits,
                            ),
                        }
                    ]
                },
                config={
                    "configurable": {"thread_id": resolved_run_id},
                    "callbacks": [bundle.token_usage_callback],
                },
            )
            if expert_family is not None:
                _warn_on_run_outcome(agent_result, parsed_intent=parsed_intent)
        except (RuntimeError, ValueError, TypeError, OpenAIError, LangChainException) as exc:
            code = _code_from_exception(exc, "AGENT_INVOKE_FAILED")
            if not self._can_continue_after_terminal_agent_error(resolved_run_id, code):
                return self._fail(resolved_run_id, question, code, extra_payload=_failure_payload(parsed_intent))
        try:
            self._flush_pending_token_usage(bundle.guard_context)
        except TraceWriteError as exc:
            return self._fail(resolved_run_id, question, exc.code, extra_payload=_failure_payload(parsed_intent))

        if bundle.guard_context.failed:
            return self._fail(
                resolved_run_id,
                question,
                bundle.guard_context.error_code or "AGENT_TOOL_FAILED",
                extra_payload=_failure_payload(parsed_intent),
            )

        no_anomaly_error = self._no_anomaly_contract_error(resolved_run_id)
        if no_anomaly_error is not None:
            return self._fail(resolved_run_id, question, no_anomaly_error, extra_payload=_failure_payload(parsed_intent))

        reflection = self._verify(resolved_run_id, repair_count=0, parsed_intent=parsed_intent)
        initial_reflection = reflection
        if not reflection.passed:
            if _has_repair_action(reflection) and int(getattr(self.dependencies.settings, "max_repair", 1)) > 0:
                repair_action = _first_repair_action(reflection)
                try:
                    bundle.guard_context.required_repair_action = repair_action
                    repair_result = selected_agent.invoke(
                        {
                            "messages": [
                                {
                                    "role": "user",
                                    "content": _repair_instruction(reflection, repair_action=repair_action),
                                }
                            ]
                        },
                        config={
                            "configurable": {"thread_id": resolved_run_id},
                            "callbacks": [bundle.token_usage_callback],
                        },
                    )
                    if expert_family is not None:
                        _warn_on_run_outcome(repair_result, parsed_intent=parsed_intent)
                except (RuntimeError, ValueError, TypeError, OpenAIError, LangChainException) as exc:
                    code = _code_from_exception(exc, "REFLECTION_REPAIR_FAILED")
                    return self._fail(
                        resolved_run_id,
                        question,
                        code,
                        extra_payload=_failure_payload(
                            parsed_intent,
                            {"reflection_issues": _reflection_issues_payload(initial_reflection)},
                        ),
                    )
                finally:
                    bundle.guard_context.required_repair_action = None
                try:
                    self._flush_pending_token_usage(bundle.guard_context)
                except TraceWriteError as exc:
                    return self._fail(resolved_run_id, question, exc.code, extra_payload=_failure_payload(parsed_intent))
                if bundle.guard_context.failed:
                    return self._fail(
                        resolved_run_id,
                        question,
                        bundle.guard_context.error_code or "AGENT_TOOL_FAILED",
                        extra_payload=_failure_payload(parsed_intent),
                    )
                reflection = self._verify(resolved_run_id, repair_count=1, parsed_intent=parsed_intent)
            if not reflection.passed:
                return self._fail(
                    resolved_run_id,
                    question,
                    "REFLECTION_REPAIR_FAILED",
                    extra_payload=_failure_payload(
                        parsed_intent,
                        {"reflection_issues": _reflection_issues_payload(reflection)},
                    ),
                )

        status = "no_anomaly" if self._is_no_anomaly(resolved_run_id) else "succeeded"
        report = self._project_report(resolved_run_id, status=status)
        if report is None:
            return self._fail(
                resolved_run_id,
                question,
                "REPORT_PROJECTION_FAILED",
                extra_payload=_failure_payload(parsed_intent),
            )
        try:
            self._create_required_tasks(resolved_run_id, report)
            self._finish_run_with_observability(resolved_run_id, status=status, error_code=None)
            self._write_required_memory(
                resolved_run_id,
                report,
                reflection=reflection,
                status=status,
                initial_reflection=initial_reflection,
                parsed_intent=parsed_intent,
            )
        except (TraceWriteError, RuntimeError) as exc:
            code = _code_from_exception(exc, "MEMORY_WRITE_FAILED")
            return self._fail(resolved_run_id, question, code, extra_payload=_failure_payload(parsed_intent))
        return {
            "run_id": resolved_run_id,
            "question": question,
            "status": status,
            "error_code": None,
            "reflection": reflection.model_dump(mode="json"),
            "report": report,
        }

    def _verify(self, run_id: str, *, repair_count: int, parsed_intent: ParsedIntent | None = None) -> Any:
        state = self._reflection_state(run_id, repair_count=repair_count, parsed_intent=parsed_intent)
        persisted = {row["evidence_id"]: row for row in self.dependencies.repository.get_evidences(run_id)}
        return verify_reflection(
            state,
            max_repair=int(getattr(self.dependencies.settings, "max_repair", 1)),
            persisted_evidence_by_id=persisted,
        )

    def _reflection_state(
        self,
        run_id: str,
        *,
        repair_count: int,
        parsed_intent: ParsedIntent | None = None,
    ) -> dict[str, Any]:
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
            "metric_id": run.get("metric_id") or (parsed_intent.metric_id if parsed_intent is not None else None),
            "target_date": run.get("target_date")
            or (parsed_intent.target_date if parsed_intent is not None else None)
            or getattr(self.dependencies.settings, "target_date"),
            "parsed_spec": {"filters": _parsed_intent_scope(parsed_intent)} if parsed_intent is not None else {"filters": {}},
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

    def _can_continue_after_terminal_agent_error(self, run_id: str, error_code: str) -> bool:
        if not _is_transient_llm_error_code(error_code):
            return False
        if self._is_no_anomaly(run_id):
            return True
        e4 = self.dependencies.repository.get_evidence(run_id=run_id, evidence_id=f"{run_id}:E4")
        e_rank = self.dependencies.repository.get_evidence(run_id=run_id, evidence_id=f"{run_id}:E_rank")
        if e4 is None or e_rank is None:
            return False
        if e4.get("guard_status") != "passed" or e_rank.get("guard_status") != "passed":
            return False
        e4_summary = e4.get("result_summary") or {}
        selected = e4_summary.get("selected_candidate")
        if not isinstance(selected, dict):
            return False
        return _has_required_evidence_chain(run_id, selected.get("evidence_ids"))

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

    def _read_required_memory(self, run_id: str, *, parsed_intent: ParsedIntent) -> list[dict[str, Any]]:
        if not getattr(self.dependencies.settings, "memory_enabled", False):
            return []
        repo = getattr(self.dependencies, "memory_repo", None)
        if repo is None:
            raise RuntimeError("MEMORY_READ_FAILED: memory repository unavailable")
        if not hasattr(repo, "read_layers"):
            raise RuntimeError("MEMORY_READ_FAILED: memory repository does not implement four-layer reads")
        try:
            raw_hits = [
                *repo.read_layers(f"{parsed_intent.metric_id}|semantic", layers=("semantic",)),
                *repo.read_layers(
                    f"{parsed_intent.metric_id}|run",
                    layers=("episodic", "reflection", "case"),
                ),
            ]
            scope = _parsed_intent_scope(parsed_intent)
            hits = _filter_memory_hits_by_scope(raw_hits, scope=scope)
            self.dependencies.trace_writer.write_step(
                run_id=run_id,
                node="memory_read",
                action="read_layers",
                input_summary={
                    "metric_id": parsed_intent.metric_id,
                    "mem_keys": [f"{parsed_intent.metric_id}|semantic", f"{parsed_intent.metric_id}|run"],
                    "layers": ["semantic", "episodic", "reflection", "case"],
                    "filters": scope,
                },
                output_summary={
                    "hit_count": len(hits),
                    "excluded_hit_count": len(raw_hits) - len(hits),
                    "hits": [_memory_hit_audit(hit) for hit in hits],
                },
                error_code=None,
            )
            return hits
        except RuntimeError as exc:
            raise RuntimeError("MEMORY_READ_FAILED: memory read failed") from exc

    def _write_triage_route(self, run_id: str, *, parsed_intent: ParsedIntent, family: str) -> None:
        self.dependencies.trace_writer.write_step(
            run_id=run_id,
            node="triage",
            action=f"route_{family}",
            input_summary={"metric_id": parsed_intent.metric_id},
            output_summary={"family": family, "metric_id": parsed_intent.metric_id},
            error_code=None,
        )

    def _flush_pending_token_usage(self, guard_context: Any) -> None:
        for usage in guard_context.drain_pending_token_usage():
            self._write_final_token_usage_trace(guard_context.run_id, usage)

    def _write_final_token_usage_trace(self, run_id: str, usage: dict[str, Any]) -> None:
        self.dependencies.trace_writer.write_step(
            run_id=run_id,
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

    def _write_required_memory(
        self,
        run_id: str,
        report: dict[str, Any] | None,
        *,
        reflection: Any,
        status: str,
        initial_reflection: Any | None = None,
        parsed_intent: ParsedIntent | None = None,
    ) -> None:
        if not _memory_write_on_finalize_enabled(self.dependencies.settings):
            return
        repo = getattr(self.dependencies, "memory_repo", None)
        if repo is None:
            raise RuntimeError("MEMORY_WRITE_FAILED: memory repository unavailable")
        if report is None:
            raise RuntimeError("MEMORY_WRITE_FAILED: terminal report required for memory write")
        candidate = report.get("top_candidate") if isinstance(report.get("top_candidate"), dict) else {}
        metric_id = report.get("metric_id")
        filters = _parsed_intent_scope(parsed_intent)
        dimension = candidate.get("dimension") if isinstance(candidate, dict) else None
        root_cause_type = candidate.get("root_cause_type") if isinstance(candidate, dict) else None
        verdict = candidate.get("verdict") if isinstance(candidate, dict) else None
        if status == "no_anomaly":
            root_cause_type = "no_anomaly"
            verdict = "no_anomaly"
        try:
            repair_count = int(getattr(reflection, "repair_count", 0) or 0)
            if repair_count > 0:
                self._write_reflection_memory(
                    run_id,
                    "REFLECTION_REPAIRED",
                    {
                        "repair_count": repair_count,
                        "metric_id": metric_id,
                        **({"filters": filters} if filters else {}),
                        "reflection_issues": _reflection_issues_payload(initial_reflection or reflection),
                    },
                )
            payload = {
                "run_id": run_id,
                "metric_id": metric_id,
                "dimension": dimension,
                "root_cause_type": root_cause_type,
                "verdict": verdict,
            }
            if filters:
                payload["filters"] = filters
            repo.write(
                {
                    "layer": "episodic",
                    "mem_key": f"{metric_id}|run",
                    "payload": payload,
                    "confidence": 0.8,
                    "source": "reflection_verified",
                }
            )
        except RuntimeError as exc:
            raise RuntimeError("MEMORY_WRITE_FAILED: memory write failed") from exc

    def _fail(
        self,
        run_id: str,
        question: str,
        code: str,
        *,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        secondary_error_code: str | None = None
        if code not in {"MEMORY_READ_FAILED", "MEMORY_WRITE_FAILED"}:
            try:
                self._write_reflection_memory(
                    run_id,
                    code,
                    {"gap_description": _message_for_failure_code(code), **(extra_payload or {})},
                )
            except RuntimeError as exc:
                secondary_error_code = _code_from_exception(exc, "MEMORY_WRITE_FAILED")
                LOGGER.warning(
                    "reflection memory write failed; preserving primary error_code=%s: %s",
                    code,
                    exc,
                )
        try:
            self._finish_run_with_observability(run_id, status="failed", error_code=code)
        except (TraceWriteError, RuntimeError) as exc:
            return {
                "run_id": run_id,
                "question": question,
                "status": "failed",
                "error_code": code,
                "finalization_error_code": _code_from_exception(exc, "SYSTEM_TABLE_READ_FAILED"),
                **({"secondary_error_code": secondary_error_code} if secondary_error_code else {}),
            }
        return {
            "run_id": run_id,
            "question": question,
            "status": "failed",
            "error_code": code,
            **({"secondary_error_code": secondary_error_code} if secondary_error_code else {}),
        }

    def _finish_run_with_observability(self, run_id: str, *, status: str, error_code: str | None) -> None:
        token_summary = build_token_summary(self.dependencies.repository.get_trace_steps(run_id))
        self.dependencies.trace_writer.finish_run(
            run_id=run_id,
            status=status,
            error_code=error_code,
            total_tokens=token_summary["total_tokens"],
            total_latency_ms=token_summary["latency_ms"],
            token_breakdown=token_summary["by_step"],
        )

    def _write_reflection_memory(self, run_id: str, error_code: str, extra_payload: dict[str, Any] | None = None) -> None:
        if not _memory_write_on_finalize_enabled(self.dependencies.settings):
            return
        repo = getattr(self.dependencies, "memory_repo", None)
        if repo is None:
            raise RuntimeError("MEMORY_WRITE_FAILED: memory repository unavailable")
        payload = {"run_id": run_id, "error_code": error_code, **(extra_payload or {})}
        metric_id = payload.get("metric_id") or (self.dependencies.repository.get_agent_run(run_id) or {}).get("metric_id")
        if not metric_id:
            raise RuntimeError("MEMORY_WRITE_FAILED: reflection memory requires metric_id")
        mem_key = f"{metric_id}|run"
        try:
            repo.write(
                {
                    "layer": "reflection",
                    "mem_key": mem_key,
                    "payload": payload,
                    "confidence": 0.75,
                    "source": "reflection_verified",
                }
            )
        except RuntimeError as exc:
            raise RuntimeError("MEMORY_WRITE_FAILED: reflection memory write failed") from exc


def _memory_write_on_finalize_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "memory_enabled", False)) and bool(
        getattr(settings, "memory_write_on_finalize", True)
    )


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


def _has_repair_action(reflection: Any) -> bool:
    return any(getattr(issue, "suggested_action", None) is not None for issue in getattr(reflection, "issues", []))


def _warn_on_run_outcome(result: Any, *, parsed_intent: ParsedIntent) -> RunOutcome | None:
    raw: Any = None
    if isinstance(result, dict):
        raw = result.get("structured_response")
    else:
        raw = getattr(result, "structured_response", None)
    if raw is None:
        LOGGER.warning("expert did not return structured RunOutcome; continuing from persisted artifacts")
        return None
    try:
        outcome = raw if isinstance(raw, RunOutcome) else RunOutcome.model_validate(raw)
    except (TypeError, ValueError) as exc:
        LOGGER.warning("malformed RunOutcome ignored; continuing from persisted artifacts: %s", exc)
        return None
    if outcome.metric_id != parsed_intent.metric_id:
        LOGGER.warning(
            "RunOutcome metric_id=%s does not match ParsedIntent metric_id=%s; continuing from persisted artifacts",
            outcome.metric_id,
            parsed_intent.metric_id,
        )
        return None
    return outcome


def _first_repair_action(reflection: Any) -> str | None:
    for issue in getattr(reflection, "issues", []):
        action = getattr(getattr(issue, "suggested_action", None), "action", None)
        if action:
            return str(action)
    return None


def _repair_instruction(reflection: Any, *, repair_action: str | None) -> str:
    repair_payload = json.dumps(_json_ready(reflection.model_dump(mode="json")), sort_keys=True)
    if repair_action is None:
        return f"Repair Reflection issue using persisted evidence only: {repair_payload}"
    repair_args = json.dumps(_json_ready(_first_repair_args(reflection)), sort_keys=True)
    continuation = _repair_continuation_text(repair_action)
    forbidden_tools = (
        "Do not call detect_anomaly or drilldown_dimension during repair."
        if repair_action != "detect_anomaly"
        else "After detect_anomaly, continue only if the returned E1 says is_anomaly=true."
    )
    return (
        "Repair Reflection issue using persisted evidence only.\n"
        f"Only call {repair_action} as the first repair tool. {forbidden_tools}\n"
        f"Required repair action: {repair_action}\n"
        f"Call exactly this tool with exactly these JSON args: {repair_action}({repair_args})\n"
        f"{continuation}\n"
        "Do not answer in text before the required repair tool call.\n"
        f"Reflection result: {repair_payload}"
    )


def _json_ready(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_json_default))


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _first_repair_args(reflection: Any) -> dict[str, Any]:
    for issue in getattr(reflection, "issues", []):
        suggested_action = getattr(issue, "suggested_action", None)
        args = getattr(suggested_action, "args", None)
        if isinstance(args, dict):
            return args
    return {}


def _repair_continuation_text(repair_action: str) -> str:
    if repair_action == "detect_anomaly":
        return (
            "If detect_anomaly returns E1 with is_anomaly=true, continue the normal RCA path: drilldown_dimension, "
            "fetch_related_signal, calculate_contribution, then rank_root_causes. If is_anomaly=false, stop."
        )
    if repair_action == "fetch_related_signal":
        return (
            "If fetch_related_signal returns E3, immediately call calculate_contribution with the exact E1/E2/E3 evidence_ids, "
            "then call rank_root_causes after E4."
        )
    if repair_action == "calculate_contribution":
        return "If calculate_contribution returns E4, immediately call rank_root_causes."
    return "After the required repair tool completes, stop unless that tool returns instructions for the mandatory next RCA step."


def _code_from_message(message: str, default: str) -> str:
    code = message.split(":", maxsplit=1)[0]
    return code if code and code.isupper() else default


def _code_from_exception(exc: BaseException, default: str) -> str:
    explicit_code = getattr(exc, "code", None)
    if isinstance(explicit_code, str) and explicit_code:
        if explicit_code.isupper():
            return explicit_code
        provider_code = _provider_transient_code(exc, explicit_code=explicit_code)
        if provider_code is not None:
            return provider_code
        return explicit_code
    typed_message_code = _code_from_message(str(exc), "")
    if typed_message_code:
        return typed_message_code
    transient_code = _provider_transient_code(exc)
    if transient_code is not None:
        return transient_code
    return _code_from_message(str(exc), default)


def _provider_transient_code(exc: BaseException, *, explicit_code: str | None = None) -> str | None:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return "RATE_LIMIT_EXCEEDED"
    if status_code in {408, 504}:
        return "REQUEST_TIMEOUT"
    if status_code in {500, 502, 503}:
        return "LLM_REQUIRED_UNAVAILABLE"

    text = f"{explicit_code or ''} {exc.__class__.__name__}: {exc}".lower()
    if "rate limit" in text or "rate_limit" in text or "too many requests" in text:
        return "RATE_LIMIT_EXCEEDED"
    if "timeout" in text or "timed out" in text:
        return "REQUEST_TIMEOUT"
    if (
        "api connection" in text
        or "connection error" in text
        or "temporarily unavailable" in text
        or "server error" in text
        or "internal server" in text
        or "bad gateway" in text
        or "service unavailable" in text
    ):
        return "LLM_REQUIRED_UNAVAILABLE"
    return None


def _is_transient_llm_error_code(error_code: str) -> bool:
    return str(error_code).lower() in {
        "llm_required_unavailable",
        "rate_limit_exceeded",
        "request_timeout",
        "timeout",
    }


def _has_required_evidence_chain(run_id: str, evidence_ids: Any) -> bool:
    if not isinstance(evidence_ids, list):
        return False
    prefix = f"{run_id}:"
    aliases = {
        str(evidence_id).removeprefix(prefix)
        for evidence_id in evidence_ids
        if str(evidence_id).startswith(prefix)
    }
    return all(
        any(alias == required or alias.startswith(f"{required}_") for alias in aliases)
        for required in {"E1", "E2", "E3", "E4", "E_rank"}
    )


def _agent_user_message(
    question: str,
    settings: Any,
    *,
    parsed_intent: ParsedIntent | None = None,
    memory_hits: list[dict[str, Any]] | None = None,
) -> str:
    target_date = parsed_intent.target_date if parsed_intent is not None else getattr(settings, "target_date", None)
    target_date_text = target_date.isoformat() if hasattr(target_date, "isoformat") else str(target_date)
    allowed_metrics = ", ".join(sorted(PHASE1_METRICS))
    explicit_scope = _parsed_intent_scope(parsed_intent)
    explicit_scope_text = ", ".join(f"{key}={value}" for key, value in sorted(explicit_scope.items()))
    parsed_metric_text = parsed_intent.metric_id if parsed_intent is not None else "unparsed"
    analysis_strategy_text = parsed_intent.analysis_strategy if parsed_intent is not None else "unparsed"
    discovery_policy = discovery_policy_from_intent(parsed_intent) if parsed_intent is not None else DiscoveryPolicy()
    discovery_policy_text = _discovery_policy_text(discovery_policy)
    memory_context_text = _memory_context_text(memory_hits or [])
    return (
        f"{question}\n\n"
        "Run context:\n"
        f"- target_date: {target_date_text}\n"
        f"- parsed target metric_id: {parsed_metric_text}\n"
        f"- parsed analysis_strategy: {analysis_strategy_text}\n"
        f"- discovery policy: {discovery_policy_text}\n"
        "- Discovery policy is mandatory: complete required_drilldowns first, then make the first related-signal call match first_signal exactly when listed.\n"
        "- Interpret relative dates such as yesterday as target_date unless the user gives an explicit date.\n"
        "- Every target_date argument MUST exactly equal the run context target_date above.\n"
        f"- allowed metric_id values: {allowed_metrics}\n"
        "- Every metric_id argument MUST equal the parsed target metric_id. Do not switch target metrics when checking causes.\n"
        "- Target metric is the KPI being explained. Words such as stockout, refund, UV, AOV, logistics, or quality are cause mechanisms to verify, not permission to change target metric.\n"
        "- Use metric_id exactly as listed above; do not uppercase, translate, or invent aliases.\n"
        f"- explicit or parsed question filters: {explicit_scope_text or 'none'}\n"
        f"- memory context: {memory_context_text}\n"
        "- If filters are listed, detect_anomaly, drilldown_dimension, and calculate_contribution must carry the same filters.\n"
        "- fetch_related_signal may omit filters, or pass filters only when they exactly match its selected dimension/element.\n"
    )


def _discovery_policy_text(policy: DiscoveryPolicy) -> str:
    parts: list[str] = []
    if policy.required_drilldowns:
        parts.append(f"required_drilldowns={','.join(policy.required_drilldowns)}")
    if policy.first_signal_dimension is not None or policy.first_signal_type is not None:
        parts.append(
            "first_signal="
            f"{policy.first_signal_dimension or 'any'}:{policy.first_signal_type or 'any'}"
        )
    if policy.first_signal_element is not None:
        parts.append(f"first_signal_element={policy.first_signal_element}")
    if policy.enforce_first_signal_top_candidate:
        parts.append("first_signal_must_use_top_drilldown_candidate=true")
    return "; ".join(parts) if parts else "none"


def _parsed_intent_scope(parsed_intent: ParsedIntent | None) -> dict[str, str]:
    if parsed_intent is None:
        return {}
    if len(parsed_intent.filters) == 1:
        key, value = next(iter(parsed_intent.filters.items()))
        return {str(key): str(value)}
    if parsed_intent.dimension is not None and parsed_intent.element is not None:
        return {str(parsed_intent.dimension): str(parsed_intent.element)}
    return {}


def _memory_context_text(memory_hits: list[dict[str, Any]]) -> str:
    if not memory_hits:
        return "none"
    fragments = []
    for hit in memory_hits[:6]:
        layer = hit.get("layer")
        mem_key = hit.get("mem_key")
        hit_payload = hit.get("payload") if isinstance(hit.get("payload"), dict) else {}
        public_hit = {**hit_payload, **hit}
        public_hit.pop("payload", None)
        payload = {
            key: value
            for key, value in public_hit.items()
            if key
            in {
                "metric_id",
                "dimension",
                "filters",
                "root_cause_type",
                "verdict",
                "error_code",
                "display_name",
                "confidence",
            }
        }
        fragments.append(f"{layer}:{mem_key}:{payload}")
    return "; ".join(fragments)


def _memory_hit_audit(hit: dict[str, Any]) -> dict[str, Any]:
    filters = _memory_hit_filters(hit)
    return {
        "memory_id": hit.get("memory_id"),
        "layer": hit.get("layer"),
        "mem_key": hit.get("mem_key"),
        "filters": filters,
        "confidence": hit.get("confidence"),
        "source": hit.get("source"),
    }


def _reflection_issues_payload(reflection: Any) -> list[dict[str, Any]]:
    issues = getattr(reflection, "issues", []) or []
    payload: list[dict[str, Any]] = []
    for issue in issues:
        if hasattr(issue, "model_dump"):
            payload.append(issue.model_dump(mode="json"))
        elif isinstance(issue, dict):
            payload.append(dict(issue))
        else:
            payload.append({"message": str(issue)})
    return payload


def _failure_payload(
    parsed_intent: ParsedIntent | None,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload = dict(extra_payload or {})
    if parsed_intent is not None:
        payload.setdefault("metric_id", parsed_intent.metric_id)
        filters = _parsed_intent_scope(parsed_intent)
        if filters:
            payload.setdefault("filters", filters)
    return payload or None


def _filter_memory_hits_by_scope(hits: list[dict[str, Any]], *, scope: dict[str, str]) -> list[dict[str, Any]]:
    return [hit for hit in hits if _memory_hit_matches_scope(hit, scope=scope)]


def _memory_hit_matches_scope(hit: dict[str, Any], *, scope: dict[str, str]) -> bool:
    hit_filters = _memory_hit_filters(hit)
    if not hit_filters:
        return not scope or str(hit.get("layer")) == "semantic"
    return hit_filters == scope


def _memory_hit_filters(hit: dict[str, Any]) -> dict[str, str]:
    payload = hit.get("payload") if isinstance(hit.get("payload"), dict) else {}
    raw_filters = hit.get("filters", payload.get("filters"))
    if raw_filters is None:
        return {}
    if not isinstance(raw_filters, dict):
        raise RuntimeError("MEMORY_READ_FAILED: memory filters must be an object")
    return {str(key): str(value) for key, value in raw_filters.items()}


def _message_for_failure_code(code: str) -> str:
    return code.lower().replace("_", " ")

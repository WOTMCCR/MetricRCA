"""Top-level deterministic RCA runtime service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from metric_rca.config.settings import Settings, get_settings
from metric_rca.domain.models import Evidence, QuerySpec, RootCauseCandidate
from metric_rca.observability.summary import build_token_summary
from metric_rca.observability.trace import TraceWriteError
from metric_rca.reporting.projector import build_report_from_persisted_artifacts
from metric_rca.runtime.plan_compiler import RcaPlanCompiler
from metric_rca.runtime.plan_compiler import PlanCompilerError
from metric_rca.runtime.plan_executor import RcaPlanExecutor
from metric_rca.runtime.run_context import RunContext
from metric_rca.runtime.sdk_tools import ToolExecutor
from metric_rca.services.metric_contracts import MetricServiceError, ParsedIntent


ReflectionVerifier = Callable[[str, int, ParsedIntent], Any]
ReportProjector = Callable[[str, str], dict[str, Any] | None]


class RunService:
    def __init__(
        self,
        *,
        dependencies: Any,
        plan_compiler: Any | None = None,
        plan_executor: Any | None = None,
        reflection_verifier: ReflectionVerifier | None = None,
        report_projector: ReportProjector | None = None,
    ) -> None:
        self.dependencies = dependencies
        self._plan_compiler = plan_compiler or RcaPlanCompiler()
        self._plan_executor = plan_executor or RcaPlanExecutor(
            tool_executor=ToolExecutor(dependencies=dependencies),
            trace_writer=dependencies.trace_writer,
        )
        self._reflection_verifier = reflection_verifier or self._verify_reflection
        self._report_projector = report_projector or self._project_report

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
            plan = self._plan_compiler.compile(
                run_id=resolved_run_id,
                parsed_intent=parsed_intent,
                budget=_budget_from_settings(self.dependencies.settings),
            )
            execution = self._plan_executor.execute(
                RunContext(
                    run_id=resolved_run_id,
                    metric_id=parsed_intent.metric_id,
                    target_date=parsed_intent.target_date,
                    explicit_scope=plan.explicit_scope,
                    budget=plan.budget,
                    repository=self.dependencies.repository,
                ),
                plan,
            )
        except (MetricServiceError, PlanCompilerError) as exc:
            return self._fail(resolved_run_id, question, _code_from_exception(exc, "RUN_SERVICE_FAILED"))

        if execution.status == "failed":
            return self._fail(resolved_run_id, question, execution.error_code or "PLAN_EXECUTION_FAILED")

        reflection = self._reflection_verifier(resolved_run_id, 0, parsed_intent)
        if execution.status == "succeeded" and not getattr(reflection, "passed", False):
            return self._fail(resolved_run_id, question, "REFLECTION_REPAIR_FAILED")

        status = "no_anomaly" if execution.status == "no_anomaly" else "succeeded"
        report = self._report_projector(resolved_run_id, status)
        if report is None:
            return self._fail(resolved_run_id, question, "REPORT_PROJECTION_FAILED")
        try:
            self._create_required_tasks(resolved_run_id, report)
            self._finish_run(resolved_run_id, status=status, error_code=None)
        except (RuntimeError, TraceWriteError) as exc:
            return {
                "run_id": resolved_run_id,
                "question": question,
                "status": "failed",
                "error_code": "RUN_FINALIZATION_FAILED",
                "finalization_error_code": _code_from_exception(exc, "SYSTEM_TABLE_READ_FAILED"),
            }
        return {
            "run_id": resolved_run_id,
            "question": question,
            "status": status,
            "error_code": None,
            "reflection": reflection.model_dump(mode="json") if hasattr(reflection, "model_dump") else {},
            "report": report,
        }

    def _fail(self, run_id: str, question: str, code: str) -> dict[str, Any]:
        try:
            self._finish_run(run_id, status="failed", error_code=code)
        except (RuntimeError, TraceWriteError) as exc:
            return {
                "run_id": run_id,
                "question": question,
                "status": "failed",
                "error_code": code,
                "finalization_error_code": _code_from_exception(exc, "SYSTEM_TABLE_READ_FAILED"),
            }
        return {"run_id": run_id, "question": question, "status": "failed", "error_code": code}

    def _finish_run(self, run_id: str, *, status: str, error_code: str | None) -> None:
        trace_steps = self.dependencies.repository.get_trace_steps(run_id)
        token_summary = build_token_summary(trace_steps)
        self.dependencies.trace_writer.finish_run(
            run_id=run_id,
            status=status,
            error_code=error_code,
            total_tokens=token_summary["total_tokens"],
            total_latency_ms=token_summary["latency_ms"],
            token_breakdown=token_summary["by_step"],
        )

    def _project_report(self, run_id: str, status: str) -> dict[str, Any] | None:
        run = dict(self.dependencies.repository.get_agent_run(run_id) or {})
        run["status"] = status
        return build_report_from_persisted_artifacts(
            agent_run=run,
            evidences=self.dependencies.repository.get_evidences(run_id),
            tasks=self.dependencies.repository.get_operation_tasks(run_id),
        )

    def _verify_reflection(self, run_id: str, repair_count: int, parsed_intent: ParsedIntent) -> Any:
        from metric_rca.agent.reflection import verify_reflection

        state = self._reflection_state(run_id, repair_count=repair_count, parsed_intent=parsed_intent)
        persisted = {row["evidence_id"]: row for row in self.dependencies.repository.get_evidences(run_id)}
        return verify_reflection(
            state,
            max_repair=int(getattr(self.dependencies.settings, "max_repair", 1)),
            persisted_evidence_by_id=persisted,
        )

    def _reflection_state(self, run_id: str, *, repair_count: int, parsed_intent: ParsedIntent) -> dict[str, Any]:
        evidences = [self._evidence_from_row(row) for row in self.dependencies.repository.get_evidences(run_id)]
        e4 = next((ev for ev in evidences if ev.evidence_id == f"{run_id}:E4"), None)
        candidates: list[RootCauseCandidate] = []
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
            "metric_id": run.get("metric_id") or parsed_intent.metric_id,
            "target_date": run.get("target_date") or parsed_intent.target_date,
            "parsed_spec": {"filters": _parsed_intent_scope(parsed_intent)},
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

    def _create_required_tasks(self, run_id: str, report: dict[str, Any] | None) -> None:
        if report is None or report.get("status") != "succeeded":
            return
        candidate = report.get("top_candidate")
        if not isinstance(candidate, dict):
            return
        root_cause_type = str(candidate.get("root_cause_type") or "")
        if not root_cause_type:
            return
        self.dependencies.repository.create_operation_task(
            {
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
        )


def _parsed_intent_scope(parsed_intent: ParsedIntent | None) -> dict[str, str]:
    if parsed_intent is None:
        return {}
    if len(parsed_intent.filters) == 1:
        key, value = next(iter(parsed_intent.filters.items()))
        return {str(key): str(value)}
    if parsed_intent.dimension is not None and parsed_intent.element is not None:
        return {str(parsed_intent.dimension): str(parsed_intent.element)}
    return {}


def _budget_from_settings(settings: Settings | Any) -> dict[str, int]:
    return {
        "max_steps": int(getattr(settings, "max_steps", 8)),
        "max_query": int(getattr(settings, "max_query", 12)),
        "max_drilldown_depth": int(getattr(settings, "max_drilldown_depth", 3)),
    }


def _code_from_exception(exc: BaseException, default: str) -> str:
    explicit_code = getattr(exc, "code", None)
    if isinstance(explicit_code, str) and explicit_code:
        return explicit_code
    message_code = str(exc).split(":", maxsplit=1)[0]
    if message_code and message_code.isupper():
        return message_code
    return default


def build_runtime_dependencies(
    *,
    settings: Settings | None = None,
    repository: Any | None = None,
    metric_service: Any | None = None,
    renderer: Any | None = None,
    trace_writer: Any | None = None,
    memory_repo: Any | None = None,
) -> Any:
    from metric_rca.agent.runner import build_dependencies

    _ = datetime.now(timezone.utc)
    return build_dependencies(
        settings=settings or get_settings(),
        repository=repository,
        metric_service=metric_service,
        renderer=renderer,
        trace_writer=trace_writer,
        memory_repo=memory_repo,
    )

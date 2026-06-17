"""Compatibility guard types backed by the runtime ActionGate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from metric_rca.runtime.action_gate import ActionGate
from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_models import GateDecision, RcaAction
from metric_rca.runtime.run_context import RunContext


class ActionGateMiddlewareError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class RunGuardContext:
    run_id: str
    settings: Any
    trace_writer: Any
    tool_arg_schemas: dict[str, Any]
    repository: Any | None = None
    step_count: int = 0
    query_count: int = 0
    drilldown_depth: int = 0
    budget_exhausted_once: bool = False
    failed: bool = False
    error_code: str | None = None
    token_usage_by_call: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_token_usage: list[dict[str, Any]] = field(default_factory=list)
    explicit_filters: dict[str, str] = field(default_factory=dict)
    target_metric_id: str | None = None
    target_date: Any | None = None

    def mark_failed(self, code: str) -> None:
        self.failed = True
        self.error_code = code

    def record_token_usage(self, usage: dict[str, Any]) -> None:
        self.pending_token_usage.append(usage)

    def token_usage_for_call(self, tool_call_id: str) -> dict[str, Any] | None:
        return self.token_usage_by_call.get(tool_call_id) or (
            self.pending_token_usage.pop(0) if self.pending_token_usage else None
        )

    def drain_pending_token_usage(self) -> list[dict[str, Any]]:
        pending = [*self.pending_token_usage]
        self.pending_token_usage.clear()
        return pending


class MetricRCATokenUsageCallback:
    def __init__(self, context: RunGuardContext) -> None:
        self.context = context

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = getattr(response, "llm_output", None)
        if isinstance(usage, dict) and isinstance(usage.get("token_usage"), dict):
            self.context.record_token_usage(usage["token_usage"])


class ActionGateMiddleware:
    """Compatibility facade for callers that still construct a guard object."""

    def __init__(self, context: RunGuardContext) -> None:
        self.context = context
        self._gate = ActionGate()

    def validate(self, action: RcaAction, evidence_graph: EvidenceGraph) -> GateDecision:
        if self.context.target_metric_id is None or self.context.target_date is None:
            raise ActionGateMiddlewareError("METRIC_SCOPE_VIOLATION", "run target metric/date must be set before validation")
        ctx = RunContext(
            run_id=self.context.run_id,
            metric_id=self.context.target_metric_id,
            target_date=self.context.target_date,
            explicit_scope=self.context.explicit_filters,
            budget={
                "max_steps": int(getattr(self.context.settings, "max_steps", 8)),
                "max_query": int(getattr(self.context.settings, "max_query", 12)),
                "max_drilldown_depth": int(getattr(self.context.settings, "max_drilldown_depth", 3)),
            },
            repository=self.context.repository,
            step_count=self.context.step_count,
            query_count=self.context.query_count,
            drilldown_depth=self.context.drilldown_depth,
            budget_exhausted_once=self.context.budget_exhausted_once,
        )
        decision = self._gate.validate(ctx, action, evidence_graph)
        if not decision.allowed and decision.error_code:
            self.context.error_code = decision.error_code
        return decision

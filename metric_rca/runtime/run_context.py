"""Runtime context shared by deterministic RCA plan execution components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from metric_rca.runtime.dependencies import RuntimeRepository


def _default_budget() -> dict[str, int]:
    return {"max_steps": 8, "max_query": 12, "max_drilldown_depth": 3}


@dataclass
class RunContext:
    run_id: str
    metric_id: str
    target_date: date
    explicit_scope: dict[str, str] = field(default_factory=dict)
    budget: dict[str, int] = field(default_factory=_default_budget)
    repository: RuntimeRepository | None = None
    step_count: int = 0
    query_count: int = 0
    drilldown_depth: int = 0
    budget_exhausted_once: bool = False
    failed: bool = False
    error_code: str | None = None

    def mark_failed(self, error_code: str) -> None:
        self.failed = True
        self.error_code = error_code

    def record_allowed_action(self, action_kind: str) -> None:
        self.step_count += 1
        if action_kind in {"detect_anomaly", "drilldown_dimension", "fetch_related_signal", "calculate_contribution"}:
            self.query_count += 1
        if action_kind == "drilldown_dimension":
            self.drilldown_depth += 1

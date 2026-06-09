"""Dependency container for API routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from metric_rca.agent.graph import run_rca
from metric_rca.config.settings import Settings, get_settings
from metric_rca.evals.runner import run_eval
from metric_rca.repositories.metric_repository import MetricRepository


RcaRunner = Callable[..., dict[str, Any]]
EvalRunner = Callable[..., dict[str, Any]]


@dataclass
class ApiDependencies:
    repository: Any | None = None
    rca_runner: RcaRunner = run_rca
    eval_runner: EvalRunner = run_eval

    def get_repository(self) -> Any:
        if self.repository is None:
            self.repository = MetricRepository.from_settings(get_settings())
        return self.repository


def settings_with_overrides(
    *,
    target_date: Any = None,
    business_today: Any = None,
    memory_enabled: bool | None = None,
    memory_required: bool | None = None,
) -> Settings:
    values = get_settings().model_dump()
    if target_date is not None:
        values["target_date"] = target_date
    if business_today is not None:
        values["business_today"] = business_today
    if memory_enabled is not None:
        values["memory_enabled"] = memory_enabled
    if memory_required is not None:
        values["memory_required"] = memory_required
    return Settings(**values)

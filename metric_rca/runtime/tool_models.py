"""Shared contracts for deterministic runtime tool execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from metric_rca.domain.models import Observation, RootCauseCandidate, StrictModel
from metric_rca.runtime.dependencies import RuntimeDependencies


@dataclass(frozen=True)
class MetricRCAToolHandler:
    args_model: type[StrictModel]
    call: Callable[[Any, RuntimeDependencies], Any]


class ToolExecutionResult(StrictModel):
    observation: Observation
    evidence_ids: list[str] = []
    candidates: list[RootCauseCandidate] = []


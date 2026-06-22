"""Typed helpers for persisted-artifact evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


class EvalRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(code if message is None else f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    question: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RootCauseTruth:
    root_cause_type: str
    dimension: str | None
    element: str | None
    weight: float = 1.0


@dataclass(frozen=True)
class GroundTruth:
    case_id: str
    business_date: date
    metric_id: str
    expected_anomaly: bool
    root_cause_type: str | None
    dimension: str | None
    element: str | None
    root_causes: tuple[RootCauseTruth, ...] = ()


@dataclass(frozen=True)
class PersistedArtifacts:
    agent_run: dict[str, Any] | None
    evidences: list[dict[str, Any]]
    trace_steps: list[dict[str, Any]]
    sql_audit: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    report: dict[str, Any] | None
    memory_records: list[dict[str, Any]] = field(default_factory=list)

"""Typed RCA plan and execution contracts."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator

from metric_rca.domain.models import StrictModel


RcaActionKind = Literal[
    "detect_anomaly",
    "drilldown_dimension",
    "fetch_related_signal",
    "calculate_contribution",
    "rank_root_causes",
]


class RcaAction(StrictModel):
    action_id: str
    kind: RcaActionKind
    args: dict[str, Any] = Field(default_factory=dict)
    requires: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    dynamic: bool = False

    @field_validator("requires", "produces")
    @classmethod
    def _aliases_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("PLAN_INVALID")
        return value


class CasePrior(StrictModel):
    metric_id: str
    preferred_dimensions: list[str] = Field(default_factory=list)
    preferred_signal_types: list[str] = Field(default_factory=list)
    prior_root_causes: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    source_memory_ids: list[str] = Field(default_factory=list)


class RcaPlan(StrictModel):
    run_id: str
    metric_id: str
    target_date: date
    question_family: str
    family: Literal["gmv_family", "rate_family"]
    explicit_scope: dict[str, str] = Field(default_factory=dict)
    actions: list[RcaAction]
    budget: dict[str, int]
    memory_hints: list[CasePrior] = Field(default_factory=list)

    @field_validator("actions")
    @classmethod
    def _actions_must_be_unique(cls, value: list[RcaAction]) -> list[RcaAction]:
        action_ids = [action.action_id for action in value]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("PLAN_INVALID")
        return value


class GateDecision(StrictModel):
    allowed: bool
    error_code: str | None = None
    message: str | None = None


class ExecutionResult(StrictModel):
    status: Literal["succeeded", "no_anomaly", "failed"]
    error_code: str | None = None
    produced_evidence_ids: list[str] = Field(default_factory=list)


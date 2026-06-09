"""Shared typed schemas for deterministic P2 tools."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from metric_rca.domain.models import Evidence, Observation, RootCauseCandidate, StrictModel


class DetectAnomalyArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date
    filters: dict[str, str] = Field(default_factory=dict)


class DrilldownDimensionArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date
    dimension: str
    evidence_ids: list[str]
    filters: dict[str, str] = Field(default_factory=dict)


class FetchRelatedSignalArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date
    signal_type: Literal["campaign", "inventory", "conversion", "refund_quality"]
    dimension: str
    element: str
    evidence_ids: list[str]


class CalculateContributionArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date
    dimension: str
    element: str
    evidence_ids: list[str]
    filters: dict[str, str] = Field(default_factory=dict)


class ToolResult(StrictModel):
    observation: Observation
    evidences: list[Evidence] = Field(default_factory=list)
    evidence_alias: str | None = None
    candidates: list[RootCauseCandidate] = Field(default_factory=list)

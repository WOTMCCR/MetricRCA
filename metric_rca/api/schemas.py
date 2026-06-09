"""Pydantic schemas for MetricRCA API responses."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunCreateRequest(BaseModel):
    question: str = Field(min_length=1)
    target_date: date | None = None
    business_today: date | None = None
    memory_enabled: bool | None = None
    memory_required: bool | None = None


class ErrorBody(BaseModel):
    error_code: str
    message: str
    recoverable: bool = False
    retryable: bool = False
    trace_step_id: str | None = None
    suggested_next_action: str | None = None


class RunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    error_code: str | None = None
    report: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)


class TraceResponse(BaseModel):
    run_id: str
    trace: list[dict[str, Any]]


class EvidenceResponse(BaseModel):
    run_id: str
    evidence: list[dict[str, Any]]


class SqlAuditResponse(BaseModel):
    run_id: str
    sql_audit: list[dict[str, Any]]


class TasksResponse(BaseModel):
    run_id: str
    tasks: list[dict[str, Any]]


class MemoryResponse(BaseModel):
    run_id: str
    memory: list[dict[str, Any]]


class EvalResponse(BaseModel):
    eval_id: str
    summary: dict[str, Any]
    cases: list[dict[str, Any]]

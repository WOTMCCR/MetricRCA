"""Pydantic schemas for MetricRCA API responses."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


ScoreFlag = Annotated[int, Field(strict=True, ge=0, le=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
NonNegativeNumber = Annotated[float, Field(strict=True, ge=0.0)]
Rate = Annotated[float, Field(strict=True, ge=0.0, le=1.0)]


class RunCreateRequest(BaseModel):
    question: str = Field(min_length=1)
    target_date: date | None = None
    business_today: date | None = None
    memory_enabled: bool | None = None
    memory_required: bool | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None


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
    token_summary: dict[str, Any] | None = None
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


class EvalSummaryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_total: NonNegativeInt | None = None
    intent_accuracy: Rate | None = None
    top1_rate: Rate | None = None
    top3_rate: Rate | None = None
    anomaly_accuracy: Rate | None = None
    evidence_coverage_avg: Rate | None = None
    sql_safe_rate: Rate | None = None
    report_traceable_rate: Rate | None = None
    reflection_repair_ok: StrictBool | None = None
    memory_pollution_ok: StrictBool | None = None
    dangerous_sql_blocked: StrictBool | None = None
    no_anomaly_correct: StrictBool | None = None
    avg_tokens_per_case: NonNegativeNumber | None = None
    avg_latency_ms_per_case: NonNegativeNumber | None = None
    memory_enabled_top1_rate: Rate | None = None
    memory_disabled_top1_rate: Rate | None = None
    memory_hit_improvement: Annotated[float, Field(strict=True, ge=-1.0, le=1.0)] | None = None
    llm_provider: str | None = Field(default=None, min_length=1)
    llm_model: str | None = Field(default=None, min_length=1)
    configured_case_total: NonNegativeInt | None = None
    completed_case_total: NonNegativeInt | None = None
    completed_memory_case_total: NonNegativeInt | None = None
    complete: StrictBool | None = None
    thresholds_met: StrictBool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_known_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        null_fields = sorted(key for key, value in data.items() if value is None)
        if null_fields:
            raise ValueError(f"eval summary fields must not be null: {', '.join(null_fields)}")
        return data

    @model_validator(mode="after")
    def require_complete_summary_fields(self) -> EvalSummaryPayload:
        if self.complete is not True:
            return self
        required = {
            "case_total",
            "intent_accuracy",
            "top1_rate",
            "top3_rate",
            "anomaly_accuracy",
            "evidence_coverage_avg",
            "sql_safe_rate",
            "report_traceable_rate",
            "reflection_repair_ok",
            "memory_pollution_ok",
            "dangerous_sql_blocked",
            "no_anomaly_correct",
            "avg_tokens_per_case",
            "avg_latency_ms_per_case",
            "memory_enabled_top1_rate",
            "memory_disabled_top1_rate",
            "memory_hit_improvement",
            "llm_provider",
            "llm_model",
            "configured_case_total",
            "completed_case_total",
            "completed_memory_case_total",
            "thresholds_met",
        }
        missing = sorted(field for field in required if getattr(self, field) is None)
        if missing:
            raise ValueError(f"complete eval summary missing required fields: {', '.join(missing)}")
        return self


class EvalSummaryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: EvalSummaryPayload


class EvalCaseResultDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    report_traceable_ok: ScoreFlag
    memory_pollution_ok: ScoreFlag
    no_anomaly_task_ok: ScoreFlag
    adtributor_used: ScoreFlag
    multi_agent_path: str = Field(min_length=1)


class EvalCaseResultCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    intent_ok: ScoreFlag
    anomaly_ok: ScoreFlag
    top1_ok: ScoreFlag
    top3_ok: ScoreFlag
    evidence_coverage: Rate
    sql_safe: ScoreFlag
    reflection_repair_ok: ScoreFlag
    detail: EvalCaseResultDetail


class EvalSummaryStoreResponse(BaseModel):
    eval_id: str
    status: str


class EvalCaseResultStoreResponse(BaseModel):
    eval_id: str
    case_id: str
    status: str

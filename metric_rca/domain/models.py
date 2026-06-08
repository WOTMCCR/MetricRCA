from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from metric_rca.domain.enums import DimensionId, MetricId


ALLOWED_METRICS = {item.value for item in MetricId}
PHASE1_METRICS = ALLOWED_METRICS - {MetricId.CAMPAIGN_ROI.value}
ALLOWED_DIMENSIONS = {item.value for item in DimensionId}
METRIC_ALLOWED_DIMENSIONS: dict[str, set[str]] = {
    "gmv": {"channel", "category", "device", "product"},
    "net_gmv": {"channel", "category", "device", "product"},
    "uv": {"channel", "category", "device", "product"},
    "pay_cvr": {"channel", "category", "device", "product"},
    "refund_rate": {"channel", "category", "device", "product"},
    "stockout_rate": {"category", "warehouse", "product"},
    "complaint_rate": {"category", "product"},
    "aov": {"channel", "category", "device", "product"},
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRange(StrictModel):
    start_date: date
    end_date: date
    tz: str = "Asia/Tokyo"


class MetricDefinition(StrictModel):
    metric_id: str
    display_name: str
    formula: str
    numerator_sql_fragment: str | None = None
    denominator_sql_fragment: str | None = None
    higher_is_better: bool = True
    allowed_dimensions: list[str] = Field(default_factory=list)
    source_table: str


class Dimension(StrictModel):
    dim_id: str
    column: str
    table: str


class Baseline(StrictModel):
    method: Literal["prev_4_same_weekday"] = "prev_4_same_weekday"
    baseline_dates: list[date]
    baseline_mean: float
    baseline_std: float
    sample_n: int


class QuerySpec(StrictModel):
    metric_id: str
    time_range: TimeRange
    group_by: list[str] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    limit: int = Field(default=1000, le=5000)
    purpose: Literal["current", "baseline", "drilldown", "signal"] = "current"

    @field_validator("metric_id")
    @classmethod
    def _metric_whitelist(cls, value: str) -> str:
        if value not in PHASE1_METRICS:
            raise ValueError("QUERY_SPEC_INVALID")
        return value

    @field_validator("group_by")
    @classmethod
    def _limit_groupby(cls, value: list[str]) -> list[str]:
        if len(value) > 2:
            raise ValueError("group_by dimension count exceeds MVP limit(2)")
        invalid = [dim for dim in value if dim not in ALLOWED_DIMENSIONS]
        if invalid:
            raise ValueError("DIMENSION_NOT_ALLOWED")
        return value

    @field_validator("filters")
    @classmethod
    def _filter_whitelist(cls, value: dict[str, str]) -> dict[str, str]:
        invalid = [dim for dim in value if dim not in ALLOWED_DIMENSIONS]
        if invalid:
            raise ValueError("DIMENSION_NOT_ALLOWED")
        return value

    @model_validator(mode="after")
    def _metric_dimension_whitelist(self) -> QuerySpec:
        allowed = METRIC_ALLOWED_DIMENSIONS[self.metric_id]
        invalid = [
            dimension
            for dimension in [*self.group_by, *self.filters.keys()]
            if dimension not in allowed
        ]
        if invalid:
            raise ValueError("DIMENSION_NOT_ALLOWED")
        return self


class SQLPlan(StrictModel):
    sql: str
    sql_hash: str
    guard_status: Literal["passed", "rejected"] = "rejected"
    guard_errors: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    renderer_signature: str | None = None
    guard_signature: str | None = None


class Evidence(StrictModel):
    evidence_id: str
    query_spec: QuerySpec
    sql: str
    sql_hash: str
    guard_status: str
    result_summary: dict[str, Any]
    data_source: str
    created_at: datetime


class Observation(StrictModel):
    action_name: str
    ok: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    message: str | None = None


class RootCauseCandidate(StrictModel):
    root_cause_type: str
    dimension: str | None = None
    element: str | None = None
    contribution_pct: float
    signal_severity: float
    evidence_support: float
    reflection_factor: float = 1.0
    eng_confidence: float
    verdict: str
    evidence_ids: list[str] = Field(default_factory=list)


class AgentAction(StrictModel):
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None


class ReflectionIssue(StrictModel):
    check: str
    severity: Literal["error", "warning"]
    by: Literal["rule", "llm"]
    message: str
    suggested_action: AgentAction | None = None


class ReflectionResult(StrictModel):
    passed: bool
    issues: list[ReflectionIssue] = Field(default_factory=list)
    repaired: bool = False
    repair_count: int = 0


class MemoryRecord(StrictModel):
    memory_id: str
    layer: Literal["case", "semantic", "episodic", "reflection"]
    key: str
    payload: dict[str, Any]
    confidence: float = 0.5
    source: str = "system"
    version: int = 1
    ttl_days: int | None = None
    created_at: datetime


class EvalCase(StrictModel):
    case_id: str
    question: str
    expected_metric: str
    expected_anomaly: bool
    expected_root_cause: str | None = None
    expected_dimension: str | None = None
    expected_element: str | None = None


class TraceStep(StrictModel):
    step_id: str
    run_id: str
    seq: int
    node: str
    action: str | None = None
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    latency_ms: int = 0
    created_at: datetime


class AgentRun(StrictModel):
    run_id: str
    question: str
    status: Literal["running", "succeeded", "no_anomaly", "failed"] = "running"
    metric_id: str | None = None
    target_date: date
    error_code: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

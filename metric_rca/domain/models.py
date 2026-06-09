"""Pydantic v2 契约模型：系统所有结构化数据的"边界守门人"。

核心约定：所有契约模型继承 `StrictModel`，即 `model_config = ConfigDict(extra="forbid")`，
让任何非法 / 拼错字段在入口即抛 `ValidationError`。这既是文档硬要求，也是让测试能够
"击穿 shortcut"的前提——脏数据进不来，下游就不必到处做防御性兜底。

其中 `QuerySpec` 是替代"任意 Text-to-SQL"的核心受控契约，带多重白名单校验。

对应 docs/MetricRCA.md §2；docs/COMPLIANCE_MATRIX.md 第 6、7 行。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from metric_rca.domain.enums import DimensionId, MetricId


# —— 白名单常量：从枚举派生，单一事实来源，避免与 enums.py 不一致 ——
ALLOWED_METRICS = {item.value for item in MetricId}
# Phase 1 取数指标 = 全部指标去掉 1 个月才做的 campaign_roi。
PHASE1_METRICS = ALLOWED_METRICS - {MetricId.CAMPAIGN_ROI.value}
ALLOWED_DIMENSIONS = {item.value for item in DimensionId}
# 指标 ↔ 维度的交叉白名单：不同指标允许的下钻维度不同（如 complaint_rate 没有 channel 口径）。
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
    """所有契约模型的基类：禁止额外字段（extra="forbid"）。"""

    model_config = ConfigDict(extra="forbid")


class TimeRange(StrictModel):
    """查询时间窗（业务本地日）。tz 默认 Asia/Tokyo，与 business_date 口径一致。"""

    start_date: date
    end_date: date
    tz: str = "Asia/Tokyo"


class MetricDefinition(StrictModel):
    """指标元数据（来自 metric_definition 表）：公式、口径、可下钻维度、来源表。"""

    metric_id: str
    display_name: str
    formula: str
    numerator_sql_fragment: str | None = None
    denominator_sql_fragment: str | None = None
    higher_is_better: bool = True
    allowed_dimensions: list[str] = Field(default_factory=list)
    source_table: str


class Dimension(StrictModel):
    """维度元数据：维度 id → 物理列 / 表，供渲染器确定性映射 JOIN。"""

    dim_id: str
    column: str
    table: str


class Baseline(StrictModel):
    """异常检测基线（Phase 2 使用）：前 4 个同星期几的均值 / 标准差 / 样本数。"""

    method: Literal["prev_4_same_weekday"] = "prev_4_same_weekday"
    baseline_dates: list[date]
    baseline_mean: float
    baseline_std: float
    sample_n: int


class QuerySpec(StrictModel):
    """受控查询规格——替代任意 SQL 的唯一意图载体（绝不携带原始 SQL）。

    通过三层校验保证安全：
    1) metric_id 必须是 Phase 1 指标；
    2) group_by ≤2 且全部是合法维度；
    3) filters 的键也必须是合法维度；
    4) model_validator 再做"指标↔维度"交叉白名单。
    """

    metric_id: str
    time_range: TimeRange
    group_by: list[str] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    limit: int = Field(default=1000, le=5000)  # 强制上限，渲染器还会再拼 LIMIT
    purpose: Literal["current", "baseline", "drilldown", "signal"] = "current"
    signal_type: Literal["metric", "campaign"] = "metric"

    @field_validator("metric_id")
    @classmethod
    def _metric_whitelist(cls, value: str) -> str:
        # 非 Phase 1 指标（如 campaign_roi）直接拒绝。
        if value not in PHASE1_METRICS:
            raise ValueError("QUERY_SPEC_INVALID")
        return value

    @field_validator("group_by")
    @classmethod
    def _limit_groupby(cls, value: list[str]) -> list[str]:
        # MVP 最多 2 维下钻；且每个维度都必须在维度白名单内。
        if len(value) > 2:
            raise ValueError("group_by dimension count exceeds MVP limit(2)")
        invalid = [dim for dim in value if dim not in ALLOWED_DIMENSIONS]
        if invalid:
            raise ValueError("DIMENSION_NOT_ALLOWED")
        return value

    @field_validator("filters")
    @classmethod
    def _filter_whitelist(cls, value: dict[str, str]) -> dict[str, str]:
        # 过滤键（维度名）同样走维度白名单。
        invalid = [dim for dim in value if dim not in ALLOWED_DIMENSIONS]
        if invalid:
            raise ValueError("DIMENSION_NOT_ALLOWED")
        return value

    @model_validator(mode="after")
    def _metric_dimension_whitelist(self) -> QuerySpec:
        # 跨字段校验：group_by + filters 用到的维度，必须在"该指标允许的维度"集合里。
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
    """渲染 + 守卫的产物：SQL 文本、其 sha256、守卫状态，以及两枚溯源签名。

    renderer_signature / guard_signature 是文档之外的工程加固（HMAC 双盖章）：
    证明这条 SQL 确实由本进程渲染器生成、并由本进程守卫放行，使手工伪造的
    "guard_status=passed" Plan 无法通过 repository 执行（防 SQLGuard 旁路）。
    """

    sql: str
    sql_hash: str  # sha256(sql)
    guard_status: Literal["passed", "rejected"] = "rejected"  # 默认拒绝：安全失败
    guard_errors: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    renderer_signature: str | None = None
    guard_signature: str | None = None


class Evidence(StrictModel):
    """证据：每次取数的结构化快照（数值来源）。结论必须绑定当前 run 的 Evidence。"""

    evidence_id: str
    query_spec: QuerySpec
    sql: str
    sql_hash: str
    guard_status: str
    result_summary: dict[str, Any]  # 结构化结果摘要，供数值溯源
    data_source: str
    created_at: datetime


class Observation(StrictModel):
    """ReAct 观察：工具执行结果（成功/失败 + 错误码 + 关联证据 id）。"""

    action_name: str
    ok: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    message: str | None = None


class RootCauseCandidate(StrictModel):
    """根因候选：贡献占比 / 信号强度 / 证据支持度，及工程置信度（非统计置信度）。"""

    root_cause_type: str
    dimension: str | None = None
    element: str | None = None  # 维度值，如 paid_ads / electronics
    contribution_pct: float
    signal_severity: float
    evidence_support: float
    reflection_factor: float = 1.0
    eng_confidence: float  # engineering confidence，明确不是统计置信度
    verdict: str
    evidence_ids: list[str] = Field(default_factory=list)


class AgentAction(StrictModel):
    """ReAct 动作（受控）：action 必须 ∈ 白名单；rationale 仅记录，不作为事实。"""

    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None


class ReflectionIssue(StrictModel):
    """Reflection 校验发现的单条问题；error 级且带 suggested_action 时触发一次修复。"""

    check: str
    severity: Literal["error", "warning"]
    by: Literal["rule", "llm"]
    message: str
    suggested_action: AgentAction | None = None


class ReflectionResult(StrictModel):
    """Reflection 校验结果汇总（是否通过 / 问题列表 / 是否修复 / 修复次数）。"""

    passed: bool
    issues: list[ReflectionIssue] = Field(default_factory=list)
    repaired: bool = False
    repair_count: int = 0


class MemoryRecord(StrictModel):
    """记忆记录：带 confidence/source/version/ttl_days 的污染控制字段。

    记忆只能影响下钻优先级，绝不能直接成为最终结论。
    """

    memory_id: str
    layer: Literal["case", "semantic", "episodic", "reflection"]
    key: str  # 精确检索键，如 "gmv|channel"
    payload: dict[str, Any]
    confidence: float = 0.5
    source: str = "system"
    version: int = 1
    ttl_days: int | None = None
    created_at: datetime


class EvalCase(StrictModel):
    """eval 用例：通过评估真因表校验一次 AgentRun。"""

    case_id: str
    question: str
    expected_metric: str
    expected_anomaly: bool
    expected_root_cause: str | None = None  # ground truth 主因
    expected_dimension: str | None = None
    expected_element: str | None = None


class TraceStep(StrictModel):
    """可观测性：一次运行中每个节点/步骤的 span（输入/输出摘要、错误码、时延）。"""

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
    """一次 RCA 运行的根记录（状态机：running → succeeded/no_anomaly/failed）。"""

    run_id: str
    question: str
    status: Literal["running", "succeeded", "no_anomaly", "failed"] = "running"
    metric_id: str | None = None
    target_date: date
    error_code: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

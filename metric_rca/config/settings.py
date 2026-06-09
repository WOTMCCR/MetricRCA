"""集中式类型化配置（pydantic-settings）。

为什么需要它：设计文档要求阈值 / 运行上限不能是散落在代码里的魔法数，且明确禁止
"默认 provider / config 替换"。本模块把所有口径收敛到一个可校验、可被环境变量覆盖的
`Settings` 对象，并在构造期把若干 Zero-Fallback 红线前移到配置层。

对应 docs/COMPLIANCE_MATRIX.md 第 26 行；docs/MetricRCA.md §4(config)/§5(limits)/§12(thresholds)。
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_prefix：所有配置走 METRIC_RCA_* 环境变量；extra="forbid" 拒绝未知配置项，
    # 防止拼错的环境变量被静默忽略。
    model_config = SettingsConfigDict(env_prefix="METRIC_RCA_", extra="forbid")

    # 两个 DSN 均必填（min_length=1、无默认值）：连不上数据库时必须显式失败，
    # 不允许"静默退化到默认 / 内存库"。db_dsn=应用账号(写系统表)，readonly_db_dsn=只读账号(查业务)。
    db_dsn: str = Field(min_length=1)
    readonly_db_dsn: str = Field(min_length=1)

    # 固定业务口径（与 seed / 异常注入一致）。
    tz: str = "Asia/Tokyo"
    business_today: date = date(2026, 6, 6)
    target_date: date = date(2026, 6, 5)

    # 异常检测阈值（Phase 2 使用）：|delta_pct|>=thresh_pct 且 |z|>=z_thresh 才算异常。
    thresh_pct: float = 0.15
    z_thresh: float = 2.0

    # 业务终止上限（业务安全机制，不依赖 LangGraph 的 recursion_limit）。
    max_steps: int = 8
    max_query: int = 12
    max_drilldown_depth: int = 2
    max_repair: int = 1

    # 单条 SQL 的执行超时（毫秒），repo 执行前 SET SESSION 生效，防慢查。
    statement_timeout_ms: int = 3000

    # LLM / Memory 的启用与"是否必需"开关。问题解析必须真实调用 OpenAI IntentPlanner；
    # 缺少 provider / API key 时直接 fail-fast，不允许 mock 或 fallback parser。
    llm_enabled: bool = True
    llm_required: bool = False
    llm_provider: str | None = "openai"
    llm_model: str = "gpt-5.4-nano"
    llm_api_key: str | None = None
    memory_enabled: bool = True
    memory_required: bool = False
    signal_metric_by_type: dict[str, str] = Field(
        default_factory=lambda: {
            "campaign": "gmv",
            "inventory": "stockout_rate",
            "conversion": "pay_cvr",
            "refund_quality": "complaint_rate",
        }
    )
    root_cause_type_by_metric: dict[str, str] = Field(
        default_factory=lambda: {
            "refund_rate": "complaint_or_quality_issue",
            "pay_cvr": "conversion_drop",
        }
    )
    root_cause_type_by_dimension: dict[str, str] = Field(
        default_factory=lambda: {
            "channel": "campaign_traffic_drop",
            "category": "stockout",
            "device": "conversion_drop",
        }
    )
    root_cause_type_by_dimension_element: dict[str, str] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _required_provider_available(self) -> Settings:
        if self.llm_api_key is None:
            self.llm_api_key = os.getenv("OPENAI_API_KEY")
        # Zero-Fallback 前移：如果该运行"要求 LLM"但没有配置 provider，
        # 在配置构造期就抛 typed error，而不是等运行到一半才发现再静默兜底。
        if self.llm_required and (not self.llm_provider or not self.llm_api_key):
            raise ValueError("LLM_REQUIRED_UNAVAILABLE")
        return self


@lru_cache
def get_settings() -> Settings:
    # 进程内单例：保证全局口径一致，避免重复读环境变量产生分叉配置。
    return Settings()

from __future__ import annotations

from datetime import date
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="METRIC_RCA_", extra="forbid")

    db_dsn: str = Field(min_length=1)
    readonly_db_dsn: str = Field(min_length=1)
    tz: str = "Asia/Tokyo"
    business_today: date = date(2026, 6, 6)
    target_date: date = date(2026, 6, 5)
    thresh_pct: float = 0.15
    z_thresh: float = 2.0
    max_steps: int = 8
    max_query: int = 12
    max_drilldown_depth: int = 2
    max_repair: int = 1
    statement_timeout_ms: int = 3000
    llm_enabled: bool = False
    llm_required: bool = False
    llm_provider: str | None = None
    memory_enabled: bool = True
    memory_required: bool = False

    @model_validator(mode="after")
    def _required_provider_available(self) -> Settings:
        if self.llm_required and not self.llm_provider:
            raise ValueError("LLM_REQUIRED_UNAVAILABLE")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

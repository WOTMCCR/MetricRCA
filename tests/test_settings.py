from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from metric_rca.config.settings import Settings


def test_settings_defaults_and_required_dsn_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METRIC_RCA_DB_DSN", raising=False)
    monkeypatch.delenv("METRIC_RCA_READONLY_DB_DSN", raising=False)
    monkeypatch.delenv("METRIC_RCA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings()

    settings = Settings(
        db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
        readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
    )
    assert settings.tz == "Asia/Tokyo"
    assert settings.business_today == date(2026, 6, 6)
    assert settings.target_date == date(2026, 6, 5)
    assert settings.thresh_pct == 0.15
    assert settings.z_thresh == 2.0
    assert settings.max_steps == 8
    assert settings.max_query == 12
    assert settings.max_drilldown_depth == 2
    assert settings.max_repair == 1
    assert settings.memory_enabled is True
    assert settings.memory_required is False
    assert settings.llm_enabled is True
    assert settings.llm_required is False
    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-5.4-nano"
    assert settings.llm_api_key is None

    with pytest.raises(ValidationError, match="LLM_REQUIRED_UNAVAILABLE"):
        Settings(
            db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
            readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
            llm_required=True,
        )

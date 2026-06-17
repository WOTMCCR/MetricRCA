from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from metric_rca.config.settings import Settings


def test_settings_defaults_and_required_dsn_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METRIC_RCA_DB_DSN", raising=False)
    monkeypatch.delenv("METRIC_RCA_READONLY_DB_DSN", raising=False)
    monkeypatch.delenv("METRIC_RCA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("METRIC_RCA_LLM_MODEL", raising=False)
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
    assert settings.max_query == 20
    assert settings.max_drilldown_depth == 3
    assert settings.max_repair == 1
    assert settings.memory_enabled is True
    assert settings.memory_required is False
    assert settings.memory_write_on_finalize is True
    assert settings.eval_concurrency == 1
    assert settings.eval_llm_max_attempts == 3
    assert settings.llm_enabled is True
    assert settings.llm_required is True
    assert settings.llm_provider is None
    assert settings.llm_model is None
    assert settings.llm_api_key is None
    assert settings.llm_base_url is None
    assert settings.llm_structured_output_method == "json_schema"
    assert settings.llm_temperature is None
    assert settings.agent_tracing_enabled is False
    assert settings.agent_trace_group_id is None
    assert settings.multi_agent_enabled is False


def test_eval_llm_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
            readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
            eval_llm_max_attempts=0,
        )


def test_memory_required_cannot_disable_memory() -> None:
    with pytest.raises(ValidationError, match="CONFIG_INVALID"):
        Settings(
            db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
            readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
            memory_enabled=False,
            memory_required=True,
        )


def test_signal_metric_mapping_must_be_complete_and_metric_whitelisted() -> None:
    base = {
        "campaign": "gmv",
        "inventory": "stockout_rate",
        "conversion": "pay_cvr",
        "refund_quality": "complaint_rate",
    }
    settings = Settings(
        db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
        readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
        llm_model="gpt-test",
        llm_api_key="key",
        signal_metric_by_type=base,
    )
    assert settings.signal_metric_by_type == base

    missing = {key: value for key, value in base.items() if key != "conversion"}
    with pytest.raises(ValidationError, match="CONFIG_INVALID"):
        Settings(
            db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
            readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
            llm_model="gpt-test",
            llm_api_key="key",
            signal_metric_by_type=missing,
        )

    invalid = {**base, "conversion": "not_a_metric"}
    with pytest.raises(ValidationError, match="CONFIG_INVALID"):
        Settings(
            db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
            readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
            llm_model="gpt-test",
            llm_api_key="key",
            signal_metric_by_type=invalid,
        )


def test_openai_compatible_provider_does_not_substitute_openai_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.delenv("METRIC_RCA_LLM_API_KEY", raising=False)

    settings = Settings(
        db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
        readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
        llm_provider="openai-compatible",
        llm_model="deepseek-chat",
        llm_base_url="https://api.deepseek.com",
    )

    assert settings.llm_api_key is None

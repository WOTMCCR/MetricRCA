from __future__ import annotations

from datetime import date
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from langchain_core.exceptions import LangChainException

from metric_rca.config.settings import Settings, get_settings
from metric_rca.data.seed_data import main as seed_main
from metric_rca.domain.models import MetricDefinition
from metric_rca.repositories.metadata_repository import MetadataRepository
from metric_rca.services.intent_planner import LLMIntentPlanner, IntentPlanner, build_system_prompt
from metric_rca.services.metric_service import MetricService, MetricServiceError, ParsedIntent


ROOT = Path(__file__).resolve().parents[1]


def _metric(metric_id: str, *, dimensions: list[str] | None = None) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        display_name=f"Test {metric_id}",
        formula="test formula",
        higher_is_better=True,
        allowed_dimensions=dimensions or ["channel", "category"],
        source_table="fact_order",
    )


class FakeMetadataRepository:
    def __init__(self, metrics: list[MetricDefinition]) -> None:
        self.metrics = {metric.metric_id: metric for metric in metrics}
        self.dimension_requests: list[str] = []

    def get_metric_definition(self, metric_id: str) -> MetricDefinition:
        metric = self.metrics.get(metric_id)
        if metric is None:
            raise MetricServiceError("METRIC_NOT_FOUND", f"metric not found: {metric_id}")
        return metric

    def get_schema_context(self, metric_id: str) -> dict[str, object]:
        metric = self.get_metric_definition(metric_id)
        return {
            "source_table": metric.source_table,
            "allowed_dimensions": metric.allowed_dimensions,
            "formula": metric.formula,
        }

    def list_metrics(self) -> list[MetricDefinition]:
        return list(self.metrics.values())

    def list_dimension_values(self, dimension: str) -> list[str]:
        self.dimension_requests.append(dimension)
        return {"channel": ["paid_ads"], "category": ["electronics"], "device": ["mobile"]}.get(dimension, [])


class StaticIntentPlanner:
    def __init__(self, parsed: ParsedIntent) -> None:
        self.parsed = parsed

    def parse(self, *args, **kwargs) -> ParsedIntent:
        return self.parsed


def _live_settings() -> Settings:
    api_key = os.getenv("METRIC_RCA_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "test-key":
        pytest.skip("real OpenAI credentials are not configured for live intent parsing")
    return Settings(
        db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
        readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
        llm_enabled=True,
        llm_required=False,
        llm_provider="openai",
        llm_model="gpt-5.4-nano",
        llm_api_key=api_key,
    )


def _settings_without_llm_key() -> Settings:
    return Settings(
        db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
        readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
        llm_enabled=True,
        llm_required=False,
        llm_provider="openai",
        llm_model="gpt-5.4-nano",
        llm_api_key=None,
    )


def test_get_metric_definition_reads_from_metadata_repo_not_dict() -> None:
    custom = _metric("custom_metric", dimensions=["warehouse"])
    service = MetricService(
        FakeMetadataRepository([custom]),
        settings=_live_settings(),
    )

    assert service.get_metric_definition("custom_metric") == custom


def test_metadata_methods_do_not_require_openai_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("METRIC_RCA_LLM_API_KEY", raising=False)
    custom = _metric("custom_metric", dimensions=["warehouse"])
    service = MetricService(
        FakeMetadataRepository([custom]),
        settings=_settings_without_llm_key(),
    )

    assert service.get_metric_definition("custom_metric") == custom
    assert service.get_schema_context("custom_metric")["allowed_dimensions"] == ["warehouse"]

    with pytest.raises(MetricServiceError) as exc_info:
        service.parse_question("Why did yesterday GMV drop?", business_today=date(2026, 6, 6))
    assert exc_info.value.code == "LLM_REQUIRED_UNAVAILABLE"


def test_metric_service_runtime_reads_mutated_persisted_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("METRIC_RCA_LLM_API_KEY", raising=False)
    seed_main()
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    metric_id = "temporary_runtime_metric"
    try:
        repo = MetadataRepository(engine)
        service = MetricService(repo, settings=_settings_without_llm_key())
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO metric_definition (
                      metric_id, display_name, formula, numerator_sql_fragment,
                      denominator_sql_fragment, higher_is_better, source_table,
                      allowed_dimensions
                    )
                    VALUES (
                      :metric_id, 'Temporary Runtime Metric', 'sum(test)', NULL,
                      NULL, 1, 'fact_order', '["channel"]'
                    )
                    """
                ),
                {"metric_id": metric_id},
            )

        assert service.get_metric_definition(metric_id).allowed_dimensions == ["channel"]
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE metric_definition
                    SET allowed_dimensions = '["category"]'
                    WHERE metric_id = :metric_id
                    """
                ),
                {"metric_id": metric_id},
            )
        assert service.get_metric_definition(metric_id).allowed_dimensions == ["category"]

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM metric_definition WHERE metric_id = :metric_id"), {"metric_id": metric_id})
        with pytest.raises(MetricServiceError) as exc_info:
            service.get_metric_definition(metric_id)
        assert exc_info.value.code == "METRIC_NOT_FOUND"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM metric_definition WHERE metric_id = :metric_id"), {"metric_id": metric_id})
        engine.dispose()


def test_drop_metric_from_metadata_repo_raises_metric_not_found() -> None:
    seed_main()
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    repo = MetadataRepository(engine)
    metric_id = "temporary_drop_metric"
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO metric_definition (
                      metric_id, display_name, formula, numerator_sql_fragment,
                      denominator_sql_fragment, higher_is_better, source_table,
                      allowed_dimensions
                    )
                    VALUES (
                      :metric_id, 'Temporary Drop Metric', 'sum(test)', NULL,
                      NULL, 1, 'fact_order', '["channel"]'
                    )
                    """
                ),
                {"metric_id": metric_id},
            )
        assert repo.get_metric_definition(metric_id).metric_id == metric_id

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM metric_definition WHERE metric_id = :metric_id"), {"metric_id": metric_id})

        with pytest.raises(MetricServiceError) as exc_info:
            repo.get_metric_definition(metric_id)
        assert exc_info.value.code == "METRIC_NOT_FOUND"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM metric_definition WHERE metric_id = :metric_id"), {"metric_id": metric_id})
        engine.dispose()


def test_metadata_repo_get_metric_definition_returns_typed_model() -> None:
    seed_main()
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        repo = MetadataRepository(engine)
        definition = repo.get_metric_definition("gmv")
        assert isinstance(definition, MetricDefinition)
        assert definition.metric_id == "gmv"
        assert definition.source_table == "fact_order"
        assert "channel" in definition.allowed_dimensions
    finally:
        engine.dispose()


def test_mutate_schema_context_in_metadata_repo_changes_runtime_output_and_error() -> None:
    seed_main()
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    repo = MetadataRepository(engine)
    metric_id = "temporary_schema_metric"
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO metric_definition (
                      metric_id, display_name, formula, numerator_sql_fragment,
                      denominator_sql_fragment, higher_is_better, source_table,
                      allowed_dimensions
                    )
                    VALUES (
                      :metric_id, 'Temporary Schema Metric', 'sum(test)', NULL,
                      NULL, 1, 'fact_order', '["channel"]'
                    )
                    """
                ),
                {"metric_id": metric_id},
            )
        assert repo.get_schema_context(metric_id)["allowed_dimensions"] == ["channel"]

        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE metric_definition
                    SET allowed_dimensions = '["category"]'
                    WHERE metric_id = :metric_id
                    """
                ),
                {"metric_id": metric_id},
            )
        assert repo.get_schema_context(metric_id)["allowed_dimensions"] == ["category"]

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM metric_definition WHERE metric_id = :metric_id"), {"metric_id": metric_id})
        with pytest.raises(MetricServiceError) as exc_info:
            repo.get_schema_context(metric_id)
        assert exc_info.value.code == "SCHEMA_CONTEXT_MISSING"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM metric_definition WHERE metric_id = :metric_id"), {"metric_id": metric_id})
        engine.dispose()


def test_intent_planner_uses_metadata_context_for_live_parse() -> None:
    repo = FakeMetadataRepository(
        [
            _metric("gmv", dimensions=["channel", "category"]),
            _metric("pay_cvr", dimensions=["channel", "device"]),
        ]
    )
    service = MetricService(repo, settings=_live_settings())

    parsed = service.parse_question("Why did yesterday paid ads channel GMV drop?", business_today=date(2026, 6, 6))

    assert parsed.metric_id == "gmv"
    assert parsed.dimension == "channel"
    assert parsed.element == "paid_ads"
    assert service.supported_metrics == ["gmv", "pay_cvr"]
    assert service.supported_dimensions == ["category", "channel", "device"]
    assert service.dimension_values["channel"] == ["paid_ads"]
    assert repo.dimension_requests == ["category", "channel", "device"]


def test_parse_question_uses_metadata_driven_metric_list() -> None:
    service = MetricService(
        FakeMetadataRepository([_metric("gmv"), _metric("pay_cvr")]),
        settings=_live_settings(),
    )

    with pytest.raises(MetricServiceError) as exc_info:
        service.parse_question("Why did yesterday refund rate increase?", business_today=date(2026, 6, 6))
    assert exc_info.value.code == "METRIC_NOT_FOUND"


def test_parse_question_rejects_element_not_present_in_metadata_dimension_values() -> None:
    class ValueMetadataRepository(FakeMetadataRepository):
        def list_dimension_values(self, dimension: str) -> list[str]:
            self.dimension_requests.append(dimension)
            return {"channel": ["known_channel"], "category": ["known_category"]}.get(dimension, [])

    service = MetricService(
        ValueMetadataRepository([_metric("gmv", dimensions=["channel", "category"])]),
        settings=_live_settings(),
    )

    with pytest.raises(MetricServiceError) as exc_info:
        service.parse_question("Why did yesterday channel=unknown_channel GMV drop?", business_today=date(2026, 6, 6))
    assert exc_info.value.code == "DIMENSION_NOT_ALLOWED"


def test_parse_question_accepts_llm_resolved_past_business_date() -> None:
    service = MetricService(
        FakeMetadataRepository([_metric("gmv")]),
        settings=_settings_without_llm_key(),
    )
    service._intent_planner = StaticIntentPlanner(
        ParsedIntent(
            metric_id="gmv",
            target_date=date(2026, 6, 3),
            question_family="gmv_drop",
            analysis_strategy="standard",
        )
    )

    parsed = service.parse_question("Was GMV abnormal two days ago?", business_today=date(2026, 6, 6))

    assert parsed.target_date == date(2026, 6, 3)


def test_parse_question_rejects_llm_resolved_current_or_future_business_date() -> None:
    service = MetricService(
        FakeMetadataRepository([_metric("gmv")]),
        settings=_settings_without_llm_key(),
    )
    service._intent_planner = StaticIntentPlanner(
        ParsedIntent(
            metric_id="gmv",
            target_date=date(2026, 6, 6),
            question_family="gmv_drop",
            analysis_strategy="standard",
        )
    )

    with pytest.raises(MetricServiceError) as exc_info:
        service.parse_question("Why did GMV change today?", business_today=date(2026, 6, 6))

    assert exc_info.value.code == "DATE_RANGE_INVALID"


def test_no_keyword_parsing_in_metric_service() -> None:
    source = (ROOT / "metric_rca" / "services" / "metric_service.py").read_text()
    forbidden = ['in text', 'if "gmv"', 'if "refund"', "_dimension_from_text", "_element_from_text"]
    offenders = [token for token in forbidden if token in source]
    assert offenders == []


def test_intent_planner_system_prompt_has_no_hardcoded_metrics() -> None:
    source = (ROOT / "metric_rca" / "services" / "intent_planner.py").read_text()
    metric_literals = ["gmv", "net_gmv", "pay_cvr", "refund_rate", "uv", "aov", "stockout_rate", "complaint_rate"]
    offenders = [metric for metric in metric_literals if f'"{metric}"' in source or f"'{metric}'" in source]
    assert offenders == []


def test_intent_planner_protocol_documents_live_planner_boundary() -> None:
    assert hasattr(IntentPlanner, "parse")


def test_intent_planner_prompt_for_json_mode_disallows_intent_wrapper() -> None:
    prompt = build_system_prompt(
        business_today=date(2026, 6, 6),
        supported_metrics=["net_gmv"],
        supported_dimensions=["product"],
        supported_dimension_values={"product": ["1"]},
        supported_families=["net_gmv_drop"],
    )

    assert "Do not wrap the fields in an intent object." in prompt
    assert "error_code, metric_id, target_date, question_family, analysis_strategy" in prompt


def test_intent_planner_prompt_includes_net_gmv_slice_examples() -> None:
    prompt = build_system_prompt(
        business_today=date(2026, 6, 6),
        supported_metrics=["net_gmv"],
        supported_dimensions=["channel", "product"],
        supported_dimension_values={"channel": ["paid_ads"], "product": ["1"]},
        supported_families=["net_gmv_drop"],
    )

    assert "net GMV" in prompt
    assert "metric_id=net_gmv" in prompt
    assert "paid ads -> paid_ads" in prompt
    assert "question_family=net_gmv_drop" in prompt


def test_intent_planner_prompt_includes_phase_b_alias_date_and_ambiguity_guidance() -> None:
    prompt = build_system_prompt(
        business_today=date(2026, 6, 6),
        supported_metrics=["gmv", "net_gmv", "uv"],
        supported_dimensions=["channel"],
        supported_dimension_values={"channel": ["paid_ads"]},
        supported_families=["gmv_drop", "net_gmv_drop", "uv_drop"],
    )

    assert "traffic" in prompt
    assert "metric_id=uv" in prompt
    assert "question_family=uv_drop" in prompt
    assert "sales" in prompt
    assert "metric_id=gmv" in prompt
    assert "two days ago" in prompt
    assert "on the Nth" in prompt
    assert "since the weekend" in prompt
    assert "seems off" in prompt


def test_llm_intent_planner_maps_langchain_invocation_error_to_typed_error() -> None:
    class BrokenStructuredModel:
        def invoke(self, messages):
            raise LangChainException("transport wrapper failed")

    planner = LLMIntentPlanner(provider="openai", model="gpt-5.4-nano", api_key="test-key")
    planner._structured_model = BrokenStructuredModel()

    with pytest.raises(MetricServiceError) as exc_info:
        planner.parse(
            "Why did yesterday GMV drop?",
            business_today=date(2026, 6, 6),
            supported_metrics=["gmv"],
            supported_dimensions=["channel"],
            supported_dimension_values={"channel": ["paid_ads"]},
            supported_families=["gmv_drop"],
        )

    assert exc_info.value.code == "LLM_REQUIRED_UNAVAILABLE"


def test_openai_compatible_intent_planner_uses_configured_base_url_and_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeStructuredModel:
        def invoke(self, messages):
            captured["messages"] = messages
            return {
                "error_code": None,
                "metric_id": "net_gmv",
                "target_date": "2026-06-05",
                "question_family": "net_gmv_drop",
                "analysis_strategy": "standard",
                "dimension": "product",
                "element": "1",
                "filters": [{"dimension": "product", "value": "1"}],
            }

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured["chat_kwargs"] = kwargs

        def with_structured_output(self, schema, *, method: str):
            captured["schema"] = schema
            captured["method"] = method
            return FakeStructuredModel()

    monkeypatch.setattr("metric_rca.services.llm_client.ChatOpenAI", FakeChatOpenAI)

    planner = LLMIntentPlanner(
        provider="openai-compatible",
        model="deepseek-chat",
        api_key="deepseek-key",
        base_url="https://api.deepseek.com",
        structured_output_method="json_mode",
    )
    parsed = planner.parse(
        "Why did net GMV fall for product 1 yesterday?",
        business_today=date(2026, 6, 6),
        supported_metrics=["net_gmv"],
        supported_dimensions=["product"],
        supported_dimension_values={"product": ["1"]},
        supported_families=["net_gmv_drop"],
    )

    assert captured["chat_kwargs"]["model"] == "deepseek-chat"
    assert captured["chat_kwargs"]["api_key"] == "deepseek-key"
    assert captured["chat_kwargs"]["base_url"] == "https://api.deepseek.com"
    assert captured["chat_kwargs"]["max_completion_tokens"] == 3000
    assert captured["method"] == "json_mode"
    assert parsed.metric_id == "net_gmv"
    assert parsed.analysis_strategy == "standard"
    assert parsed.filters == {"product": "1"}


def test_llm_intent_planner_retries_parse_failed_with_same_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {"calls": 0}

    class FakeStructuredModel:
        def invoke(self, messages):
            captured["calls"] = int(captured["calls"]) + 1
            captured["messages"] = messages
            if captured["calls"] == 1:
                return {
                    "error_code": "PARSE_FAILED",
                    "metric_id": None,
                    "target_date": None,
                    "question_family": None,
                    "analysis_strategy": "standard",
                    "dimension": None,
                    "element": None,
                    "filters": [],
                }
            return {
                "error_code": None,
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "question_family": "gmv_drop",
                "analysis_strategy": "channel_first",
                "dimension": None,
                "element": None,
                "filters": [],
            }

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured["chat_kwargs"] = kwargs

        def with_structured_output(self, schema, *, method: str):
            captured["schema"] = schema
            captured["method"] = method
            return FakeStructuredModel()

    monkeypatch.setattr("metric_rca.services.llm_client.ChatOpenAI", FakeChatOpenAI)

    planner = LLMIntentPlanner(provider="openai", model="gpt-5-nano", api_key="test-key")
    parsed = planner.parse(
        "Was yesterday's GMV actually abnormal?",
        business_today=date(2026, 6, 6),
        supported_metrics=["gmv"],
        supported_dimensions=["channel"],
        supported_dimension_values={"channel": ["paid_ads"]},
        supported_families=["gmv_drop"],
    )

    assert captured["calls"] == 2
    assert captured["schema"].__name__ == "_LLMIntentOutput"
    assert "Previous parser attempt returned PARSE_FAILED" in captured["messages"][1][1]
    assert parsed.metric_id == "gmv"
    assert parsed.analysis_strategy == "channel_first"


def test_llm_intent_planner_accepts_organic_first_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeStructuredModel:
        def invoke(self, messages):
            captured["messages"] = messages
            return {
                "error_code": None,
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "question_family": "gmv_drop",
                "analysis_strategy": "organic_first",
                "dimension": None,
                "element": None,
                "filters": [],
            }

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            pass

        def with_structured_output(self, schema, *, method: str):
            return FakeStructuredModel()

    monkeypatch.setattr("metric_rca.services.llm_client.ChatOpenAI", FakeChatOpenAI)

    planner = LLMIntentPlanner(provider="openai", model="gpt-5-nano", api_key="test-key")
    parsed = planner.parse(
        "Why did yesterday's GMV fall despite stable merchandising?",
        business_today=date(2026, 6, 6),
        supported_metrics=["gmv"],
        supported_dimensions=["channel", "category", "product"],
        supported_dimension_values={"channel": ["paid_ads", "organic"]},
        supported_families=["gmv_drop"],
    )

    system_prompt = captured["messages"][0][1]
    assert "organic_first" in system_prompt
    assert parsed.metric_id == "gmv"
    assert parsed.analysis_strategy == "organic_first"
    assert parsed.dimension is None
    assert parsed.element is None
    assert parsed.filters == {}


def test_llm_intent_planner_does_not_retry_typed_semantic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {"calls": 0}

    class FakeStructuredModel:
        def invoke(self, messages):
            captured["calls"] = int(captured["calls"]) + 1
            return {
                "error_code": "METRIC_NOT_FOUND",
                "metric_id": None,
                "target_date": None,
                "question_family": None,
                "analysis_strategy": "standard",
                "dimension": None,
                "element": None,
                "filters": [],
            }

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            pass

        def with_structured_output(self, schema, *, method: str):
            return FakeStructuredModel()

    monkeypatch.setattr("metric_rca.services.llm_client.ChatOpenAI", FakeChatOpenAI)

    planner = LLMIntentPlanner(provider="openai", model="gpt-5-nano", api_key="test-key")

    with pytest.raises(MetricServiceError) as exc_info:
        planner.parse(
            "Why did an unsupported KPI fall yesterday?",
            business_today=date(2026, 6, 6),
            supported_metrics=["gmv"],
            supported_dimensions=["channel"],
            supported_dimension_values={"channel": ["paid_ads"]},
            supported_families=["gmv_drop"],
        )

    assert captured["calls"] == 1
    assert exc_info.value.code == "METRIC_NOT_FOUND"


def test_openai_compatible_intent_planner_requires_base_url() -> None:
    with pytest.raises(MetricServiceError) as exc_info:
        LLMIntentPlanner(
            provider="openai-compatible",
            model="deepseek-chat",
            api_key="deepseek-key",
        )

    assert exc_info.value.code == "LLM_BASE_URL_REQUIRED"

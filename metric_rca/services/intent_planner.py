"""Intent planner implementations for OpenAI-backed question parsing."""

from __future__ import annotations

import json
from datetime import date
from typing import Literal, Protocol

from langchain_core.exceptions import LangChainException, OutputParserException
from openai import OpenAIError
from pydantic import Field, ValidationError

from metric_rca.domain.models import StrictModel
from metric_rca.services.llm_client import LLMClientConfigError, build_openai_compatible_chat_model
from metric_rca.services.metric_contracts import (
    AnalysisStrategy,
    MetricServiceError,
    ParsedIntent,
    QuestionFamily,
    metric_id_from_question_family,
)

LLM_INTENT_PARSE_MAX_ATTEMPTS = 3


SYSTEM_PROMPT_TEMPLATE = """You are an intent parser for a metric anomaly diagnosis system.
Your job is to extract structured intent from user questions.

SUPPORTED QUESTION FAMILIES:
{families_list}

SUPPORTED METRICS:
{metrics_list}

SUPPORTED DIMENSIONS:
{dimensions_list}

SUPPORTED DIMENSION VALUES:
{dimension_values}

BUSINESS TODAY:
{business_today}

RULES:
- Output MUST be valid JSON matching the supplied schema.
- metric_id MUST be one of the supported metrics.
- If the question contains explicit "metric_id=<value>" text, metric_id MUST be exactly that value.
- question_family MUST be one of the supported families.
- analysis_strategy MUST be one of standard, channel_first, product_first, or organic_first.
- Use analysis_strategy=standard for explicit dimension=value slices and ordinary
  single-slice diagnosis.
- Use analysis_strategy=channel_first when an unscoped broad store/overall target
  KPI question should first verify channel/campaign movement.
- Use analysis_strategy=organic_first when an unscoped target KPI question should
  first verify organic channel campaign movement because merchandising is stated
  as stable or not the likely driver.
- Use analysis_strategy=product_first when an unscoped target KPI question should
  first verify product/inventory movement, including merchandise sales, price, or
  average-order-value wording.
- dimension MUST be one of the supported dimensions or null.
- element and filter values MUST come from the supported dimension values when values are listed.
- If the question contains explicit "dimension=value" text, treat it as a required filter.
  Do not ignore explicit dimension values.
- target_date must be business_today minus one day.
- If the question does not match any family, set error_code to PARSE_FAILED and intent to null.
- If the question mentions an unsupported metric, set error_code to METRIC_NOT_FOUND and intent to null.
- If the question mentions an unsupported dimension, set error_code to DIMENSION_NOT_ALLOWED and intent to null.
- If the question mentions a date range other than yesterday, set error_code to DATE_RANGE_INVALID and intent to null.
- For supported questions, set error_code to null and fill all intent fields.
- filters MUST be an array of objects with dimension and value keys.
- Output one top-level JSON object with exactly these keys:
  error_code, metric_id, target_date, question_family, analysis_strategy,
  dimension, element, filters.
- Do not wrap the fields in an intent object.
- For unsupported questions, set error_code and set metric_id, target_date,
  question_family, dimension, and element to null, analysis_strategy to standard,
  with filters as an empty array.
- Do not judge facts, write SQL, choose root causes, or infer business evidence.
- Parse the target metric as the KPI the user asks to explain. Words such as
  stockout, refund, UV, AOV, logistics, campaign, and quality describe possible
  cause mechanisms or related signals unless the user explicitly asks for that
  metric rate itself.
- Treat "conversion rate" and "CVR" as the pay conversion KPI, metric_id=pay_cvr.
- Treat "net GMV", "net revenue", and "GMV after refunds" as the net GMV KPI,
  metric_id=net_gmv, when net_gmv is in the supported metrics.
- Treat "stockout rate" as metric_id=stockout_rate, not as a GMV cause.
- Natural language dimension values may use spaces instead of underscores; map
  the phrase to the supported value exactly, for example paid ads -> paid_ads
  when paid_ads is listed under channel.
- Treat business paraphrases such as "fell", "fall", "decline", "below
  expectation", "normal seasonal range", "merchandise sales", "across the
  store", and "despite stable merchandising" as supported drop/anomaly
  questions when they mention a
  supported KPI such as GMV. For example, "Why did yesterday's GMV decline in
  merchandise sales?" is metric_id=gmv, question_family=gmv_drop,
  analysis_strategy=product_first, with no explicit dimension/filter.
  "Why was yesterday's GMV below expectation across the store?" and "Was
  yesterday's GMV meaningfully below its normal seasonal range?" are
  metric_id=gmv, question_family=gmv_drop, analysis_strategy=channel_first,
  with no explicit dimension/filter. "Why did yesterday's GMV fall despite
  stable merchandising?" is also metric_id=gmv, question_family=gmv_drop,
  analysis_strategy=organic_first, with no explicit dimension/filter.
  "Why did net GMV fall for product 1 yesterday?" is metric_id=net_gmv,
  question_family=net_gmv_drop, analysis_strategy=standard, dimension=product,
  element=1, and filters containing dimension=product and value=1. "Why did
  net GMV fall in paid ads yesterday?" is metric_id=net_gmv,
  question_family=net_gmv_drop, analysis_strategy=standard, dimension=channel,
  element=paid_ads, and filters containing dimension=channel and value=paid_ads.
  "Why did electronics GMV fall yesterday?" is metric_id=gmv,
  question_family=category_gmv_anomaly, analysis_strategy=standard,
  dimension=category, element=electronics, and filters containing
  dimension=category and value=electronics. "Why did mobile conversion rate
  fall yesterday?" is metric_id=pay_cvr, question_family=pay_cvr_drop,
  analysis_strategy=standard, dimension=device, element=mobile, and filters
  containing dimension=device and value=mobile. "Why did stockout rate rise in
  the Osaka warehouse yesterday?" is metric_id=stockout_rate,
  question_family=stockout_rate_increase, analysis_strategy=standard,
  dimension=warehouse, element=osaka, and filters containing
  dimension=warehouse and value=osaka. "Was yesterday's GMV actually abnormal?"
  is metric_id=gmv, question_family=gmv_drop, analysis_strategy=standard, with
  no explicit dimension/filter. "Was yesterday's conversion rate actually
  abnormal?" is metric_id=pay_cvr, question_family=pay_cvr_drop,
  analysis_strategy=standard, with no explicit dimension/filter.
- Do not infer a category/product element from broad words such as merchandise
  unless a supported dimension value is explicit in the user question.
- Do not treat "despite stable merchandising" as merchandise sales; it means
  merchandising was stable, so use analysis_strategy=organic_first.
"""


class IntentPlanner(Protocol):
    """Swappable intent parsing boundary."""

    def parse(
        self,
        question: str,
        *,
        business_today: date,
        supported_metrics: list[str],
        supported_dimensions: list[str],
        supported_dimension_values: dict[str, list[str]],
        supported_families: list[str],
    ) -> ParsedIntent:
        ...


class _LLMIntentFilter(StrictModel):
    dimension: str = Field(description="Dimension key from the supported dimensions.")
    value: str = Field(description="Dimension value from the supported dimension values.")


class _LLMIntentOutput(StrictModel):
    error_code: Literal[
        "PARSE_FAILED",
        "METRIC_NOT_FOUND",
        "DIMENSION_NOT_ALLOWED",
        "DATE_RANGE_INVALID",
    ] | None = Field(description="Typed error code for unsupported questions, otherwise null.")
    metric_id: str | None = Field(description="Metric id from the supported metrics, or null on error.")
    target_date: str | None = Field(description="Target business date in ISO format, or null on error.")
    question_family: QuestionFamily | None = Field(description="Question family from the supported families, or null on error.")
    analysis_strategy: AnalysisStrategy | None = Field(description="Structured discovery strategy for guard policy.")
    dimension: str | None = Field(description="Primary dimension from supported dimensions, or null.")
    element: str | None = Field(description="Primary dimension value from supported values, or null.")
    filters: list[_LLMIntentFilter] = Field(description="Dimension filters as strict key/value pairs.")


class LLMIntentPlanner:
    """LangChain OpenAI structured intent extraction with no fallback."""

    def __init__(
        self,
        *,
        provider: str | None,
        model: str,
        api_key: str | None,
        base_url: str | None = None,
        structured_output_method: Literal["json_schema", "json_mode", "function_calling"] = "json_schema",
    ) -> None:
        self._provider = provider
        self._model = model
        self._api_key = api_key
        try:
            chat_model = build_openai_compatible_chat_model(
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
                timeout=30,
                max_retries=0,
                max_completion_tokens=3000,
            )
        except LLMClientConfigError as exc:
            raise MetricServiceError(exc.code, exc.message) from exc
        self._structured_model = chat_model.with_structured_output(
            _LLMIntentOutput,
            method=structured_output_method,
        )

    def parse(
        self,
        question: str,
        *,
        business_today: date,
        supported_metrics: list[str],
        supported_dimensions: list[str],
        supported_dimension_values: dict[str, list[str]],
        supported_families: list[str],
    ) -> ParsedIntent:
        prompt = build_system_prompt(
            business_today=business_today,
            supported_metrics=supported_metrics,
            supported_dimensions=supported_dimensions,
            supported_dimension_values=supported_dimension_values,
            supported_families=supported_families,
        )
        last_parse_error: Exception | None = None
        for attempt in range(1, LLM_INTENT_PARSE_MAX_ATTEMPTS + 1):
            try:
                raw_payload = self._structured_model.invoke(
                    [("system", prompt), ("human", _human_prompt(question, attempt=attempt))]
                )
                parsed_payload = _coerce_intent_output(raw_payload)
            except OpenAIError as exc:
                raise MetricServiceError("LLM_REQUIRED_UNAVAILABLE", "LLM intent planner request failed") from exc
            except LangChainException as exc:
                if not isinstance(exc, OutputParserException):
                    raise MetricServiceError("LLM_REQUIRED_UNAVAILABLE", "LangChain intent planner request failed") from exc
                last_parse_error = exc
                if attempt < LLM_INTENT_PARSE_MAX_ATTEMPTS:
                    continue
                raise MetricServiceError("PARSE_FAILED", "LLM returned invalid intent payload") from exc
            except (MetricServiceError, ValidationError) as exc:
                last_parse_error = exc
                if attempt < LLM_INTENT_PARSE_MAX_ATTEMPTS:
                    continue
                raise MetricServiceError("PARSE_FAILED", "LLM returned invalid intent payload") from exc

            if parsed_payload.error_code == "PARSE_FAILED":
                last_parse_error = MetricServiceError("PARSE_FAILED", "intent parsing failed: PARSE_FAILED")
                if attempt < LLM_INTENT_PARSE_MAX_ATTEMPTS:
                    continue
                raise last_parse_error
            if parsed_payload.error_code is not None:
                raise MetricServiceError(parsed_payload.error_code, f"intent parsing failed: {parsed_payload.error_code}")
            try:
                return _intent_from_payload(parsed_payload)
            except ValidationError as exc:
                last_parse_error = exc
                if attempt < LLM_INTENT_PARSE_MAX_ATTEMPTS:
                    continue
                raise MetricServiceError("PARSE_FAILED", "LLM intent payload failed schema validation") from exc

        raise MetricServiceError("PARSE_FAILED", "LLM returned invalid intent payload") from last_parse_error


def _coerce_intent_output(payload: object) -> _LLMIntentOutput:
    if isinstance(payload, _LLMIntentOutput):
        return payload
    if isinstance(payload, dict):
        return _LLMIntentOutput.model_validate(payload)
    raise MetricServiceError("PARSE_FAILED", "LLM returned invalid intent payload type")


def _intent_from_payload(parsed_payload: _LLMIntentOutput) -> ParsedIntent:
    filters = {item.dimension: item.value for item in parsed_payload.filters}
    metric_id = parsed_payload.metric_id
    if metric_id is None and parsed_payload.question_family is not None:
        metric_id = metric_id_from_question_family(parsed_payload.question_family)
    intent_payload = {
        "metric_id": metric_id,
        "target_date": parsed_payload.target_date,
        "question_family": parsed_payload.question_family,
        "analysis_strategy": parsed_payload.analysis_strategy,
        "dimension": parsed_payload.dimension,
        "element": parsed_payload.element,
        "filters": filters,
    }
    return ParsedIntent.model_validate(intent_payload)


def _human_prompt(question: str, *, attempt: int) -> str:
    if attempt == 1:
        return question
    return (
        f"{question}\n\n"
        "Previous parser attempt returned PARSE_FAILED or an invalid schema for this same question. "
        "Re-evaluate against the supported metrics, dimensions, values, and examples. "
        "If it is supported, return a valid structured intent; if it is truly unsupported, return the typed error."
    )


def build_system_prompt(
    *,
    business_today: date,
    supported_metrics: list[str],
    supported_dimensions: list[str],
    supported_dimension_values: dict[str, list[str]],
    supported_families: list[str],
) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        business_today=business_today.isoformat(),
        metrics_list=_json_list(supported_metrics),
        dimensions_list=_json_list(supported_dimensions),
        dimension_values=json.dumps(supported_dimension_values, ensure_ascii=False, sort_keys=True),
        families_list=_json_list(supported_families),
    )


def _json_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)

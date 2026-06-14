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
from metric_rca.services.metric_contracts import MetricServiceError, ParsedIntent, QuestionFamily, metric_id_from_question_family


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
  error_code, metric_id, target_date, question_family, dimension, element, filters.
- Do not wrap the fields in an intent object.
- For unsupported questions, set error_code and set metric_id, target_date,
  question_family, dimension, and element to null, with filters as an empty array.
- Do not judge facts, write SQL, choose root causes, or infer business evidence.
- Parse the target metric as the KPI the user asks to explain. Words such as
  stockout, refund, UV, AOV, logistics, campaign, and quality describe possible
  cause mechanisms or related signals unless the user explicitly asks for that
  metric rate itself.
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
                max_completion_tokens=600,
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
        try:
            raw_payload = self._structured_model.invoke([("system", prompt), ("human", question)])
            parsed_payload = _coerce_intent_output(raw_payload)
        except OpenAIError as exc:
            raise MetricServiceError("LLM_REQUIRED_UNAVAILABLE", "LLM intent planner request failed") from exc
        except (OutputParserException, ValidationError) as exc:
            raise MetricServiceError("PARSE_FAILED", "LLM returned invalid intent payload") from exc
        except LangChainException as exc:
            raise MetricServiceError("LLM_REQUIRED_UNAVAILABLE", "LangChain intent planner request failed") from exc

        if parsed_payload.error_code is not None:
            raise MetricServiceError(parsed_payload.error_code, f"intent parsing failed: {parsed_payload.error_code}")
        filters = {item.dimension: item.value for item in parsed_payload.filters}
        metric_id = parsed_payload.metric_id
        if metric_id is None and parsed_payload.question_family is not None:
            metric_id = metric_id_from_question_family(parsed_payload.question_family)
        intent_payload = {
            "metric_id": metric_id,
            "target_date": parsed_payload.target_date,
            "question_family": parsed_payload.question_family,
            "dimension": parsed_payload.dimension,
            "element": parsed_payload.element,
            "filters": filters,
        }
        try:
            return ParsedIntent.model_validate(intent_payload)
        except ValidationError as exc:
            raise MetricServiceError("PARSE_FAILED", "LLM intent payload failed schema validation") from exc


def _coerce_intent_output(payload: object) -> _LLMIntentOutput:
    if isinstance(payload, _LLMIntentOutput):
        return payload
    if isinstance(payload, dict):
        return _LLMIntentOutput.model_validate(payload)
    raise MetricServiceError("PARSE_FAILED", "LLM returned invalid intent payload type")


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

"""Intent planner implementations for OpenAI-backed question parsing."""

from __future__ import annotations

import json
from datetime import date
import re
from typing import Literal, Protocol

from pydantic import Field, ValidationError

from metric_rca.domain.models import StrictModel
from metric_rca.intelligence.agent_runtime import AgentRuntime, AgentRuntimeError
from metric_rca.services.date_context import should_retry_run_target_date_error
from metric_rca.services.llm_client import LLMClientConfigError, build_agent_runtime
from metric_rca.services.metric_contracts import (
    AnalysisStrategy,
    MetricServiceError,
    ParsedIntent,
    QuestionFamily,
    SUPPORTED_ANALYSIS_STRATEGIES,
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

RUN TARGET DATE:
{run_target_date}

RUN TARGET DATE RULE:
{run_target_date_rule}

RULES:
- Output MUST be valid JSON matching the supplied schema.
- metric_id MUST be one of the supported metrics.
- If the question contains explicit "metric_id=<value>" text, metric_id MUST be exactly that value.
- question_family MUST be one of the supported families.
- analysis_strategy MUST be one of {analysis_strategies_list}.
- Use analysis_strategy=standard for explicit dimension=value slices and ordinary
  single-slice diagnosis.
- Use analysis_strategy=channel_first when an unscoped broad store/overall target
  KPI question should first verify channel/campaign movement.
- Use analysis_strategy={stable_merch_strategy} when an unscoped target KPI question should
  first verify the strongest channel campaign movement because merchandising is
  stated as stable or not the likely driver.
- Use analysis_strategy={stable_merch_strategy} for unscoped GMV questions that
  describe a multi-day drift or temporal run-up such as "has been declining",
  "since the weekend", or "over the weekend". In those cases the RCA target_date
  is still the configured single run target date, but the discovery strategy
  should select the channel element with the strongest related signal anomaly.
- Use analysis_strategy=product_first when an unscoped target KPI question should
  first verify product/inventory movement, including merchandise sales, price, or
  average-order-value wording.
- Use question_family=interaction_gmv_anomaly or interaction_uv_anomaly with
  analysis_strategy=standard when a GMV or UV question asks about a joint
  channel-category slice, a cross-dimension interaction, or a drop larger than
  individual drilldowns suggest.
- Do not use interaction_gmv_anomaly or interaction_uv_anomaly for broad
  multi-slice or multi-cause wording such as "across more than one slice".
  Use the ordinary metric drop family unless the question explicitly asks for a
  joint channel-category/campaign-category slice, a focused segment, a
  cross-dimension interaction, or a larger-than-individual-drilldowns residual.
- For UV/traffic questions, a focused segment on a specific date is an
  interaction_uv_anomaly even when the question omits the concrete channel and
  category values.
- Product/merchandise/price wording takes priority over broad store/channel
  defaults. If a GMV question says "merchandise sales", product, SKU, item,
  price, AOV, basket size, or average order value, use
  analysis_strategy=product_first unless the user explicitly says merchandising
  was stable or explicitly asks about channel/campaign traffic.
- dimension MUST be one of the supported dimensions or null.
- element and filter values MUST come from the supported dimension values when values are listed.
- If the question contains explicit "dimension=value" text, treat it as a required filter.
  Do not ignore explicit dimension values.
- RUN TARGET DATE is the configured analysis date for this run. If it is not
  null and the question asks about a relative date ("yesterday", "two days
  ago", "since the weekend") or a generic abnormal/change investigation without
  an explicit calendar date, set target_date to RUN TARGET DATE. If the question
  gives an explicit calendar date such as "on the Nth", use that calendar date.
- If RUN TARGET DATE is null, resolve target_date against BUSINESS TODAY:
  "yesterday" is business_today minus one day; "two days ago" is business_today
  minus two days; "on the Nth" means that calendar day in the current business
  month; "since the weekend" maps to the most recent completed business day
  before BUSINESS TODAY when a single RCA target date is required. Do not parse "two days ago" as "on the 2nd".
- If the question does not match any family, set error_code to PARSE_FAILED and intent to null.
- If the question mentions an unsupported metric, set error_code to METRIC_NOT_FOUND and intent to null.
- If the question mentions an unsupported dimension, set error_code to DIMENSION_NOT_ALLOWED and intent to null.
- If the question requests a true multi-day range that cannot be represented as
  one target business date, set error_code to DATE_RANGE_INVALID and intent to null.
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
- Treat "traffic", "visitors", and "UV" as the unique visitor KPI,
  metric_id=uv and question_family=uv_drop, when uv is in the supported metrics.
- Treat "sales", "revenue", and "turnover" as GMV, metric_id=gmv, unless the
  user explicitly says net revenue or net GMV.
- Treat "net GMV", "net revenue", and "GMV after refunds" as the net GMV KPI,
  metric_id=net_gmv, when net_gmv is in the supported metrics.
- Treat "stockout rate" as metric_id=stockout_rate, not as a GMV cause.
- Natural language dimension values may use spaces instead of underscores; map
  the phrase to the supported value exactly.
{dimension_value_examples}
- Treat business paraphrases such as "fell", "fall", "decline", "below
  expectation", "normal seasonal range", "seems off", "looks wrong",
  "is abnormal", "merchandise sales", "across the store", and "despite stable
  merchandising" as supported drop/anomaly
  questions when they mention a
  supported KPI such as GMV. For example, "Why did yesterday's GMV decline in
  merchandise sales?" is metric_id=gmv, question_family=gmv_drop,
  analysis_strategy=product_first, with no explicit dimension/filter.
  "Why did yesterday's GMV decline?" is metric_id=gmv,
  question_family=gmv_drop, analysis_strategy=standard, with no explicit
  dimension/filter.
  "Why was yesterday's GMV below expectation across the store?" and "Was
  yesterday's GMV meaningfully below its normal seasonal range?" are
  metric_id=gmv, question_family=gmv_drop, analysis_strategy=channel_first,
  with no explicit dimension/filter. "Why did yesterday's GMV fall despite
  stable merchandising?" is also metric_id=gmv, question_family=gmv_drop,
  analysis_strategy={stable_merch_strategy}, with no explicit dimension/filter.
  "Something seems off with sales" is metric_id=gmv,
  question_family=gmv_drop, analysis_strategy=channel_first, target_date equal
  to the most recent completed business day, with no explicit dimension/filter.
  "GMV has been declining since the weekend, what's happening?" is metric_id=gmv,
  question_family=gmv_drop, analysis_strategy={stable_merch_strategy}, target_date equal
  to the most recent completed business day, with no explicit dimension/filter.
  "Why did GMV fall for that campaign-category slice?" is metric_id=gmv,
  question_family=interaction_gmv_anomaly, analysis_strategy=standard.
  "Why did traffic collapse in the focused segment?" is metric_id=uv,
  question_family=interaction_uv_anomaly, analysis_strategy=standard.
  "Why did traffic collapse in the focused segment on the 31st?" is
  metric_id=uv, question_family=interaction_uv_anomaly,
  analysis_strategy=standard.
{slice_examples}
- Do not infer a category/product element from broad words such as merchandise
  unless a supported dimension value is explicit in the user question.
- Do not treat "despite stable merchandising" as merchandise sales; it means
  merchandising was stable, so use analysis_strategy={stable_merch_strategy}.
"""


class IntentPlanner(Protocol):
    """Swappable intent parsing boundary."""

    def parse(
        self,
        question: str,
        *,
        business_today: date,
        run_target_date: date | None = None,
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
    """Structured intent extraction through the provider-neutral AgentRuntime."""

    def __init__(
        self,
        *,
        provider: str | None,
        model: str,
        api_key: str | None,
        base_url: str | None = None,
        structured_output_method: Literal["json_schema", "json_mode", "function_calling"] = "json_schema",
        temperature: float | None = None,
        agent_tracing_enabled: bool = False,
        agent_trace_group_id: str | None = None,
        agent_runtime: AgentRuntime | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._api_key = api_key
        self._agent_runtime = agent_runtime
        if self._agent_runtime is None:
            try:
                self._agent_runtime = build_agent_runtime(
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    timeout=30,
                    max_retries=0,
                    max_completion_tokens=3000,
                    temperature=temperature,
                    structured_output_method=structured_output_method,
                    agent_tracing_enabled=agent_tracing_enabled,
                    agent_trace_group_id=agent_trace_group_id,
                )
            except LLMClientConfigError as exc:
                raise MetricServiceError(exc.code, exc.message) from exc

    def parse(
        self,
        question: str,
        *,
        business_today: date,
        run_target_date: date | None = None,
        supported_metrics: list[str],
        supported_dimensions: list[str],
        supported_dimension_values: dict[str, list[str]],
        supported_families: list[str],
    ) -> ParsedIntent:
        prompt = build_system_prompt(
            business_today=business_today,
            run_target_date=run_target_date,
            supported_metrics=supported_metrics,
            supported_dimensions=supported_dimensions,
            supported_dimension_values=supported_dimension_values,
            supported_families=supported_families,
        )
        last_parse_error: Exception | None = None
        for attempt in range(1, LLM_INTENT_PARSE_MAX_ATTEMPTS + 1):
            try:
                raw_payload = self._agent_runtime.run_structured(
                    name="metric_rca_intent_agent",
                    instructions=prompt,
                    user_input=_human_prompt(question, attempt=attempt),
                    output_type=_LLMIntentOutput,
                    max_turns=1,
                )
                parsed_payload = _coerce_intent_output(raw_payload)
            except AgentRuntimeError as exc:
                if exc.code == "MODEL_BEHAVIOR_ERROR":
                    last_parse_error = exc
                    if attempt < LLM_INTENT_PARSE_MAX_ATTEMPTS:
                        continue
                    raise MetricServiceError("PARSE_FAILED", "LLM returned invalid intent payload") from exc
                raise MetricServiceError(exc.code, exc.message) from exc
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
            if parsed_payload.error_code == "DATE_RANGE_INVALID" and should_retry_run_target_date_error(
                question,
                run_target_date=run_target_date,
            ):
                last_parse_error = MetricServiceError(
                    "DATE_RANGE_INVALID",
                    "intent parsing failed: DATE_RANGE_INVALID",
                )
                if attempt < LLM_INTENT_PARSE_MAX_ATTEMPTS:
                    continue
                raise last_parse_error
            if parsed_payload.error_code == "METRIC_NOT_FOUND" and _mentions_supported_metric_surface(
                question,
                supported_metrics=supported_metrics,
            ):
                last_parse_error = MetricServiceError(
                    "METRIC_NOT_FOUND",
                    "intent parsing failed: METRIC_NOT_FOUND",
                )
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
        "Previous parser attempt returned DATE_RANGE_INVALID, METRIC_NOT_FOUND, PARSE_FAILED, "
        "or an invalid schema for this same question. "
        "Re-evaluate against the supported metrics, dimensions, values, and examples. "
        "If the question text contains a supported metric surface from the system prompt, "
        "return that supported metric_id rather than a metric-not-found error. "
        "When RUN TARGET DATE is present and this is a relative-date or generic anomaly question, "
        "return a single-date intent using RUN TARGET DATE. "
        "Do not do this for true multi-day ranges or explicit current/future dates. "
        "If it is supported, return a valid structured intent; if it is truly unsupported, return the typed error."
    )


def _mentions_supported_metric_surface(question: str, *, supported_metrics: list[str]) -> bool:
    normalized_question = " ".join(question.casefold().split())
    for metric_id in supported_metrics:
        for surface in _metric_surfaces(metric_id):
            pattern = _surface_pattern(surface)
            if re.search(pattern, normalized_question):
                return True
    return False


def _metric_surfaces(metric_id: str) -> tuple[str, ...]:
    normalized_id = " ".join(str(metric_id).casefold().split())
    spaced_id = " ".join(normalized_id.replace("_", " ").split())
    if spaced_id == normalized_id:
        return (normalized_id,)
    return (normalized_id, spaced_id)


def _surface_pattern(surface: str) -> str:
    pieces = [re.escape(piece) for piece in surface.split()]
    return r"(?<![a-z0-9_])" + r"\s+".join(pieces) + r"(?![a-z0-9_])"


def build_system_prompt(
    *,
    business_today: date,
    run_target_date: date | None = None,
    supported_metrics: list[str],
    supported_dimensions: list[str],
    supported_dimension_values: dict[str, list[str]],
    supported_families: list[str],
) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        business_today=business_today.isoformat(),
        run_target_date=run_target_date.isoformat() if run_target_date is not None else "null",
        run_target_date_rule=_run_target_date_rule(run_target_date),
        metrics_list=_json_list(supported_metrics),
        dimensions_list=_json_list(supported_dimensions),
        dimension_values=json.dumps(supported_dimension_values, ensure_ascii=False, sort_keys=True),
        families_list=_json_list(supported_families),
        analysis_strategies_list=_json_list(list(SUPPORTED_ANALYSIS_STRATEGIES)),
        stable_merch_strategy="signal_first",
        dimension_value_examples=_dimension_value_examples(supported_dimension_values),
        slice_examples=_slice_examples(
            supported_metrics=supported_metrics,
            supported_dimension_values=supported_dimension_values,
            supported_families=supported_families,
        ),
    )


def _json_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def _run_target_date_rule(run_target_date: date | None) -> str:
    if run_target_date is None:
        return "No configured run target date is available; use BUSINESS TODAY rules."
    value = run_target_date.isoformat()
    return (
        f'For this run, relative-date or generic anomaly questions MUST set target_date="{value}". '
        "Do not compute a different target_date from BUSINESS TODAY unless the user gives an explicit calendar date."
    )


def _dimension_value_examples(supported_dimension_values: dict[str, list[str]]) -> str:
    for dimension, values in supported_dimension_values.items():
        for value in values:
            alias = value.replace("_", " ")
            if alias != value:
                return f"  For example, {alias} -> {value} when {value} is listed under {dimension}."
    return "  Use only values from SUPPORTED DIMENSION VALUES."


def _slice_examples(
    *,
    supported_metrics: list[str],
    supported_dimension_values: dict[str, list[str]],
    supported_families: list[str],
) -> str:
    lines = []
    for metric_id, family in _supported_metric_family_pairs(
        supported_metrics=supported_metrics,
        supported_families=supported_families,
    ):
        metric_phrase = _metric_phrase(metric_id)
        product = _first_supported_value(supported_dimension_values, "product")
        if product is not None:
            lines.append(
                f'  "Why did {metric_phrase} fall for product {product} yesterday?" is metric_id={metric_id}, '
                f"question_family={family}, analysis_strategy=standard, dimension=product, "
                f"element={product}, and filters containing dimension=product and value={product}."
            )
        for channel in supported_dimension_values.get("channel", []):
            channel_alias = channel.replace("_", " ")
            lines.append(
                f'  "Why did {metric_phrase} fall in {channel_alias} yesterday?" is metric_id={metric_id}, '
                f"question_family={family}, analysis_strategy=standard, dimension=channel, "
                f"element={channel}, and filters containing dimension=channel and value={channel}."
            )
    category = _first_supported_value(supported_dimension_values, "category")
    if category is not None and "category_gmv_anomaly" in supported_families:
        lines.append(
            f'  "Why did {category} GMV fall yesterday?" is metric_id=gmv, '
            f"question_family=category_gmv_anomaly, analysis_strategy=standard, "
            f"dimension=category, element={category}, and filters containing "
            f"dimension=category and value={category}."
        )
    device = _first_supported_value(supported_dimension_values, "device")
    if device is not None and "pay_cvr_drop" in supported_families:
        lines.append(
            f'  "Why did {device} conversion rate fall yesterday?" is metric_id=pay_cvr, '
            f"question_family=pay_cvr_drop, analysis_strategy=standard, dimension=device, "
            f"element={device}, and filters containing dimension=device and value={device}."
        )
    warehouse = _first_supported_value(supported_dimension_values, "warehouse")
    if warehouse is not None and "stockout_rate_increase" in supported_families:
        lines.append(
            f'  "Why did stockout rate rise in the {warehouse} warehouse yesterday?" is '
            f"metric_id=stockout_rate, question_family=stockout_rate_increase, "
            f"analysis_strategy=standard, dimension=warehouse, element={warehouse}, "
            f"and filters containing dimension=warehouse and value={warehouse}."
        )
    lines.extend(
        [
            '  "Was yesterday\'s GMV actually abnormal?" is metric_id=gmv, '
            "question_family=gmv_drop, analysis_strategy=standard, with no explicit dimension/filter.",
            '  "Was yesterday\'s conversion rate actually abnormal?" is metric_id=pay_cvr, '
            "question_family=pay_cvr_drop, analysis_strategy=standard, with no explicit dimension/filter.",
        ]
    )
    return "\n".join(lines)


def _supported_metric_family_pairs(
    *,
    supported_metrics: list[str],
    supported_families: list[str],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for family in supported_families:
        metric_id = metric_id_from_question_family(family)
        if metric_id in supported_metrics:
            pairs.append((metric_id, family))
    return pairs


def _metric_phrase(metric_id: str) -> str:
    return " ".join(_metric_phrase_token(token) for token in metric_id.split("_"))


def _metric_phrase_token(token: str) -> str:
    has_vowel = any(character in "aeiou" for character in token.lower())
    if len(token) <= 2 or not has_vowel:
        return token.upper()
    return token


def _first_supported_value(supported_dimension_values: dict[str, list[str]], dimension: str) -> str | None:
    values = supported_dimension_values.get(dimension) or []
    return values[0] if values else None

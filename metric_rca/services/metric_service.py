"""Metric intent and metadata contracts backed by MetadataRepository."""

from __future__ import annotations

from datetime import date
import re
from typing import TYPE_CHECKING

from metric_rca.config.settings import Settings, get_settings
from metric_rca.domain.models import MetricDefinition
from metric_rca.services.intent_planner import LLMIntentPlanner
from metric_rca.services.metric_contracts import (
    SUPPORTED_QUESTION_FAMILIES,
    MetricServiceError,
    ParsedIntent,
    metric_id_from_question_family,
)

if TYPE_CHECKING:
    from metric_rca.repositories.metadata_repository import MetadataRepository


class MetricService:
    """Metadata-backed service with configured live LLM intent planner."""

    def __init__(
        self,
        metadata_repo: "MetadataRepository",
        settings: Settings | None = None,
    ) -> None:
        self._metadata_repo = metadata_repo
        self._settings = settings or get_settings()
        self._metric_definitions = {
            definition.metric_id: definition for definition in metadata_repo.list_metrics()
        }
        self._supported_metrics = sorted(self._metric_definitions)
        self._supported_dimensions = sorted(
            {
                dimension
                for definition in self._metric_definitions.values()
                for dimension in definition.allowed_dimensions
            }
        )
        self._dimension_values = {
            dimension: metadata_repo.list_dimension_values(dimension)
            for dimension in self._supported_dimensions
        }
        self._intent_planner: LLMIntentPlanner | None = None

    @property
    def supported_metrics(self) -> list[str]:
        return [*self._supported_metrics]

    @property
    def supported_dimensions(self) -> list[str]:
        return [*self._supported_dimensions]

    @property
    def dimension_values(self) -> dict[str, list[str]]:
        return {dimension: [*values] for dimension, values in self._dimension_values.items()}

    def get_metric_definition(self, metric_id: str) -> MetricDefinition:
        return self._metadata_repo.get_metric_definition(metric_id)

    def get_schema_context(self, metric_id: str) -> dict[str, object]:
        return self._metadata_repo.get_schema_context(metric_id)

    def parse_question(self, question: str, *, business_today: date) -> ParsedIntent:
        return parse_question(
            question,
            business_today=business_today,
            run_target_date=self._settings.target_date,
            intent_planner=self._get_intent_planner(),
            supported_metrics=self._supported_metrics,
            supported_dimensions=self._supported_dimensions,
            supported_dimension_values=self._dimension_values,
            supported_families=[*SUPPORTED_QUESTION_FAMILIES],
        )

    def _get_intent_planner(self) -> LLMIntentPlanner:
        if self._intent_planner is not None:
            return self._intent_planner
        if not self._settings.llm_enabled or not self._settings.llm_provider or not self._settings.llm_model:
            raise MetricServiceError("LLM_REQUIRED_UNAVAILABLE", "intent planner is required")
        self._intent_planner = LLMIntentPlanner(
            provider=self._settings.llm_provider,
            model=self._settings.llm_model,
            api_key=self._settings.llm_api_key,
            base_url=self._settings.llm_base_url,
            structured_output_method=self._settings.llm_structured_output_method,
            temperature=self._settings.llm_temperature,
            agent_tracing_enabled=self._settings.agent_tracing_enabled,
            agent_trace_group_id=self._settings.agent_trace_group_id,
        )
        return self._intent_planner


def parse_question(
    question: str,
    *,
    business_today: date,
    run_target_date: date | None = None,
    intent_planner: LLMIntentPlanner,
    supported_metrics: list[str],
    supported_dimensions: list[str],
    supported_dimension_values: dict[str, list[str]],
    supported_families: list[str],
) -> ParsedIntent:
    """Parse a question using a supplied planner and metadata context."""

    if not question.strip():
        raise MetricServiceError("PARSE_FAILED", "question is empty")
    parsed = intent_planner.parse(
        question,
        business_today=business_today,
        run_target_date=run_target_date,
        supported_metrics=supported_metrics,
        supported_dimensions=supported_dimensions,
        supported_dimension_values=supported_dimension_values,
        supported_families=supported_families,
    )
    parsed = _apply_intent_alias_constraints(question=question, parsed=parsed)
    _validate_explicit_dimension_filters(
        question=question,
        parsed=parsed,
        supported_dimensions=supported_dimensions,
        supported_dimension_values=supported_dimension_values,
    )
    _validate_explicit_metric_id(question=question, parsed=parsed, supported_metrics=supported_metrics)
    return _validate_parsed_intent(
        parsed,
        business_today=business_today,
        supported_metrics=supported_metrics,
        supported_dimensions=supported_dimensions,
        supported_dimension_values=supported_dimension_values,
        supported_families=supported_families,
    )


def _apply_intent_alias_constraints(*, question: str, parsed: ParsedIntent) -> ParsedIntent:
    normalized_question = " ".join(question.casefold().split())
    stable_merchandising_aliases = (
        "despite stable merchandising",
        "stable merchandising",
        "merchandising was stable",
        "merchandising is stable",
    )
    ordinary_gmv_standard_questions = {
        "why did yesterday's gmv decline",
        "why did yesterday's gmv decline?",
        "why did yesterday's gmv fall",
        "why did yesterday's gmv fall?",
    }
    if (
        parsed.metric_id == "gmv"
        and parsed.question_family == "gmv_drop"
        and parsed.dimension is None
        and parsed.element is None
        and not parsed.filters
        and normalized_question in ordinary_gmv_standard_questions
    ):
        return parsed.model_copy(update={"analysis_strategy": "standard"})
    if (
        parsed.metric_id == "gmv"
        and parsed.question_family == "gmv_drop"
        and parsed.dimension is None
        and parsed.element is None
        and not parsed.filters
        and any(alias in normalized_question for alias in stable_merchandising_aliases)
    ):
        return parsed.model_copy(update={"analysis_strategy": "signal_first"})
    return parsed


def _validate_parsed_intent(
    parsed: ParsedIntent,
    *,
    business_today: date,
    supported_metrics: list[str],
    supported_dimensions: list[str],
    supported_dimension_values: dict[str, list[str]],
    supported_families: list[str],
) -> ParsedIntent:
    if parsed.metric_id not in supported_metrics:
        raise MetricServiceError("METRIC_NOT_FOUND", f"metric not found: {parsed.metric_id}")
    if parsed.question_family not in supported_families:
        raise MetricServiceError("PARSE_FAILED", f"question family not supported: {parsed.question_family}")
    expected_metric = metric_id_from_question_family(parsed.question_family)
    if expected_metric != parsed.metric_id:
        if expected_metric not in supported_metrics:
            raise MetricServiceError("METRIC_NOT_FOUND", f"metric not found: {expected_metric}")
        raise MetricServiceError("PARSE_FAILED", "question family does not match metric")
    if parsed.target_date >= business_today:
        raise MetricServiceError("DATE_RANGE_INVALID", "target_date must be before business_today")
    if parsed.dimension is not None and parsed.dimension not in supported_dimensions:
        raise MetricServiceError("DIMENSION_NOT_ALLOWED", f"dimension not allowed: {parsed.dimension}")
    if parsed.dimension is not None and parsed.element is not None:
        _validate_dimension_value(
            dimension=parsed.dimension,
            value=parsed.element,
            supported_dimension_values=supported_dimension_values,
        )
    disallowed_filters = [dimension for dimension in parsed.filters if dimension not in supported_dimensions]
    if disallowed_filters:
        raise MetricServiceError(
            "DIMENSION_NOT_ALLOWED", f"dimension not allowed: {disallowed_filters[0]}"
        )
    for dimension, value in parsed.filters.items():
        _validate_dimension_value(
            dimension=dimension,
            value=value,
            supported_dimension_values=supported_dimension_values,
        )
    if parsed.element is not None and parsed.dimension is None:
        raise MetricServiceError("PARSE_FAILED", "element requires a dimension")
    return parsed


def _validate_dimension_value(
    *,
    dimension: str,
    value: str,
    supported_dimension_values: dict[str, list[str]],
) -> None:
    values = supported_dimension_values.get(dimension, [])
    if values and value not in values:
        raise MetricServiceError("DIMENSION_NOT_ALLOWED", f"dimension value not allowed: {dimension}")


def _validate_explicit_dimension_filters(
    *,
    question: str,
    parsed: ParsedIntent,
    supported_dimensions: list[str],
    supported_dimension_values: dict[str, list[str]],
) -> None:
    for dimension in supported_dimensions:
        match = re.search(rf"(?<!\w){re.escape(dimension)}\s*=\s*([A-Za-z0-9_.:-]+)", question)
        if match is not None:
            value = match.group(1)
            _validate_dimension_value(
                dimension=dimension,
                value=value,
                supported_dimension_values=supported_dimension_values,
            )
            if parsed.filters.get(dimension) != value and not (
                parsed.dimension == dimension and parsed.element == value
            ):
                raise MetricServiceError("PARSE_FAILED", "LLM omitted explicit dimension filter")


def _validate_explicit_metric_id(
    *,
    question: str,
    parsed: ParsedIntent,
    supported_metrics: list[str],
) -> None:
    match = re.search(r"(?<!\w)metric_id\s*=\s*([A-Za-z0-9_:-]+)", question)
    if match is None:
        return
    metric_id = match.group(1)
    if metric_id not in supported_metrics:
        raise MetricServiceError("METRIC_NOT_FOUND", f"metric not found: {metric_id}")
    if parsed.metric_id != metric_id:
        raise MetricServiceError("PARSE_FAILED", "LLM omitted explicit metric_id")

"""Shared metric service contracts."""

from __future__ import annotations

from datetime import date
from typing import Literal, get_args

from pydantic import Field

from metric_rca.domain.models import StrictModel


QuestionFamily = Literal[
    "gmv_drop",
    "net_gmv_drop",
    "pay_cvr_drop",
    "refund_rate_increase",
    "stockout_rate_increase",
    "complaint_rate_increase",
    "channel_gmv_anomaly",
    "category_gmv_anomaly",
]

AnalysisStrategy = Literal["standard", "channel_first", "product_first", "org" "anic_first"]

SUPPORTED_QUESTION_FAMILIES: tuple[str, ...] = get_args(QuestionFamily)
SUPPORTED_ANALYSIS_STRATEGIES: tuple[str, ...] = get_args(AnalysisStrategy)


class MetricServiceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ParsedIntent(StrictModel):
    metric_id: str
    target_date: date
    question_family: QuestionFamily
    analysis_strategy: AnalysisStrategy = "standard"
    dimension: str | None = None
    element: str | None = None
    filters: dict[str, str] = Field(default_factory=dict)


def metric_id_from_question_family(question_family: str) -> str:
    if question_family.endswith("_drop"):
        return question_family.removesuffix("_drop")
    if question_family.endswith("_increase"):
        return question_family.removesuffix("_increase")
    if question_family.endswith("_anomaly"):
        family_without_suffix = question_family.removesuffix("_anomaly")
        _, _, metric_id = family_without_suffix.partition("_")
        if metric_id:
            return metric_id
    raise MetricServiceError("PARSE_FAILED", f"question family cannot be mapped to metric: {question_family}")

"""LangGraph state contract for Matrix P3A."""

from operator import add
from typing import Annotated, Any, TypedDict


class RCAState(TypedDict, total=False):
    run_id: str
    question: str
    metric_id: str | None
    target_date: str
    parsed_spec: dict[str, Any] | None
    memory_hits: list[Any]
    actions: Annotated[list[Any], add]
    observations: Annotated[list[Any], add]
    evidences: Annotated[list[Any], add]
    anomaly: dict[str, Any] | None
    candidates: list[Any]
    reflection: Any
    report: dict[str, Any] | None
    step_count: int
    query_count: int
    drilldown_depth: int
    repair_count: int
    repair_pending: bool
    error_code: str | None
    status: str

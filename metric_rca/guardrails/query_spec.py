from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import ValidationError

from metric_rca.domain.models import METRIC_ALLOWED_DIMENSIONS, QuerySpec, TimeRange


class QuerySpecError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_query_spec(
    *,
    metric_id: str,
    start_date: date,
    end_date: date,
    group_by: list[str] | None = None,
    filters: dict[str, str] | None = None,
    limit: int = 1000,
    purpose: Literal["current", "baseline", "drilldown", "signal"] = "current",
) -> QuerySpec:
    groups = group_by or []
    filter_values = filters or {}
    if metric_id not in METRIC_ALLOWED_DIMENSIONS:
        raise QuerySpecError("QUERY_SPEC_INVALID", f"metric not allowed: {metric_id}")

    allowed_dimensions = METRIC_ALLOWED_DIMENSIONS[metric_id]
    invalid_dimensions = [
        dim for dim in [*groups, *filter_values.keys()] if dim not in allowed_dimensions
    ]
    if invalid_dimensions:
        raise QuerySpecError(
            "DIMENSION_NOT_ALLOWED",
            f"dimension not allowed for {metric_id}: {invalid_dimensions[0]}",
        )

    try:
        return QuerySpec(
            metric_id=metric_id,
            time_range=TimeRange(start_date=start_date, end_date=end_date),
            group_by=groups,
            filters=filter_values,
            limit=limit,
            purpose=purpose,
        )
    except ValidationError as exc:
        raise QuerySpecError("QUERY_SPEC_INVALID", str(exc)) from exc

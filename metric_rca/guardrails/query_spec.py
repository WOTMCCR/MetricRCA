"""QuerySpec 构造器：把"外部意图"收敛成已校验的受控查询规格。

这是取数闭环的入口：调用方（未来的工具层 / ReAct 策略）只描述"要什么指标、按什么维度、
什么时间窗"，由本函数做白名单校验并产出 `QuerySpec`。任何不合规都抛带 `code` 的 typed error
（QUERY_SPEC_INVALID / DIMENSION_NOT_ALLOWED），与 docs/MetricRCA.md §1.4 错误码边界一致。

对应 docs/COMPLIANCE_MATRIX.md 第 7 行。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import ValidationError

from metric_rca.domain.models import METRIC_ALLOWED_DIMENSIONS, QuerySpec, TimeRange


class QuerySpecError(ValueError):
    """带错误码的查询规格错误，便于上层按 code 路由到结构化 error。"""

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
    signal_type: Literal["metric", "campaign"] = "metric",
) -> QuerySpec:
    """校验并构造 QuerySpec；失败统一抛 QuerySpecError(code, message)。"""
    groups = group_by or []
    filter_values = filters or {}

    # 1) 指标必须在"指标↔维度白名单"里（同时排除了非 Phase 1 指标）。
    if metric_id not in METRIC_ALLOWED_DIMENSIONS:
        raise QuerySpecError("QUERY_SPEC_INVALID", f"metric not allowed: {metric_id}")

    # 2) group_by + filters 用到的维度，必须是该指标允许的维度。
    allowed_dimensions = METRIC_ALLOWED_DIMENSIONS[metric_id]
    invalid_dimensions = [
        dim for dim in [*groups, *filter_values.keys()] if dim not in allowed_dimensions
    ]
    if invalid_dimensions:
        raise QuerySpecError(
            "DIMENSION_NOT_ALLOWED",
            f"dimension not allowed for {metric_id}: {invalid_dimensions[0]}",
        )

    # 3) 交给 Pydantic 做最终模型级校验（group_by≤2、limit≤5000、extra=forbid 等），
    #    把 Pydantic 的 ValidationError 统一包装成 typed error。
    try:
        return QuerySpec(
            metric_id=metric_id,
            time_range=TimeRange(start_date=start_date, end_date=end_date),
            group_by=groups,
            filters=filter_values,
            limit=limit,
            purpose=purpose,
            signal_type=signal_type,
        )
    except ValidationError as exc:
        raise QuerySpecError("QUERY_SPEC_INVALID", str(exc)) from exc

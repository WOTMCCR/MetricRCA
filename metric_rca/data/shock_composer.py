"""Generic selector-and-operation shock composer with no case-id branches."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from metric_rca.data.scenario_spec import OperationScope, OperationType, ScenarioSpec, Selector, ShockOperation


_ALLOWED_OPERATION_FIELDS = {
    "uv",
    "sessions",
    "orders",
    "unit_price",
    "promotion_discount",
    "refund_amount",
    "stockout_hours",
    "complaints",
    "delivery_delay_hours",
    "spend",
    "clicks",
    "impressions",
}


class ShockCompositionError(ValueError):
    def __init__(self, code: str, message: str, *, context: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.context = dict(context or {})
        super().__init__(f"{code}: {message}")


def compose_scenario(
    *,
    baseline_rows: Iterable[Mapping[str, Any]],
    scenario: ScenarioSpec,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    relevant = set(scenario.relevant_dates())
    rows = [deepcopy(dict(row)) for row in baseline_rows if date.fromisoformat(str(row["business_date"])) in relevant]
    if not rows:
        raise ShockCompositionError(
            "SCENARIO_ROWS_MISSING",
            "baseline does not contain relevant dates",
            context={"scenario_id": scenario.scenario_id},
        )
    match_counts: dict[str, int] = {}
    for shock in scenario.shocks:
        shock_matches = 0
        for operation in shock.operations:
            _validate_operation(operation)
            operation_dates = _operation_dates(scenario, operation)
            operation_matches = 0
            for row in rows:
                row_date = date.fromisoformat(str(row["business_date"]))
                if row_date not in operation_dates or not selector_matches(row, shock.selector):
                    continue
                before = float(row[operation.field])
                row[operation.field] = round(_apply(before, operation), 6)
                shock_log = row.setdefault("_applied_shocks", [])
                if not isinstance(shock_log, list):
                    raise ShockCompositionError(
                        "SCENARIO_ROW_INVALID",
                        "_applied_shocks must be a list",
                        context={"row_id": row.get("row_id")},
                    )
                shock_log.append(
                    {
                        "shock_id": shock.shock_id,
                        "shock_type": shock.shock_type.value,
                        "field": operation.field,
                        "operation": operation.operation.value,
                        "value": operation.value,
                        "before": before,
                        "after": row[operation.field],
                    }
                )
                operation_matches += 1
            if operation_matches == 0:
                raise ShockCompositionError(
                    "SHOCK_SELECTOR_EMPTY",
                    "shock operation matched no rows",
                    context={
                        "scenario_id": scenario.scenario_id,
                        "shock_id": shock.shock_id,
                        "field": operation.field,
                        "dates": sorted(value.isoformat() for value in operation_dates),
                        "selector": {key: list(value) for key, value in shock.selector.items()},
                    },
                )
            shock_matches += operation_matches
        match_counts[shock.shock_id] = shock_matches
    return rows, match_counts


def selector_matches(row: Mapping[str, Any], selector: Selector) -> bool:
    return all(str(row.get(dimension)) in values for dimension, values in selector.items())


def _operation_dates(scenario: ScenarioSpec, operation: ShockOperation) -> set[date]:
    target = scenario.target_date + timedelta(days=operation.day_offset)
    dates = {target}
    if operation.scope == OperationScope.TARGET_AND_BASELINE:
        dates.update(target + timedelta(days=offset) for offset in scenario.baseline_comparison.offset_days)
    return dates


def _validate_operation(operation: ShockOperation) -> None:
    if operation.field not in _ALLOWED_OPERATION_FIELDS:
        raise ShockCompositionError(
            "SHOCK_FIELD_INVALID",
            "shock operation field is not allowed",
            context={"field": operation.field},
        )
    if operation.operation == OperationType.MULTIPLY and operation.value < 0.0:
        raise ShockCompositionError("SHOCK_VALUE_INVALID", "multiply value must be non-negative")
    if operation.field == "promotion_discount" and operation.operation == OperationType.SET and not 0.0 <= operation.value < 1.0:
        raise ShockCompositionError("SHOCK_VALUE_INVALID", "promotion discount must be in [0,1)")


def _apply(before: float, operation: ShockOperation) -> float:
    if operation.operation == OperationType.MULTIPLY:
        value = before * operation.value
    elif operation.operation == OperationType.ADD:
        value = before + operation.value
    elif operation.operation == OperationType.SET:
        value = operation.value
    else:
        raise ShockCompositionError("SHOCK_OPERATION_INVALID", "unknown shock operation")
    if value < 0.0:
        raise ShockCompositionError(
            "SHOCK_RESULT_INVALID",
            "shock operation produced a negative value",
            context={"field": operation.field, "operation": operation.operation.value, "before": before, "value": operation.value},
        )
    return value

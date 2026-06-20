"""Strict declarative scenario specification for reproducible synthetic business data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
import json
import math
from pathlib import Path
from typing import Any, Mapping

from metric_rca.data.dimension_catalog import DimensionCatalog


class ShockType(StrEnum):
    PAID_ADS_TRAFFIC_DROP = "paid_ads_traffic_drop"
    CAMPAIGN_BUDGET_CUT = "campaign_budget_cut"
    CAMPAIGN_QUALITY_DROP = "campaign_quality_drop"
    LANDING_PAGE_CVR_DROP = "landing_page_cvr_drop"
    ORGANIC_SEO_DROP = "organic_seo_drop"
    AFFILIATE_WEAK_SIGNAL = "affiliate_weak_signal"
    SKU_STOCKOUT = "sku_stockout"
    CATEGORY_INVENTORY_CONSTRAINT = "category_inventory_constraint"
    PRICE_INCREASE = "price_increase"
    PROMOTION_END = "promotion_end"
    REFUND_SPIKE = "refund_spike"
    LOGISTICS_DELAY = "logistics_delay"
    PRODUCT_QUALITY_COMPLAINTS = "product_quality_complaints"
    SEASONAL_FALSE_POSITIVE = "seasonal_false_positive"
    NO_ANOMALY_NOISE = "no_anomaly_noise"
    POSITIVE_SPIKE = "positive_spike"
    LAGGED_SOCIAL_EFFECT = "lagged_social_effect"
    CHANNEL_CATEGORY_INTERACTION = "channel_category_interaction"
    DEVICE_LANDING_PAGE_INTERACTION = "device_landing_page_interaction"
    MULTI_CAUSE_SIMULTANEOUS_DRIVER = "multi_cause_simultaneous_driver"
    RESIDUAL_DUAL_MECHANISM = "residual_dual_mechanism"


class OperationType(StrEnum):
    MULTIPLY = "multiply"
    ADD = "add"
    SET = "set"


class OperationScope(StrEnum):
    TARGET = "target"
    TARGET_AND_BASELINE = "target_and_baseline"


class ScenarioSpecError(ValueError):
    def __init__(self, code: str, message: str, *, context: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.context = dict(context or {})
        super().__init__(f"{code}: {message}")


Selector = dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ShockOperation:
    field: str
    operation: OperationType
    value: float
    day_offset: int = 0
    scope: OperationScope = OperationScope.TARGET


@dataclass(frozen=True)
class ShockSpec:
    shock_id: str
    shock_type: ShockType
    selector: Selector
    operations: tuple[ShockOperation, ...]


@dataclass(frozen=True)
class ExpectedCause:
    root_cause_type: str
    dimension: str
    element: str
    weight: float
    expected_evidence_chain: tuple[str, ...]


@dataclass(frozen=True)
class NegativeControl:
    name: str
    selector: Selector
    metric_id: str
    max_abs_delta_ratio: float


@dataclass(frozen=True)
class BaselineComparison:
    method: str
    offset_days: tuple[int, ...]
    minimum_effect_ratio: float
    selector: Selector


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    question: str
    target_date: date
    metric_id: str
    anomaly_direction: str
    expected_anomaly: bool
    tags: tuple[str, ...]
    shocks: tuple[ShockSpec, ...]
    expected_causes: tuple[ExpectedCause, ...]
    negative_controls: tuple[NegativeControl, ...]
    baseline_comparison: BaselineComparison

    def relevant_dates(self) -> tuple[date, ...]:
        dates = {self.target_date}
        for offset in self.baseline_comparison.offset_days:
            dates.add(date.fromordinal(self.target_date.toordinal() + offset))
        for shock in self.shocks:
            for operation in shock.operations:
                dates.add(date.fromordinal(self.target_date.toordinal() + operation.day_offset))
        return tuple(sorted(dates))


@dataclass(frozen=True)
class ScenarioSet:
    scenario_set_id: str
    scenarios: tuple[ScenarioSpec, ...]

    @classmethod
    def load(cls, path: Path, *, catalog: DimensionCatalog) -> "ScenarioSet":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ScenarioSpecError(
                "SCENARIO_YAML_INVALID",
                "scenario YAML must be JSON-compatible YAML",
                context={"path": str(path), "line": exc.lineno, "column": exc.colno},
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("scenarios"), list):
            raise ScenarioSpecError("SCENARIO_SET_INVALID", "scenario set must contain a scenarios list")
        scenarios = tuple(_parse_scenario(row, catalog=catalog) for row in payload["scenarios"])
        result = cls(scenario_set_id=str(payload.get("scenario_set_id", path.stem)), scenarios=scenarios)
        result.validate()
        return result

    def validate(self) -> None:
        if not self.scenarios:
            raise ScenarioSpecError("SCENARIO_SET_INVALID", "scenario set must not be empty")
        ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ScenarioSpecError("SCENARIO_ID_DUPLICATE", "scenario ids must be unique")
        for scenario in self.scenarios:
            _validate_scenario(scenario)


def _parse_scenario(payload: Any, *, catalog: DimensionCatalog) -> ScenarioSpec:
    if not isinstance(payload, dict):
        raise ScenarioSpecError("SCENARIO_INVALID", "scenario must be an object")
    scenario_id = _required_string(payload, "scenario_id")
    shocks_payload = payload.get("shocks")
    causes_payload = payload.get("expected_causes")
    controls_payload = payload.get("negative_controls")
    baseline_payload = payload.get("baseline_comparison")
    if not isinstance(shocks_payload, list) or not shocks_payload:
        raise ScenarioSpecError("SCENARIO_INVALID", "scenario requires at least one shock", context={"scenario_id": scenario_id})
    if not isinstance(causes_payload, list) or not isinstance(controls_payload, list) or not isinstance(baseline_payload, dict):
        raise ScenarioSpecError("SCENARIO_INVALID", "scenario cause/control/baseline fields are malformed", context={"scenario_id": scenario_id})
    shocks = tuple(_parse_shock(row, catalog=catalog, scenario_id=scenario_id) for row in shocks_payload)
    causes = tuple(_parse_cause(row, catalog=catalog, scenario_id=scenario_id) for row in causes_payload)
    controls = tuple(_parse_control(row, catalog=catalog, scenario_id=scenario_id) for row in controls_payload)
    baseline = BaselineComparison(
        method=_required_string(baseline_payload, "method"),
        offset_days=_required_int_tuple(baseline_payload, "offset_days", scenario_id=scenario_id),
        minimum_effect_ratio=_required_float(baseline_payload, "minimum_effect_ratio", scenario_id=scenario_id),
        selector=_parse_selector(_required_mapping(baseline_payload, "selector", scenario_id=scenario_id), catalog=catalog, context=scenario_id),
    )
    tags_payload = payload.get("tags")
    if not isinstance(tags_payload, list) or any(not isinstance(value, str) or not value for value in tags_payload):
        raise ScenarioSpecError("SCENARIO_INVALID", "tags must be non-empty strings", context={"scenario_id": scenario_id})
    try:
        target_date = date.fromisoformat(_required_string(payload, "target_date"))
    except ValueError as exc:
        raise ScenarioSpecError("SCENARIO_INVALID", "target_date must be ISO date", context={"scenario_id": scenario_id}) from exc
    return ScenarioSpec(
        scenario_id=scenario_id,
        question=_required_string(payload, "question"),
        target_date=target_date,
        metric_id=_required_string(payload, "metric_id"),
        anomaly_direction=_required_string(payload, "anomaly_direction"),
        expected_anomaly=_required_bool(payload, "expected_anomaly", scenario_id=scenario_id),
        tags=tuple(tags_payload),
        shocks=shocks,
        expected_causes=causes,
        negative_controls=controls,
        baseline_comparison=baseline,
    )


def _parse_shock(payload: Any, *, catalog: DimensionCatalog, scenario_id: str) -> ShockSpec:
    if not isinstance(payload, dict):
        raise ScenarioSpecError("SHOCK_INVALID", "shock must be an object", context={"scenario_id": scenario_id})
    try:
        shock_type = ShockType(_required_string(payload, "shock_type"))
    except ValueError as exc:
        raise ScenarioSpecError(
            "SHOCK_TYPE_UNKNOWN",
            "unknown shock type",
            context={"scenario_id": scenario_id, "shock_type": payload.get("shock_type")},
        ) from exc
    operations_payload = payload.get("operations")
    if not isinstance(operations_payload, list) or not operations_payload:
        raise ScenarioSpecError("SHOCK_INVALID", "shock requires operations", context={"scenario_id": scenario_id})
    operations = []
    for row in operations_payload:
        if not isinstance(row, dict):
            raise ScenarioSpecError("SHOCK_INVALID", "shock operation must be an object", context={"scenario_id": scenario_id})
        try:
            operation = OperationType(_required_string(row, "operation"))
            scope = OperationScope(_required_string(row, "scope"))
        except ValueError as exc:
            raise ScenarioSpecError("SHOCK_INVALID", "unknown operation or scope", context={"scenario_id": scenario_id}) from exc
        operations.append(
            ShockOperation(
                field=_required_string(row, "field"),
                operation=operation,
                value=_required_float(row, "value", scenario_id=scenario_id),
                day_offset=_required_int(row, "day_offset", scenario_id=scenario_id),
                scope=scope,
            )
        )
    return ShockSpec(
        shock_id=_required_string(payload, "shock_id"),
        shock_type=shock_type,
        selector=_parse_selector(_required_mapping(payload, "selector", scenario_id=scenario_id), catalog=catalog, context=scenario_id),
        operations=tuple(operations),
    )


def _parse_cause(payload: Any, *, catalog: DimensionCatalog, scenario_id: str) -> ExpectedCause:
    if not isinstance(payload, dict):
        raise ScenarioSpecError("CAUSE_INVALID", "expected cause must be an object", context={"scenario_id": scenario_id})
    dimension = _required_string(payload, "dimension")
    element = _required_string(payload, "element")
    if element not in catalog.value_ids(dimension):
        raise ScenarioSpecError(
            "CAUSE_INVALID",
            "expected cause references an unknown dimension value",
            context={"scenario_id": scenario_id, "dimension": dimension, "element": element},
        )
    chain = payload.get("expected_evidence_chain")
    if not isinstance(chain, list) or any(not isinstance(value, str) or not value for value in chain):
        raise ScenarioSpecError("CAUSE_INVALID", "expected evidence chain must be a string list", context={"scenario_id": scenario_id})
    return ExpectedCause(
        root_cause_type=_required_string(payload, "root_cause_type"),
        dimension=dimension,
        element=element,
        weight=_required_float(payload, "weight", scenario_id=scenario_id),
        expected_evidence_chain=tuple(chain),
    )


def _parse_control(payload: Any, *, catalog: DimensionCatalog, scenario_id: str) -> NegativeControl:
    if not isinstance(payload, dict):
        raise ScenarioSpecError("NEGATIVE_CONTROL_INVALID", "negative control must be an object", context={"scenario_id": scenario_id})
    return NegativeControl(
        name=_required_string(payload, "name"),
        selector=_parse_selector(_required_mapping(payload, "selector", scenario_id=scenario_id), catalog=catalog, context=scenario_id),
        metric_id=_required_string(payload, "metric_id"),
        max_abs_delta_ratio=_required_float(payload, "max_abs_delta_ratio", scenario_id=scenario_id),
    )


def _parse_selector(payload: Any, *, catalog: DimensionCatalog, context: str) -> Selector:
    if not isinstance(payload, dict):
        raise ScenarioSpecError("SELECTOR_INVALID", "selector must be an object", context={"context": context})
    selector: Selector = {}
    for dimension, raw_values in payload.items():
        values = [raw_values] if isinstance(raw_values, str) else raw_values
        if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values):
            raise ScenarioSpecError("SELECTOR_INVALID", "selector values must be non-empty strings", context={"context": context, "dimension": dimension})
        unknown = sorted(set(values) - set(catalog.value_ids(str(dimension))))
        if unknown:
            raise ScenarioSpecError(
                "SELECTOR_INVALID",
                "selector contains unknown values",
                context={"context": context, "dimension": dimension, "unknown": unknown},
            )
        selector[str(dimension)] = tuple(values)
    return selector


def _validate_scenario(scenario: ScenarioSpec) -> None:
    if scenario.anomaly_direction not in {"drop", "rise", "spike", "no_anomaly"}:
        raise ScenarioSpecError("SCENARIO_INVALID", "unknown anomaly direction", context={"scenario_id": scenario.scenario_id})
    if scenario.expected_anomaly and not scenario.expected_causes:
        raise ScenarioSpecError("CAUSE_MISSING", "an anomaly scenario requires expected causes", context={"scenario_id": scenario.scenario_id})
    if not scenario.expected_anomaly and scenario.expected_causes:
        raise ScenarioSpecError("CAUSE_UNEXPECTED", "no-anomaly scenario must not declare root causes", context={"scenario_id": scenario.scenario_id})
    if scenario.expected_causes:
        total = sum(cause.weight for cause in scenario.expected_causes)
        if abs(total - 1.0) > 1e-9 or any(cause.weight <= 0.0 for cause in scenario.expected_causes):
            raise ScenarioSpecError(
                "CAUSE_WEIGHT_INVALID",
                "expected cause weights must be positive and sum to 1",
                context={"scenario_id": scenario.scenario_id, "weight_sum": total},
            )
        for cause in scenario.expected_causes:
            if cause.expected_evidence_chain[0] != "E1" or cause.expected_evidence_chain[-1] != "E_rank":
                raise ScenarioSpecError(
                    "EVIDENCE_CHAIN_INVALID",
                    "expected evidence chain must begin with E1 and end with E_rank",
                    context={"scenario_id": scenario.scenario_id, "cause": cause.root_cause_type},
                )
    baseline = scenario.baseline_comparison
    if baseline.method != "prev_4_same_weekday" or baseline.offset_days != (-7, -14, -21, -28):
        raise ScenarioSpecError(
            "BASELINE_INVALID",
            "MetricRCA scenarios require the four previous same-weekday offsets",
            context={"scenario_id": scenario.scenario_id},
        )
    if not 0.0 <= baseline.minimum_effect_ratio <= 2.0:
        raise ScenarioSpecError("BASELINE_INVALID", "minimum effect ratio is outside [0,2]", context={"scenario_id": scenario.scenario_id})
    shock_ids = [shock.shock_id for shock in scenario.shocks]
    if len(shock_ids) != len(set(shock_ids)):
        raise ScenarioSpecError("SHOCK_ID_DUPLICATE", "shock ids must be unique within a scenario", context={"scenario_id": scenario.scenario_id})


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ScenarioSpecError("SCENARIO_FIELD_REQUIRED", f"{key} must be a non-empty string")
    return value.strip()


def _required_mapping(payload: Mapping[str, Any], key: str, *, scenario_id: str) -> Mapping[str, Any]:
    if key not in payload:
        raise ScenarioSpecError(
            "SCENARIO_FIELD_REQUIRED",
            f"{key} is required",
            context={"scenario_id": scenario_id, "field": key},
        )
    value = payload[key]
    if not isinstance(value, Mapping):
        raise ScenarioSpecError(
            "SCENARIO_FIELD_INVALID",
            f"{key} must be an object",
            context={"scenario_id": scenario_id, "field": key, "value": value},
        )
    return value


def _required_bool(payload: Mapping[str, Any], key: str, *, scenario_id: str) -> bool:
    if key not in payload:
        raise ScenarioSpecError(
            "SCENARIO_FIELD_REQUIRED",
            f"{key} is required",
            context={"scenario_id": scenario_id, "field": key},
        )
    value = payload[key]
    if not isinstance(value, bool):
        raise ScenarioSpecError(
            "SCENARIO_FIELD_INVALID",
            f"{key} must be boolean",
            context={"scenario_id": scenario_id, "field": key, "value": value},
        )
    return value


def _required_int(payload: Mapping[str, Any], key: str, *, scenario_id: str) -> int:
    if key not in payload:
        raise ScenarioSpecError(
            "SCENARIO_FIELD_REQUIRED",
            f"{key} is required",
            context={"scenario_id": scenario_id, "field": key},
        )
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ScenarioSpecError(
            "SCENARIO_FIELD_INVALID",
            f"{key} must be an integer",
            context={"scenario_id": scenario_id, "field": key, "value": value},
        )
    return value


def _required_int_tuple(payload: Mapping[str, Any], key: str, *, scenario_id: str) -> tuple[int, ...]:
    if key not in payload:
        raise ScenarioSpecError(
            "SCENARIO_FIELD_REQUIRED",
            f"{key} is required",
            context={"scenario_id": scenario_id, "field": key},
        )
    values = payload[key]
    if not isinstance(values, list) or not values:
        raise ScenarioSpecError(
            "SCENARIO_FIELD_INVALID",
            f"{key} must be a non-empty integer list",
            context={"scenario_id": scenario_id, "field": key},
        )
    return tuple(_required_int({"value": value}, "value", scenario_id=scenario_id) for value in values)


def _required_float(payload: Mapping[str, Any], key: str, *, scenario_id: str) -> float:
    if key not in payload:
        raise ScenarioSpecError(
            "SCENARIO_FIELD_REQUIRED",
            f"{key} is required",
            context={"scenario_id": scenario_id, "field": key},
        )
    value = payload[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ScenarioSpecError(
            "SCENARIO_FIELD_INVALID",
            f"{key} must be a finite number",
            context={"scenario_id": scenario_id, "field": key, "value": value},
        )
    return float(value)

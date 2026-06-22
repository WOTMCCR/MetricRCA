"""Strict schemas for MetricRCA's three-layer GRPO export."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import math
from typing import Any, Mapping


SCHEMA_VERSION = "metricrca-grpo-v2"


class TrajectoryLayer(StrEnum):
    CONTROLLER = "layer1_controller"
    SUB_AGENT = "layer2_sub_agent"
    CODING_FIX = "layer3_coding_fix"


class GrpoSchemaError(ValueError):
    def __init__(self, code: str, message: str, *, context: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.context = dict(context or {})
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class RewardComponent:
    name: str
    value: float
    weight: float = 1.0
    detail: str = ""

    def validate(self) -> None:
        if not self.name.strip():
            raise GrpoSchemaError("GRPO_REWARD_COMPONENT_INVALID", "reward component name must not be empty")
        if not math.isfinite(self.value) or not -1.0 <= self.value <= 1.0:
            raise GrpoSchemaError(
                "GRPO_REWARD_COMPONENT_INVALID",
                "reward component value must be finite and in [-1,1]",
                context={"name": self.name, "value": self.value},
            )
        if not math.isfinite(self.weight) or self.weight <= 0.0:
            raise GrpoSchemaError(
                "GRPO_REWARD_COMPONENT_INVALID",
                "reward component weight must be finite and positive",
                context={"name": self.name, "weight": self.weight},
            )


@dataclass(frozen=True)
class RewardRecord:
    total: float
    components: tuple[RewardComponent, ...]
    eligible_for_positive: bool
    exclusion_reason: str | None = None
    fix_effective: bool | None = None
    fix_minimal: bool | None = None
    fix_regressed: bool | None = None

    def validate(self, *, layer: TrajectoryLayer) -> None:
        if not math.isfinite(self.total) or not -1.0 <= self.total <= 1.0:
            raise GrpoSchemaError(
                "GRPO_REWARD_INVALID",
                "reward total must be finite and in [-1,1]",
                context={"total": self.total},
            )
        if not self.components:
            raise GrpoSchemaError("GRPO_REWARD_INVALID", "reward must contain at least one component")
        for component in self.components:
            component.validate()
        if self.eligible_for_positive and self.total <= 0.0:
            raise GrpoSchemaError(
                "GRPO_REWARD_INVALID",
                "positive eligibility requires a positive reward",
                context={"total": self.total},
            )
        if not self.eligible_for_positive and not self.exclusion_reason:
            raise GrpoSchemaError(
                "GRPO_REWARD_INVALID",
                "ineligible records require an exclusion reason",
            )
        fix_flags = (self.fix_effective, self.fix_minimal, self.fix_regressed)
        if layer == TrajectoryLayer.CODING_FIX and any(value is None for value in fix_flags):
            raise GrpoSchemaError(
                "GRPO_REWARD_INVALID",
                "coding-fix rewards require fix_effective/fix_minimal/fix_regressed",
            )
        if layer != TrajectoryLayer.CODING_FIX and any(value is not None for value in fix_flags):
            raise GrpoSchemaError(
                "GRPO_REWARD_INVALID",
                "fix assessment flags are only valid for coding-fix trajectories",
            )


@dataclass(frozen=True)
class TrajectoryRecord:
    trajectory_id: str
    layer: TrajectoryLayer
    cycle_id: str
    round: int
    source: dict[str, Any]
    input: dict[str, Any]
    trajectory: dict[str, Any]
    output: dict[str, Any]
    reward: RewardRecord
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise GrpoSchemaError(
                "GRPO_SCHEMA_VERSION_INVALID",
                "unsupported GRPO schema version",
                context={"schema_version": self.schema_version},
            )
        if not self.trajectory_id.strip():
            raise GrpoSchemaError("GRPO_TRAJECTORY_INVALID", "trajectory_id must not be empty")
        if not self.cycle_id.startswith("cycle-"):
            raise GrpoSchemaError("GRPO_TRAJECTORY_INVALID", "cycle_id must use cycle-* format")
        if self.round < 1:
            raise GrpoSchemaError("GRPO_TRAJECTORY_INVALID", "round must be positive")
        for field_name, value in (
            ("source", self.source),
            ("input", self.input),
            ("trajectory", self.trajectory),
            ("output", self.output),
            ("metadata", self.metadata),
        ):
            if not isinstance(value, dict):
                raise GrpoSchemaError(
                    "GRPO_TRAJECTORY_INVALID",
                    f"{field_name} must be an object",
                )
        _validate_layer_contract(self)
        self.reward.validate(layer=self.layer)

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["layer"] = self.layer.value
        return payload


def validate_record_dict(payload: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "trajectory_id",
        "layer",
        "cycle_id",
        "round",
        "source",
        "input",
        "trajectory",
        "output",
        "reward",
        "metadata",
    }
    missing = sorted(required - set(payload))
    extra = sorted(set(payload) - required)
    if missing or extra:
        raise GrpoSchemaError(
            "GRPO_TRAJECTORY_INVALID",
            "trajectory keys do not match the strict schema",
            context={"missing": missing, "extra": extra},
        )
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise GrpoSchemaError(
            "GRPO_SCHEMA_VERSION_INVALID",
            "unsupported GRPO schema version",
            context={"schema_version": payload.get("schema_version")},
        )
    for field_name in ("trajectory_id", "cycle_id"):
        if not isinstance(payload.get(field_name), str):
            raise GrpoSchemaError("GRPO_TRAJECTORY_INVALID", f"{field_name} must be a string")
    if not isinstance(payload.get("round"), int) or isinstance(payload.get("round"), bool):
        raise GrpoSchemaError("GRPO_TRAJECTORY_INVALID", "round must be an integer")
    for field_name in ("source", "input", "trajectory", "output", "metadata"):
        if not isinstance(payload.get(field_name), Mapping):
            raise GrpoSchemaError("GRPO_TRAJECTORY_INVALID", f"{field_name} must be an object")
    try:
        layer = TrajectoryLayer(str(payload["layer"]))
    except ValueError as exc:
        raise GrpoSchemaError(
            "GRPO_TRAJECTORY_INVALID",
            "unknown trajectory layer",
            context={"layer": payload.get("layer")},
        ) from exc
    reward_payload = payload["reward"]
    if not isinstance(reward_payload, Mapping):
        raise GrpoSchemaError("GRPO_REWARD_INVALID", "reward must be an object")
    reward_required = {
        "total",
        "components",
        "eligible_for_positive",
        "exclusion_reason",
        "fix_effective",
        "fix_minimal",
        "fix_regressed",
    }
    reward_missing = sorted(reward_required - set(reward_payload))
    reward_extra = sorted(set(reward_payload) - reward_required)
    if reward_missing or reward_extra:
        raise GrpoSchemaError(
            "GRPO_REWARD_INVALID",
            "reward keys do not match the strict schema",
            context={"missing": reward_missing, "extra": reward_extra},
        )
    if not isinstance(reward_payload.get("eligible_for_positive"), bool):
        raise GrpoSchemaError("GRPO_REWARD_INVALID", "eligible_for_positive must be boolean")
    components_payload = reward_payload.get("components")
    if not isinstance(components_payload, (list, tuple)):
        raise GrpoSchemaError("GRPO_REWARD_INVALID", "reward.components must be a list")
    components = tuple(_component_from_payload(component) for component in components_payload)
    reward = RewardRecord(
        total=_float_field(reward_payload, "total"),
        components=components,
        eligible_for_positive=reward_payload["eligible_for_positive"],
        exclusion_reason=(
            str(reward_payload["exclusion_reason"])
            if reward_payload.get("exclusion_reason") is not None
            else None
        ),
        fix_effective=_optional_bool(reward_payload.get("fix_effective")),
        fix_minimal=_optional_bool(reward_payload.get("fix_minimal")),
        fix_regressed=_optional_bool(reward_payload.get("fix_regressed")),
    )
    record = TrajectoryRecord(
        schema_version=payload["schema_version"],
        trajectory_id=payload["trajectory_id"],
        layer=layer,
        cycle_id=payload["cycle_id"],
        round=payload["round"],
        source=dict(payload["source"]),
        input=dict(payload["input"]),
        trajectory=dict(payload["trajectory"]),
        output=dict(payload["output"]),
        reward=reward,
        metadata=dict(payload["metadata"]),
    )
    record.validate()


def _validate_layer_contract(record: TrajectoryRecord) -> None:
    required_by_layer = {
        TrajectoryLayer.CONTROLLER: {
            "input": {"optimization_context"},
            "trajectory": {"controller_rules"},
            "output": {"decision"},
        },
        TrajectoryLayer.SUB_AGENT: {
            "input": {"case_id", "trajectory_type"},
            "trajectory": {"steps"},
            "output": {"result"},
        },
        TrajectoryLayer.CODING_FIX: {
            "input": {"diagnosis", "before"},
            "trajectory": {"git_diff", "changed_files"},
            "output": {"after", "fix_assessment"},
        },
    }
    contract = required_by_layer[record.layer]
    values = {
        "input": record.input,
        "trajectory": record.trajectory,
        "output": record.output,
    }
    for section, required in contract.items():
        missing = sorted(required - set(values[section]))
        if missing:
            raise GrpoSchemaError(
                "GRPO_LAYER_CONTRACT_INVALID",
                f"{record.layer.value} {section} is missing required fields",
                context={"missing": missing},
            )


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise GrpoSchemaError("GRPO_REWARD_INVALID", "optional reward flags must be booleans")
    return value


def _component_from_payload(component: Any) -> RewardComponent:
    if not isinstance(component, Mapping):
        raise GrpoSchemaError("GRPO_REWARD_INVALID", "reward components must be objects")
    required = {"name", "value", "weight", "detail"}
    missing = sorted(required - set(component))
    extra = sorted(set(component) - required)
    if missing or extra:
        raise GrpoSchemaError(
            "GRPO_REWARD_COMPONENT_INVALID",
            "reward component keys do not match the strict schema",
            context={"missing": missing, "extra": extra},
        )
    if not isinstance(component.get("name"), str) or not isinstance(component.get("detail"), str):
        raise GrpoSchemaError("GRPO_REWARD_COMPONENT_INVALID", "reward component name/detail must be strings")
    return RewardComponent(
        name=component["name"],
        value=_float_field(component, "value"),
        weight=_float_field(component, "weight"),
        detail=component["detail"],
    )


def _float_field(payload: Mapping[str, Any], field: str) -> float:
    value = payload.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise GrpoSchemaError("GRPO_REWARD_INVALID", f"{field} must be numeric")
    return float(value)

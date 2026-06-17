"""Compatibility tool wrappers over the deterministic runtime registry."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from metric_rca.domain.models import Observation
from metric_rca.runtime.sdk_tools import (
    RCA_TOOL_NAMES,
    MetricRCAToolHandler,
    ToolExecutionResult,
    _coerce_tool_result,
    build_default_tool_handlers,
)


RANK_TOOL_NAME = "rank_root_causes"
TOOL_REGISTRY = MappingProxyType(build_default_tool_handlers())
TOOL_ARG_SCHEMAS = MappingProxyType({name: spec.args_model for name, spec in TOOL_REGISTRY.items()})
DATA_FETCHING_TOOLS = frozenset(
    {"detect_anomaly", "drilldown_dimension", "fetch_related_signal", "calculate_contribution"}
)
WHITELISTED_TOOL_NAMES = RCA_TOOL_NAMES
EXPOSED_TOOL_NAMES = RCA_TOOL_NAMES


@dataclass(frozen=True)
class MetricRCATool:
    name: str
    handler: MetricRCAToolHandler
    dependencies: Any
    run_id: str

    def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(args)
        resolved["run_id"] = self.run_id
        try:
            typed_args = self.handler.args_model.model_validate(resolved)
        except ValidationError as exc:
            result = ToolExecutionResult(
                observation=Observation(
                    action_name=self.name,
                    ok=False,
                    error_code="ACTION_SCHEMA_INVALID",
                    message=exc.errors()[0]["msg"],
                )
            )
        else:
            result = _coerce_tool_result(self.handler.call(typed_args, self.dependencies))
        return {
            "observation": result.observation.model_dump(mode="json"),
            "evidence_ids": result.evidence_ids,
            "candidates": [candidate.model_dump(mode="json") for candidate in result.candidates],
        }


def build_metric_rca_tools(*, dependencies: Any, run_id: str) -> list[MetricRCATool]:
    return [
        MetricRCATool(name=name, handler=handler, dependencies=dependencies, run_id=run_id)
        for name, handler in TOOL_REGISTRY.items()
    ]

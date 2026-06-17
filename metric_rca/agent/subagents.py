"""P9 multi-agent routing and expert contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from metric_rca.agent.prompts import EXPERT_SYSTEM_PROMPT
from metric_rca.domain.models import StrictModel


EXPERT_FAMILIES = frozenset({"gmv_family", "rate_family"})


class SubagentScopeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class RunOutcome(StrictModel):
    status: Literal["succeeded", "no_anomaly", "failed", "running"]
    metric_id: str
    dimension: str | None = None
    element: str | None = None
    root_cause_type: str | None = None
    verdict: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    reflection_notes: list[str] = Field(default_factory=list)


def route_metric_family(metric_id: str, *, metric_service: Any) -> str:
    definition = metric_service.get_metric_definition(metric_id)
    family = getattr(definition, "metric_family", None)
    if family in EXPERT_FAMILIES:
        return str(family)
    raise SubagentScopeError("METRIC_METADATA_INVALID", f"metric has no expert family: {metric_id}")


def build_subagents(*, settings: Any, tools: list[Any], middleware: list[Any]) -> list[dict[str, Any]]:
    if not settings.multi_agent_enabled:
        return []
    if not tools:
        raise SubagentScopeError("AGENT_INVOKE_FAILED", "multi-agent experts require shared tools")
    if not middleware:
        raise SubagentScopeError("AGENT_INVOKE_FAILED", "multi-agent experts require shared middleware")
    return [
        {
            "name": "gmv_family_expert",
            "family": "gmv_family",
            "system_prompt": _family_prompt("gmv_family"),
            "response_format": None,
        },
        {
            "name": "rate_family_expert",
            "family": "rate_family",
            "system_prompt": _family_prompt("rate_family"),
            "response_format": None,
        },
    ]


def _family_prompt(family: str) -> str:
    if family == "gmv_family":
        guidance = """
Family guidance for gmv_family:
- Treat GMV as UV x CVR x AOV when interpreting contribution evidence.
- For net_gmv, preserve the net_gmv = gmv - refund_amount chain and validate the dominant side with tools.
- For AOV/product-first cases, use product inventory/merchandise evidence before concluding aov_drop.
"""
    elif family == "rate_family":
        guidance = """
Family guidance for rate_family:
- Direction matters: pay_cvr down is bad; refund_rate, stockout_rate, and complaint_rate up are bad.
- Broad pay_cvr or conversion-rate questions should inspect device first and use conversion evidence for the strongest device candidate.
- Broad refund_rate questions should inspect product first and use refund_quality evidence for the strongest product candidate.
- Refund and complaint cases should prefer refund_quality evidence for product/category quality issues.
- Stockout-rate cases should prefer inventory/warehouse evidence.
"""
    else:
        raise SubagentScopeError("METRIC_NOT_FOUND", f"unknown expert family: {family}")
    return f"{EXPERT_SYSTEM_PROMPT}\n\n{guidance}\nReturn a structured RunOutcome after the tool loop completes."

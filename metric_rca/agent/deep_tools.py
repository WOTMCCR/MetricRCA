"""LangChain tool wrappers over the deterministic MetricRCA tool layer."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import Field

from metric_rca.agent.tools.calculate_contribution import calculate_contribution as _calculate_contribution
from metric_rca.agent.tools.detect_anomaly import detect_anomaly as _detect_anomaly
from metric_rca.agent.tools.drilldown_dimension import drilldown_dimension as _drilldown_dimension
from metric_rca.agent.tools.fetch_related_signal import fetch_related_signal as _fetch_related_signal
from metric_rca.agent.tools.schemas import (
    CalculateContributionArgs,
    DetectAnomalyArgs,
    DrilldownDimensionArgs,
    FetchRelatedSignalArgs,
)
from metric_rca.domain.models import Evidence, Observation, QuerySpec, RootCauseCandidate, StrictModel, TimeRange


DATA_FETCHING_TOOLS = frozenset(
    {
        "detect_anomaly",
        "drilldown_dimension",
        "fetch_related_signal",
        "calculate_contribution",
    }
)
PLANNING_TOOL_NAME = "write_todos"
RANK_TOOL_NAME = "rank_root_causes"
WHITELISTED_TOOL_NAMES = DATA_FETCHING_TOOLS | {RANK_TOOL_NAME}
EXPOSED_TOOL_NAMES = WHITELISTED_TOOL_NAMES | {PLANNING_TOOL_NAME}


class DetectAnomalyIn(StrictModel):
    metric_id: str
    target_date: date
    filters: dict[str, str] = Field(default_factory=dict)


class DrilldownDimensionIn(StrictModel):
    metric_id: str
    target_date: date
    dimension: str
    evidence_ids: list[str]
    filters: dict[str, str] = Field(default_factory=dict)


class FetchRelatedSignalIn(StrictModel):
    metric_id: str
    target_date: date
    signal_type: Literal["campaign", "inventory", "conversion", "refund_quality"]
    dimension: str
    element: str
    evidence_ids: list[str]


class CalculateContributionIn(StrictModel):
    metric_id: str
    target_date: date
    dimension: str
    element: str
    evidence_ids: list[str]
    filters: dict[str, str] = Field(default_factory=dict)


class RankRootCausesIn(StrictModel):
    metric_id: str
    target_date: date


class ToolObservationOut(StrictModel):
    observation: Observation
    evidence_ids: list[str] = Field(default_factory=list)
    candidates: list[RootCauseCandidate] = Field(default_factory=list)


def build_metric_rca_tools(*, dependencies: Any, run_id: str) -> list[StructuredTool]:
    """Build deepagents-visible tools.

    The LLM supplies business arguments only. The run id and repositories are
    bound at the factory boundary so they are not prompt-editable.
    """

    def detect_anomaly(metric_id: str, target_date: date, filters: dict[str, str] | None = None) -> dict[str, Any]:
        _set_run_context(dependencies=dependencies, run_id=run_id, metric_id=metric_id, target_date=target_date)
        result = _detect_anomaly(
            DetectAnomalyArgs(
                run_id=run_id,
                metric_id=metric_id,
                target_date=target_date,
                filters=filters or {},
            ),
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
            settings=dependencies.settings,
        )
        return _dump_tool_result(result)

    def drilldown_dimension(
        metric_id: str,
        target_date: date,
        dimension: str,
        evidence_ids: list[str],
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _set_run_context(dependencies=dependencies, run_id=run_id, metric_id=metric_id, target_date=target_date)
        result = _drilldown_dimension(
            DrilldownDimensionArgs(
                run_id=run_id,
                metric_id=metric_id,
                target_date=target_date,
                dimension=dimension,
                evidence_ids=evidence_ids,
                filters=filters or {},
            ),
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
        )
        return _dump_tool_result(result)

    def fetch_related_signal(
        metric_id: str,
        target_date: date,
        signal_type: str,
        dimension: str,
        element: str,
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        _set_run_context(dependencies=dependencies, run_id=run_id, metric_id=metric_id, target_date=target_date)
        result = _fetch_related_signal(
            FetchRelatedSignalArgs(
                run_id=run_id,
                metric_id=metric_id,
                target_date=target_date,
                signal_type=signal_type,
                dimension=dimension,
                element=element,
                evidence_ids=evidence_ids,
            ),
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
            settings=dependencies.settings,
        )
        return _dump_tool_result(result)

    def calculate_contribution(
        metric_id: str,
        target_date: date,
        dimension: str,
        element: str,
        evidence_ids: list[str],
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _set_run_context(dependencies=dependencies, run_id=run_id, metric_id=metric_id, target_date=target_date)
        result = _calculate_contribution(
            CalculateContributionArgs(
                run_id=run_id,
                metric_id=metric_id,
                target_date=target_date,
                dimension=dimension,
                element=element,
                evidence_ids=evidence_ids,
                filters=filters or {},
            ),
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
        )
        return _dump_tool_result(result)

    def rank_root_causes(metric_id: str, target_date: date) -> dict[str, Any]:
        _set_run_context(dependencies=dependencies, run_id=run_id, metric_id=metric_id, target_date=target_date)
        result = _rank_from_persisted_e4(
            repository=dependencies.repository,
            run_id=run_id,
            metric_id=metric_id,
            target_date=target_date,
        )
        return result.model_dump(mode="json")

    return [
        StructuredTool.from_function(
            detect_anomaly,
            name="detect_anomaly",
            description="Detect whether a metric is anomalous on a target date.",
            args_schema=DetectAnomalyIn,
        ),
        StructuredTool.from_function(
            drilldown_dimension,
            name="drilldown_dimension",
            description="Drill down an anomalous metric by one allowed dimension.",
            args_schema=DrilldownDimensionIn,
        ),
        StructuredTool.from_function(
            fetch_related_signal,
            name="fetch_related_signal",
            description="Fetch a related signal for the selected root-cause element.",
            args_schema=FetchRelatedSignalIn,
        ),
        StructuredTool.from_function(
            calculate_contribution,
            name="calculate_contribution",
            description="Calculate final contribution and bind E4 evidence.",
            args_schema=CalculateContributionIn,
        ),
        StructuredTool.from_function(
            rank_root_causes,
            name="rank_root_causes",
            description="Rank root-cause candidates from persisted current-run evidence.",
            args_schema=RankRootCausesIn,
        ),
    ]


def _set_run_context(*, dependencies: Any, run_id: str, metric_id: str, target_date: date) -> None:
    writer = getattr(dependencies, "trace_writer", None)
    if writer is not None:
        writer.set_run_context(run_id=run_id, metric_id=metric_id, target_date=target_date)


def _dump_tool_result(result: Any) -> dict[str, Any]:
    observation = result.observation
    return ToolObservationOut(
        observation=observation,
        evidence_ids=list(observation.evidence_ids),
        candidates=list(result.candidates),
    ).model_dump(mode="json")


def _rank_from_persisted_e4(*, repository: Any, run_id: str, metric_id: str, target_date: date) -> ToolObservationOut:
    e4_id = f"{run_id}:E4"
    e4 = repository.get_evidence(run_id=run_id, evidence_id=e4_id)
    if e4 is None:
        return ToolObservationOut(
            observation=Observation(
                action_name=RANK_TOOL_NAME,
                ok=False,
                error_code="ATTRIBUTION_COVERAGE_LOW",
                message="E4 evidence is required before ranking",
            )
        )
    candidates = [
        RootCauseCandidate.model_validate(candidate)
        for candidate in (e4.get("result_summary") or {}).get("candidates", [])
    ]
    if not candidates:
        selected = (e4.get("result_summary") or {}).get("selected_candidate")
        if isinstance(selected, dict):
            candidates = [RootCauseCandidate.model_validate(selected)]
    if not candidates:
        return ToolObservationOut(
            observation=Observation(
                action_name=RANK_TOOL_NAME,
                ok=False,
                error_code="ATTRIBUTION_COVERAGE_LOW",
                message="persisted E4 has no candidates",
            )
        )
    evidence = Evidence(
        evidence_id=f"{run_id}:E_rank",
        query_spec=QuerySpec(
            metric_id=metric_id,
            time_range=TimeRange(start_date=target_date, end_date=target_date),
            purpose="current",
        ),
        sql=e4["sql_text"],
        sql_hash=e4["sql_hash"],
        guard_status=e4["guard_status"],
        result_summary={"metric_id": metric_id, "candidates": [c.model_dump(mode="json") for c in candidates]},
        data_source=e4["data_source"],
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    repository.create_evidence(
        {
            "evidence_id": evidence.evidence_id,
            "run_id": run_id,
            "query_spec": evidence.query_spec.model_dump(mode="json"),
            "sql_text": evidence.sql,
            "sql_hash": evidence.sql_hash,
            "guard_status": evidence.guard_status,
            "result_summary": evidence.result_summary,
            "data_source": evidence.data_source,
            "created_at": evidence.created_at,
        }
    )
    return ToolObservationOut(
        observation=Observation(
            action_name=RANK_TOOL_NAME,
            ok=True,
            payload={"candidates": [c.model_dump(mode="json") for c in candidates]},
            evidence_ids=[evidence.evidence_id],
        ),
        evidence_ids=[evidence.evidence_id],
        candidates=candidates,
    )

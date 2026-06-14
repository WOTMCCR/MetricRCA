"""LangChain tool wrappers over the deterministic MetricRCA tool layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import MappingProxyType
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
from metric_rca.services.adtributor_service import AdtributorElement, attribute_elements
from metric_rca.services.attribution_service import rank_root_causes as _rank_candidates


PLANNING_TOOL_NAME = "write_todos"
RANK_TOOL_NAME = "rank_root_causes"


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
    filters: dict[str, str] = Field(default_factory=dict)


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


@dataclass(frozen=True)
class MetricRCAToolSpec:
    name: str
    args_schema: type[StrictModel]
    data_fetching: bool = False


TOOL_REGISTRY = MappingProxyType(
    {
        "detect_anomaly": MetricRCAToolSpec("detect_anomaly", DetectAnomalyIn, data_fetching=True),
        "drilldown_dimension": MetricRCAToolSpec("drilldown_dimension", DrilldownDimensionIn, data_fetching=True),
        "fetch_related_signal": MetricRCAToolSpec("fetch_related_signal", FetchRelatedSignalIn, data_fetching=True),
        "calculate_contribution": MetricRCAToolSpec("calculate_contribution", CalculateContributionIn, data_fetching=True),
        "rank_root_causes": MetricRCAToolSpec("rank_root_causes", RankRootCausesIn, data_fetching=False),
    }
)
TOOL_ARG_SCHEMAS = MappingProxyType({name: spec.args_schema for name, spec in TOOL_REGISTRY.items()})
DATA_FETCHING_TOOLS = frozenset(name for name, spec in TOOL_REGISTRY.items() if spec.data_fetching)
WHITELISTED_TOOL_NAMES = frozenset(TOOL_REGISTRY)
EXPOSED_TOOL_NAMES = WHITELISTED_TOOL_NAMES | {PLANNING_TOOL_NAME}


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
        filters: dict[str, str] | None = None,
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
                filters=filters or {},
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
            settings=dependencies.settings,
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


def _rank_from_persisted_e4(
    *,
    repository: Any,
    settings: Any,
    run_id: str,
    metric_id: str,
    target_date: date,
) -> ToolObservationOut:
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
    candidates = _enhance_with_adtributor(
        repository=repository,
        settings=settings,
        run_id=run_id,
        metric_id=metric_id,
        candidates=candidates,
    )
    candidates = _rank_candidates(candidates)
    selected_candidate = candidates[0]
    sql_text = e4.get("sql_text")
    if not sql_text:
        return ToolObservationOut(
            observation=Observation(
                action_name=RANK_TOOL_NAME,
                ok=False,
                error_code="EVIDENCE_MISSING",
                message="persisted E4 sql_text is required before ranking",
            )
        )
    e4_summary = dict(e4.get("result_summary") or {})
    e4_summary["selected_candidate"] = selected_candidate.model_dump(mode="json")
    e4_summary["candidates"] = [candidate.model_dump(mode="json") for candidate in candidates]
    e4_summary["ranker"] = "adtributor_internal" if any(c.explanatory_power is not None for c in candidates) else "v1"
    _update_e4_summary(repository=repository, run_id=run_id, evidence_id=e4_id, result_summary=e4_summary)
    evidence = Evidence(
        evidence_id=f"{run_id}:E_rank",
        query_spec=QuerySpec(
            metric_id=metric_id,
            time_range=TimeRange(start_date=target_date, end_date=target_date),
            purpose="current",
        ),
        sql=sql_text,
        sql_hash=e4["sql_hash"],
        guard_status=e4["guard_status"],
        result_summary={
            "metric_id": metric_id,
            "ranker": e4_summary["ranker"],
            "selected_candidate": selected_candidate.model_dump(mode="json"),
            "candidates": [c.model_dump(mode="json") for c in candidates],
        },
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
            payload={
                "ranker": e4_summary["ranker"],
                "selected_candidate": selected_candidate.model_dump(mode="json"),
                "candidates": [c.model_dump(mode="json") for c in candidates],
            },
            evidence_ids=[evidence.evidence_id],
        ),
        evidence_ids=[evidence.evidence_id],
        candidates=candidates,
    )


def _enhance_with_adtributor(
    *,
    repository: Any,
    settings: Any,
    run_id: str,
    metric_id: str,
    candidates: list[RootCauseCandidate],
) -> list[RootCauseCandidate]:
    elements = _adtributor_elements_from_persisted_evidence(repository=repository, run_id=run_id)
    if not elements:
        return candidates
    result = attribute_elements(
        metric_id=metric_id,
        elements=elements,
        t_ep=float(getattr(settings, "adtributor_t_ep", 0.67)),
        t_eep=float(getattr(settings, "adtributor_t_eep", 0.10)),
    )
    if not result.ok:
        return candidates
    score_by_pair = {
        (score.dimension, str(score.element)): score
        for score in result.element_scores
        if score.explanatory_power > 0
    }
    if not score_by_pair:
        return candidates
    top_pair_by_dimension: dict[str, tuple[str, str]] = {}
    for pair, score in score_by_pair.items():
        previous = top_pair_by_dimension.get(pair[0])
        if previous is None or _adtributor_pair_rank(score) > _adtributor_pair_rank(score_by_pair[previous]):
            top_pair_by_dimension[pair[0]] = pair
    selected_pairs_by_dimension: dict[str, list[tuple[str, str]]] = {}
    for adtributor_candidate in result.candidates:
        for dimension, element in adtributor_candidate.dimension_elements:
            pair = (dimension, str(element))
            selected_pairs_by_dimension.setdefault(dimension, [])
            if pair not in selected_pairs_by_dimension[dimension]:
                selected_pairs_by_dimension[dimension].append(pair)

    enhanced: list[RootCauseCandidate] = []
    for candidate in candidates:
        pairs = list(candidate.dimension_elements)
        if candidate.dimension is not None and candidate.element is not None:
            pair = (candidate.dimension, str(candidate.element))
            if pair not in pairs:
                pairs.insert(0, pair)
            for selected_pair in selected_pairs_by_dimension.get(candidate.dimension, []):
                if selected_pair not in pairs:
                    pairs.append(selected_pair)
        for pair in top_pair_by_dimension.values():
            if pair not in pairs:
                pairs.append(pair)
        pair_scores = [score_by_pair[pair] for pair in pairs if pair in score_by_pair]
        if not pair_scores:
            enhanced.append(candidate)
            continue
        explanatory_power = min(1.0, sum(score.explanatory_power for score in pair_scores))
        surprise_js = sum(score.surprise_js for score in pair_scores)
        evidence_ids = [*candidate.evidence_ids]
        e_rank_id = f"{run_id}:E_rank"
        if e_rank_id not in evidence_ids:
            evidence_ids.append(e_rank_id)
        enhanced.append(
            candidate.model_copy(
                update={
                    "dimension_elements": pairs,
                    "explanatory_power": explanatory_power,
                    "surprise_js": surprise_js,
                    "contribution_pct": explanatory_power,
                    "eng_confidence": explanatory_power
                    * candidate.signal_severity
                    * candidate.evidence_support
                    * candidate.reflection_factor,
                    "evidence_ids": evidence_ids,
                }
            )
        )
    return enhanced


def _adtributor_pair_rank(score: Any) -> tuple[float, float]:
    return (float(score.explanatory_power), float(score.surprise_js))


def _adtributor_elements_from_persisted_evidence(*, repository: Any, run_id: str) -> list[AdtributorElement]:
    rows = repository.get_evidences(run_id) if hasattr(repository, "get_evidences") else []
    if not rows:
        row = repository.get_evidence(run_id=run_id, evidence_id=f"{run_id}:E2")
        rows = [row] if row is not None else []
    elements: list[AdtributorElement] = []
    for row in rows:
        summary = row.get("result_summary") if isinstance(row, dict) else None
        raw_elements = summary.get("adtributor_elements") if isinstance(summary, dict) else None
        if not isinstance(raw_elements, list):
            continue
        for raw_element in raw_elements:
            if isinstance(raw_element, dict):
                elements.append(AdtributorElement.model_validate(raw_element))
    return elements


def _update_e4_summary(*, repository: Any, run_id: str, evidence_id: str, result_summary: dict[str, Any]) -> None:
    if hasattr(repository, "update_evidence_result_summary"):
        repository.update_evidence_result_summary(run_id=run_id, evidence_id=evidence_id, result_summary=result_summary)
        return
    row = repository.get_evidence(run_id=run_id, evidence_id=evidence_id)
    if isinstance(row, dict):
        row["result_summary"] = result_summary

"""OpenAI Agents SDK tool registry and deterministic tool executor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from pydantic import ValidationError

from metric_rca.domain.models import Evidence, Observation, QuerySpec, RootCauseCandidate, StrictModel, TimeRange
from metric_rca.services.adtributor_service import AdtributorElement, attribute_elements
from metric_rca.services.attribution_service import rank_root_causes as _rank_candidates
from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_models import RcaAction
from metric_rca.runtime.run_context import RunContext


RCA_TOOL_NAMES = frozenset(
    {"detect_anomaly", "drilldown_dimension", "fetch_related_signal", "calculate_contribution", "rank_root_causes"}
)
EVIDENCE_INPUT_ACTIONS = frozenset(
    {"drilldown_dimension", "fetch_related_signal", "calculate_contribution"}
)
INTERNAL_ACTION_ARG_NAMES = frozenset({"element_selection"})
ELEMENT_SELECTION_SIGNAL_ANOMALY = "signal_anomaly"
ELEMENT_SELECTION_SIGNAL_LEVEL = "signal_level"


@dataclass(frozen=True)
class MetricRCAToolHandler:
    args_model: type[StrictModel]
    call: Callable[[Any, Any], Any]


class ToolExecutionResult(StrictModel):
    observation: Observation
    evidence_ids: list[str] = []
    candidates: list[RootCauseCandidate] = []


class RankRootCausesArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date


class ToolExecutor:
    def __init__(
        self,
        *,
        dependencies: Any,
        handlers: Mapping[str, MetricRCAToolHandler] | None = None,
    ) -> None:
        self._dependencies = dependencies
        self._handlers = dict(handlers or build_default_tool_handlers())

    def execute(self, ctx: RunContext, action: RcaAction, evidence_graph: EvidenceGraph) -> ToolExecutionResult:
        handler = self._handlers.get(action.kind)
        if handler is None:
            return _error(action.kind, "TOOL_NOT_REGISTERED", f"tool is not registered: {action.kind}")

        resolved_args, error = _resolve_action_args(ctx, action, evidence_graph, self._dependencies)
        if error is not None:
            return error

        try:
            typed_args = handler.args_model.model_validate(resolved_args)
        except ValidationError as exc:
            return _error(action.kind, "ACTION_SCHEMA_INVALID", exc.errors()[0]["msg"])

        result = handler.call(typed_args, self._dependencies)
        return _coerce_tool_result(result)


def build_default_tool_handlers() -> dict[str, MetricRCAToolHandler]:
    from metric_rca.agent.tools.calculate_contribution import calculate_contribution
    from metric_rca.agent.tools.detect_anomaly import detect_anomaly
    from metric_rca.agent.tools.drilldown_dimension import drilldown_dimension
    from metric_rca.agent.tools.fetch_related_signal import fetch_related_signal
    from metric_rca.agent.tools.schemas import (
        CalculateContributionArgs,
        DetectAnomalyArgs,
        DrilldownDimensionArgs,
        FetchRelatedSignalArgs,
    )

    def _detect(args: DetectAnomalyArgs, dependencies: Any) -> Any:
        return detect_anomaly(
            args,
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
            settings=dependencies.settings,
        )

    def _drilldown(args: DrilldownDimensionArgs, dependencies: Any) -> Any:
        return drilldown_dimension(
            args,
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
        )

    def _fetch(args: FetchRelatedSignalArgs, dependencies: Any) -> Any:
        return fetch_related_signal(
            args,
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
            settings=dependencies.settings,
        )

    def _calculate(args: CalculateContributionArgs, dependencies: Any) -> Any:
        return calculate_contribution(
            args,
            repository=dependencies.repository,
            metric_service=dependencies.metric_service,
            renderer=dependencies.renderer,
        )

    def _rank(args: RankRootCausesArgs, dependencies: Any) -> ToolExecutionResult:
        return _rank_from_persisted_e4(
            repository=dependencies.repository,
            settings=dependencies.settings,
            run_id=args.run_id,
            metric_id=args.metric_id,
            target_date=args.target_date,
        )

    return {
        "detect_anomaly": MetricRCAToolHandler(args_model=DetectAnomalyArgs, call=_detect),
        "drilldown_dimension": MetricRCAToolHandler(args_model=DrilldownDimensionArgs, call=_drilldown),
        "fetch_related_signal": MetricRCAToolHandler(args_model=FetchRelatedSignalArgs, call=_fetch),
        "calculate_contribution": MetricRCAToolHandler(args_model=CalculateContributionArgs, call=_calculate),
        "rank_root_causes": MetricRCAToolHandler(args_model=RankRootCausesArgs, call=_rank),
    }


def _resolve_action_args(
    ctx: RunContext,
    action: RcaAction,
    evidence_graph: EvidenceGraph,
    dependencies: Any,
) -> tuple[dict[str, Any], ToolExecutionResult | None]:
    args = dict(action.args)
    args["run_id"] = ctx.run_id
    if action.kind in EVIDENCE_INPUT_ACTIONS and "evidence_ids" not in args:
        args["evidence_ids"] = _required_evidence_ids(action, evidence_graph)
    if action.dynamic and args.get("element") is None:
        element, resolution_error = _dynamic_candidate_element(ctx, args, dependencies)
        if resolution_error is not None:
            return args, resolution_error
        if element is None:
            return args, _error(
                action.kind,
                "DYNAMIC_ACTION_UNRESOLVED",
                f"action {action.action_id} could not resolve element from top drilldown candidate",
            )
        args["element"] = element
    for name in INTERNAL_ACTION_ARG_NAMES:
        args.pop(name, None)
    return args, None


def _dynamic_candidate_element(
    ctx: RunContext,
    args: dict[str, Any],
    dependencies: Any,
) -> tuple[str | None, ToolExecutionResult | None]:
    signal_element = _signal_evidence_element(ctx, str(args.get("dimension") or ""))
    if signal_element is not None:
        return signal_element, None
    if args.get("element_selection") == ELEMENT_SELECTION_SIGNAL_ANOMALY:
        selected, error = _top_signal_anomaly_element(ctx=ctx, args=args, dependencies=dependencies)
        if error is not None:
            return selected, error
        if selected is None:
            return None, _error(
                "fetch_related_signal",
                "SIGNAL_SELECTION_UNRESOLVED",
                "signal-anomaly element selection found no scored drilldown candidate",
            )
        return selected, None
    if args.get("element_selection") == ELEMENT_SELECTION_SIGNAL_LEVEL:
        selected, error = _top_signal_level_element(ctx=ctx, args=args, dependencies=dependencies)
        if error is not None:
            return selected, error
        if selected is None:
            return None, _error(
                "fetch_related_signal",
                "SIGNAL_SELECTION_UNRESOLVED",
                "signal-level element selection found no scored drilldown candidate",
            )
        return selected, None
    if args.get("signal_type") == "refund_quality":
        selected, error = _top_signal_level_element(ctx=ctx, args=args, dependencies=dependencies)
        if error is not None or selected is not None:
            return selected, error
    return _top_candidate_element(ctx, str(args.get("dimension") or "")), None


def _required_evidence_ids(action: RcaAction, evidence_graph: EvidenceGraph) -> list[str]:
    evidence_ids: list[str] = []
    for alias in action.requires:
        for evidence_id in evidence_graph.matching(alias):
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
    return evidence_ids


def _top_signal_anomaly_element(
    *,
    ctx: RunContext,
    args: dict[str, Any],
    dependencies: Any,
) -> tuple[str | None, ToolExecutionResult | None]:
    repository = ctx.repository
    dimension = str(args.get("dimension") or "")
    if repository is None or not dimension:
        return None, None
    candidate_elements = _candidate_elements(ctx, dimension)
    if not candidate_elements:
        return None, None
    settings = getattr(dependencies, "settings", None)
    signal_metric_by_type = getattr(settings, "signal_metric_by_type", {}) if settings is not None else {}
    signal_type = str(args.get("signal_type") or "")
    signal_metric_id = signal_metric_by_type.get(signal_type)
    if not signal_metric_id:
        return None, _error(
            "fetch_related_signal",
            "SIGNAL_POLICY_MISSING",
            f"{signal_type or 'unknown'} signal metric is not configured",
        )
    metric_service = getattr(dependencies, "metric_service", None)
    renderer = getattr(dependencies, "renderer", None)
    if metric_service is None or renderer is None:
        return None, _error(
            "fetch_related_signal",
            "CONFIG_INVALID",
            "signal element selection requires metric_service and renderer",
        )
    try:
        from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
        from metric_rca.guardrails.sql_guard import guard_sql
        from metric_rca.services.anomaly_service import detect_anomaly_from_rows
        from metric_rca.services.metric_contracts import MetricServiceError

        metric_definition = metric_service.get_metric_definition(str(signal_metric_id))
    except QuerySpecError as exc:
        return None, _error("fetch_related_signal", exc.code, str(exc))
    except MetricServiceError as exc:
        return None, _error("fetch_related_signal", exc.code, str(exc))
    except RuntimeError as exc:
        return None, _error("fetch_related_signal", _runtime_code(exc), str(exc))

    ranked: list[tuple[str, tuple[int, int, float, float]]] = []
    for element in candidate_elements:
        filters = _string_filters(args.get("filters"))
        filters[dimension] = element
        signal_hint = "campaign" if signal_type == "campaign" else "metric"
        try:
            current_spec = build_query_spec(
                metric_id=str(signal_metric_id),
                start_date=ctx.target_date,
                end_date=ctx.target_date,
                filters=filters,
                purpose="signal",
                signal_type=signal_hint,
            )
            baseline_spec = build_query_spec(
                metric_id=str(signal_metric_id),
                start_date=ctx.target_date,
                end_date=ctx.target_date,
                filters=filters,
                purpose="baseline",
                signal_type=signal_hint,
            )
            current_plan = guard_sql(renderer.render(current_spec))
            baseline_plan = guard_sql(renderer.render(baseline_spec))
            current = repository.execute_plan(current_plan, run_id=ctx.run_id)
            baseline = repository.execute_plan(baseline_plan, run_id=ctx.run_id)
        except QuerySpecError as exc:
            return None, _error("fetch_related_signal", exc.code, str(exc))
        except RuntimeError as exc:
            return None, _error("fetch_related_signal", _runtime_code(exc), str(exc))
        try:
            signal = detect_anomaly_from_rows(
                current_rows=list(getattr(current, "rows", []) or []),
                baseline_rows=list(getattr(baseline, "rows", []) or []),
                metric_definition=metric_definition,
                thresh_pct=0.10,
                z_thresh=1.0,
            )
        except ValueError as exc:
            return None, _error("fetch_related_signal", "SIGNAL_SELECTION_INVALID_ROWS", str(exc))
        if not signal.ok:
            continue
        ranked.append((element, _signal_selection_score(signal)))
    if not ranked:
        return None, None
    return max(ranked, key=lambda item: item[1])[0], None


def _signal_selection_score(signal: Any) -> tuple[int, int, float, float]:
    return (
        int(bool(signal.is_anomaly and signal.bad_direction)),
        int(bool(signal.bad_direction)),
        abs(float(signal.delta_pct or 0.0)),
        abs(float(signal.z_score or 0.0)),
    )


def _top_signal_level_element(
    *,
    ctx: RunContext,
    args: dict[str, Any],
    dependencies: Any,
) -> tuple[str | None, ToolExecutionResult | None]:
    repository = ctx.repository
    dimension = str(args.get("dimension") or "")
    if repository is None or not dimension:
        return None, None
    candidate_elements = _candidate_elements(ctx, dimension)
    if not candidate_elements:
        return None, None
    settings = getattr(dependencies, "settings", None)
    signal_metric_by_type = getattr(settings, "signal_metric_by_type", {}) if settings is not None else {}
    signal_type = str(args.get("signal_type") or "")
    signal_metric_id = signal_metric_by_type.get(signal_type)
    if not signal_metric_id:
        return None, _error(
            "fetch_related_signal",
            "SIGNAL_POLICY_MISSING",
            f"{signal_type or 'unknown'} signal metric is not configured",
        )
    renderer = getattr(dependencies, "renderer", None)
    if renderer is None:
        return None, _error(
            "fetch_related_signal",
            "CONFIG_INVALID",
            "signal element selection requires renderer",
        )
    try:
        from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec
        from metric_rca.guardrails.sql_guard import guard_sql

        current_spec = build_query_spec(
            metric_id=str(signal_metric_id),
            start_date=ctx.target_date,
            end_date=ctx.target_date,
            group_by=[dimension],
            filters=_string_filters(args.get("filters")),
            purpose="current",
        )
        current_plan = guard_sql(renderer.render(current_spec))
        current = repository.execute_plan(current_plan, run_id=ctx.run_id)
    except QuerySpecError as exc:
        return None, _error("fetch_related_signal", exc.code, str(exc))
    except RuntimeError as exc:
        return None, _error("fetch_related_signal", _runtime_code(exc), str(exc))

    value_by_element: dict[str, float] = {}
    for row in getattr(current, "rows", []) or []:
        element = row.get(dimension)
        metric_value = row.get("metric_value")
        if element is None or metric_value is None:
            continue
        value_by_element[str(element)] = float(metric_value)
    ranked = [
        (element, value_by_element[element])
        for element in candidate_elements
        if element in value_by_element
    ]
    if not ranked:
        return None, None
    return max(ranked, key=lambda item: item[1])[0], None


def _top_candidate_element(ctx: RunContext, dimension: str) -> str | None:
    elements = _candidate_elements(ctx, dimension)
    return elements[0] if elements else None


def _candidate_elements(ctx: RunContext, dimension: str) -> list[str]:
    if ctx.repository is None or not dimension:
        return []
    row = ctx.repository.get_evidence(run_id=ctx.run_id, evidence_id=f"{ctx.run_id}:E2_{dimension}")
    if not isinstance(row, dict) or row.get("guard_status") != "passed":
        return []
    summary = row.get("result_summary")
    candidates = summary.get("candidates") if isinstance(summary, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return []
    elements: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("element") is not None:
            elements.append(str(candidate["element"]))
    return elements


def _signal_evidence_element(ctx: RunContext, dimension: str) -> str | None:
    if ctx.repository is None or not dimension or not hasattr(ctx.repository, "get_evidences"):
        return None
    for row in ctx.repository.get_evidences(ctx.run_id):
        if not isinstance(row, dict) or row.get("guard_status") != "passed":
            continue
        evidence_id = str(row.get("evidence_id") or "")
        if not evidence_id.startswith(f"{ctx.run_id}:E3"):
            continue
        summary = row.get("result_summary")
        if not isinstance(summary, dict):
            continue
        if summary.get("dimension") == dimension and summary.get("element") is not None:
            return str(summary["element"])
    return None


def _runtime_code(exc: RuntimeError) -> str:
    message_code = str(exc).split(":", maxsplit=1)[0]
    if message_code and message_code.isupper():
        return message_code
    return "SIGNAL_QUERY_FAILED"


def _string_filters(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _coerce_tool_result(result: Any) -> ToolExecutionResult:
    if isinstance(result, ToolExecutionResult):
        return result
    observation = result.observation
    evidence_ids = list(getattr(observation, "evidence_ids", []) or [])
    if not evidence_ids:
        evidence_ids = [
            evidence.evidence_id
            for evidence in getattr(result, "evidences", [])
            if getattr(evidence, "evidence_id", None)
        ]
    return ToolExecutionResult(
        observation=observation,
        evidence_ids=evidence_ids,
        candidates=list(getattr(result, "candidates", []) or []),
    )


def _rank_from_persisted_e4(
    *,
    repository: Any,
    settings: Any,
    run_id: str,
    metric_id: str,
    target_date: date,
) -> ToolExecutionResult:
    e4_id = f"{run_id}:E4"
    e4 = repository.get_evidence(run_id=run_id, evidence_id=e4_id)
    if e4 is None:
        return _error("rank_root_causes", "ATTRIBUTION_COVERAGE_LOW", "E4 evidence is required before ranking")
    candidates = [
        RootCauseCandidate.model_validate(candidate)
        for candidate in (e4.get("result_summary") or {}).get("candidates", [])
    ]
    if not candidates:
        selected = (e4.get("result_summary") or {}).get("selected_candidate")
        if isinstance(selected, dict):
            candidates = [RootCauseCandidate.model_validate(selected)]
    if not candidates:
        return _error("rank_root_causes", "ATTRIBUTION_COVERAGE_LOW", "persisted E4 has no candidates")
    e4_summary = dict(e4.get("result_summary") or {})
    persisted_selected_candidate = _persisted_selected_candidate(e4_summary)
    candidates, adtributor_audit = _enhance_with_adtributor(
        repository=repository,
        settings=settings,
        run_id=run_id,
        metric_id=metric_id,
        candidates=candidates,
    )
    e_rank_id = f"{run_id}:E_rank"
    ranked_candidates = [_candidate_with_rank_evidence(candidate, e_rank_id) for candidate in _rank_candidates(candidates)]
    signal_verified_candidate = _signal_verified_ranked_candidate(
        repository=repository,
        run_id=run_id,
        persisted_selected_candidate=persisted_selected_candidate,
        ranked_candidates=ranked_candidates,
    )
    if signal_verified_candidate is not None:
        selected_candidate = signal_verified_candidate
        candidates = [
            selected_candidate,
            *[candidate for candidate in ranked_candidates if not _same_candidate_element(candidate, selected_candidate)],
        ]
    elif adtributor_audit.get("adtributor_status") == "applied":
        candidates = ranked_candidates
        selected_candidate = candidates[0]
    elif persisted_selected_candidate is not None:
        selected_candidate = _candidate_with_rank_evidence(persisted_selected_candidate, e_rank_id)
        candidates = [
            selected_candidate,
            *[candidate for candidate in ranked_candidates if not _same_candidate_element(candidate, selected_candidate)],
        ]
    else:
        candidates = ranked_candidates
        selected_candidate = candidates[0]
    sql_text = e4.get("sql_text")
    if not sql_text:
        return _error("rank_root_causes", "EVIDENCE_MISSING", "persisted E4 sql_text is required before ranking")
    e4_summary["selected_candidate"] = selected_candidate.model_dump(mode="json")
    e4_summary["candidates"] = [candidate.model_dump(mode="json") for candidate in candidates]
    e4_summary["ranker"] = "adtributor_internal" if any(c.explanatory_power is not None for c in candidates) else "v1"
    e4_summary.update(adtributor_audit)
    _update_e4_summary(repository=repository, run_id=run_id, evidence_id=e4_id, result_summary=e4_summary)
    evidence = Evidence(
        evidence_id=e_rank_id,
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
            **adtributor_audit,
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
    return ToolExecutionResult(
        observation=Observation(
            action_name="rank_root_causes",
            ok=True,
            payload={
                "ranker": e4_summary["ranker"],
                "selected_candidate": selected_candidate.model_dump(mode="json"),
                "candidates": [c.model_dump(mode="json") for c in candidates],
                **adtributor_audit,
            },
            evidence_ids=[evidence.evidence_id],
        ),
        evidence_ids=[evidence.evidence_id],
        candidates=candidates,
    )


def _persisted_selected_candidate(e4_summary: dict[str, Any]) -> RootCauseCandidate | None:
    selected = e4_summary.get("selected_candidate")
    if not isinstance(selected, dict):
        return None
    return RootCauseCandidate.model_validate(selected)


def _same_candidate_element(left: RootCauseCandidate, right: RootCauseCandidate) -> bool:
    return (
        left.dimension == right.dimension
        and str(left.element) == str(right.element)
        and left.root_cause_type == right.root_cause_type
    )


def _signal_verified_ranked_candidate(
    *,
    repository: Any,
    run_id: str,
    persisted_selected_candidate: RootCauseCandidate | None,
    ranked_candidates: list[RootCauseCandidate],
) -> RootCauseCandidate | None:
    if persisted_selected_candidate is None:
        return None
    if not _has_matching_signal_evidence(
        repository=repository,
        run_id=run_id,
        candidate=persisted_selected_candidate,
    ):
        return None
    for candidate in ranked_candidates:
        if _same_candidate_element(candidate, persisted_selected_candidate):
            return candidate
    return _candidate_with_rank_evidence(persisted_selected_candidate, f"{run_id}:E_rank")


def _has_matching_signal_evidence(*, repository: Any, run_id: str, candidate: RootCauseCandidate) -> bool:
    if candidate.dimension is None or candidate.element is None:
        return False
    rows = repository.get_evidences(run_id) if hasattr(repository, "get_evidences") else []
    if not rows:
        return False
    for row in rows:
        if not isinstance(row, dict) or row.get("guard_status") != "passed":
            continue
        evidence_id = str(row.get("evidence_id") or "")
        if not evidence_id.startswith(f"{run_id}:E3"):
            continue
        summary = row.get("result_summary")
        if not isinstance(summary, dict):
            continue
        if summary.get("dimension") == candidate.dimension and str(summary.get("element")) == str(candidate.element):
            return True
    return False


def _enhance_with_adtributor(
    *,
    repository: Any,
    settings: Any,
    run_id: str,
    metric_id: str,
    candidates: list[RootCauseCandidate],
) -> tuple[list[RootCauseCandidate], dict[str, str]]:
    elements = _adtributor_elements_from_persisted_evidence(repository=repository, run_id=run_id)
    if not elements:
        return candidates, _adtributor_not_applicable("no persisted adtributor elements")
    result = attribute_elements(
        metric_id=metric_id,
        elements=elements,
        t_ep=float(getattr(settings, "adtributor_t_ep", 0.67)),
        t_eep=float(getattr(settings, "adtributor_t_eep", 0.10)),
    )
    if not result.ok:
        return candidates, _adtributor_not_applicable(result.error_code or "ADTRIBUTOR_NOT_APPLICABLE")
    score_by_pair = {
        (score.dimension, str(score.element)): score
        for score in result.element_scores
        if score.explanatory_power > 0
    }
    if not score_by_pair:
        return candidates, _adtributor_not_applicable("no positive adtributor scores")
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
    return enhanced, {"adtributor_status": "applied"}


def _adtributor_not_applicable(reason: str) -> dict[str, str]:
    return {
        "adtributor_status": "not_applicable",
        "adtributor_error_code": "ADTRIBUTOR_NOT_APPLICABLE",
        "adtributor_reason": reason,
    }


def _candidate_with_rank_evidence(candidate: RootCauseCandidate, e_rank_id: str) -> RootCauseCandidate:
    evidence_ids = [*candidate.evidence_ids]
    if e_rank_id not in evidence_ids:
        evidence_ids.append(e_rank_id)
    return candidate.model_copy(update={"evidence_ids": evidence_ids})


def _adtributor_pair_rank(score: Any) -> tuple[float, float]:
    return (float(score.explanatory_power), float(score.surprise_js))


def _adtributor_elements_from_persisted_evidence(*, repository: Any, run_id: str) -> list[AdtributorElement]:
    rows = repository.get_evidences(run_id) if hasattr(repository, "get_evidences") else []
    if not rows:
        row = repository.get_evidence(run_id=run_id, evidence_id=f"{run_id}:E2")
        rows = [row] if row is not None else []
    elements: list[AdtributorElement] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("guard_status") != "passed":
            continue
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


def _error(action_name: str, error_code: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        observation=Observation(
            action_name=action_name,
            ok=False,
            error_code=error_code,
            message=message,
        )
    )

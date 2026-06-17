"""Ranking support for persisted RCA contribution evidence."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from metric_rca.domain.models import Evidence, Observation, QuerySpec, RootCauseCandidate, TimeRange
from metric_rca.runtime.tool_models import ToolExecutionResult
from metric_rca.services.adtributor_service import AdtributorElement, attribute_elements
from metric_rca.services.attribution_service import rank_root_causes as _rank_candidates


def rank_from_persisted_e4(
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
    rows = repository.get_evidences(run_id)
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
    rows = repository.get_evidences(run_id)
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
    repository.update_evidence_result_summary(run_id=run_id, evidence_id=evidence_id, result_summary=result_summary)


def _error(action_name: str, error_code: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        observation=Observation(
            action_name=action_name,
            ok=False,
            error_code=error_code,
            message=message,
        )
    )

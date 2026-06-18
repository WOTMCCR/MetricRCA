"""Ranking support for persisted RCA contribution evidence."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from metric_rca.domain.enums import RootCauseType
from metric_rca.domain.models import ContributionSet, Evidence, Observation, QuerySpec, RootCauseCandidate, TimeRange
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
    e4_summary = dict(e4.get("result_summary") or {})
    contribution_set = _load_canonical_contribution_set(e4_summary)
    if isinstance(contribution_set, str):
        return _error(
            "rank_root_causes",
            contribution_set,
            "persisted E4 contribution_set is required and must match derived projection fields",
        )
    candidates = list(contribution_set.candidates)
    if not candidates:
        return _error("rank_root_causes", "ATTRIBUTION_COVERAGE_LOW", "persisted E4 contribution_set has no candidates")
    persisted_selected_candidate = contribution_set.selected_candidate
    candidates, adtributor_audit, adtributor_pair_ranks = _enhance_with_adtributor(
        repository=repository,
        settings=settings,
        run_id=run_id,
        metric_id=metric_id,
        candidates=candidates,
    )
    e_rank_id = f"{run_id}:E_rank"
    ranked_candidates = [_candidate_with_rank_evidence(candidate, e_rank_id) for candidate in _rank_candidates(candidates)]
    embedded_verified_candidate = _embedded_verified_ranked_candidate(
        repository=repository,
        run_id=run_id,
        persisted_selected_candidate=persisted_selected_candidate,
        ranked_candidates=ranked_candidates,
        adtributor_audit=adtributor_audit,
        adtributor_pair_ranks=adtributor_pair_ranks,
    )
    signal_verified_non_interaction_candidate = _signal_verified_non_interaction_candidate_for_interaction(
        repository=repository,
        run_id=run_id,
        persisted_selected_candidate=persisted_selected_candidate,
        ranked_candidates=ranked_candidates,
    )
    signal_verified_candidate = _signal_verified_ranked_candidate(
        repository=repository,
        run_id=run_id,
        persisted_selected_candidate=persisted_selected_candidate,
        ranked_candidates=ranked_candidates,
    )
    if embedded_verified_candidate is not None:
        selected_candidate = embedded_verified_candidate
        candidates = _selected_first_with_diverse_top3(selected_candidate, ranked_candidates)
    elif signal_verified_non_interaction_candidate is not None:
        selected_candidate = signal_verified_non_interaction_candidate
        candidates = _selected_first_with_diverse_top3(selected_candidate, ranked_candidates)
    elif signal_verified_candidate is not None:
        selected_candidate = signal_verified_candidate
        candidates = _selected_first_with_diverse_top3(selected_candidate, ranked_candidates)
    elif interaction_candidate := _interaction_promoted_candidate(
        repository=repository,
        run_id=run_id,
        metric_id=metric_id,
        ranked_candidates=ranked_candidates,
    ):
        selected_candidate = interaction_candidate
        candidates = _selected_first_with_diverse_top3(
            selected_candidate,
            [candidate for candidate in ranked_candidates if not _same_dimension_element(candidate, selected_candidate)],
        )
    elif adtributor_audit.get("adtributor_status") == "applied":
        candidates = _diversify_ranked_top3(ranked_candidates)
        selected_candidate = candidates[0]
    elif persisted_selected_candidate is not None:
        selected_candidate = _candidate_with_rank_evidence(persisted_selected_candidate, e_rank_id)
        candidates = _selected_first_with_diverse_top3(selected_candidate, ranked_candidates)
    else:
        candidates = _diversify_ranked_top3(ranked_candidates)
        selected_candidate = candidates[0]
    sql_text = e4.get("sql_text")
    if not sql_text:
        return _error("rank_root_causes", "EVIDENCE_MISSING", "persisted E4 sql_text is required before ranking")
    contribution_set = _with_ranked_contribution_set(
        contribution_set=contribution_set,
        selected_candidate=selected_candidate,
        candidates=candidates,
    )
    e4_summary["contribution_set"] = contribution_set.model_dump(mode="json")
    e4_summary["selected_candidate"] = contribution_set.selected_candidate.model_dump(mode="json")
    e4_summary["candidates"] = [candidate.model_dump(mode="json") for candidate in contribution_set.candidates]
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
            "contribution_set": contribution_set.model_dump(mode="json"),
            "selected_candidate": contribution_set.selected_candidate.model_dump(mode="json"),
            "candidates": [c.model_dump(mode="json") for c in contribution_set.candidates],
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
                "contribution_set": contribution_set.model_dump(mode="json"),
                "selected_candidate": contribution_set.selected_candidate.model_dump(mode="json"),
                "candidates": [c.model_dump(mode="json") for c in contribution_set.candidates],
                **adtributor_audit,
            },
            evidence_ids=[evidence.evidence_id],
        ),
        evidence_ids=[evidence.evidence_id],
        candidates=contribution_set.candidates,
    )


def _load_canonical_contribution_set(e4_summary: dict[str, Any]) -> ContributionSet | str:
    raw = e4_summary.get("contribution_set")
    if not isinstance(raw, dict):
        return "CONTRIBUTION_SET_MISSING"
    return ContributionSet.model_validate(raw)


def _with_ranked_contribution_set(
    *,
    contribution_set: ContributionSet,
    selected_candidate: RootCauseCandidate,
    candidates: list[RootCauseCandidate],
) -> ContributionSet:
    evidence_ids = _ordered_unique(
        [
            *contribution_set.evidence_ids,
            *selected_candidate.evidence_ids,
            *[evidence_id for candidate in candidates for evidence_id in candidate.evidence_ids],
        ]
    )
    return contribution_set.model_copy(
        update={
            "selected_candidate": selected_candidate,
            "candidates": candidates,
            "evidence_ids": evidence_ids,
        }
    )


def _same_candidate_element(left: RootCauseCandidate, right: RootCauseCandidate) -> bool:
    return (
        left.dimension == right.dimension
        and str(left.element) == str(right.element)
        and left.root_cause_type == right.root_cause_type
    )


def _same_dimension_element(left: RootCauseCandidate, right: RootCauseCandidate) -> bool:
    return left.dimension == right.dimension and str(left.element) == str(right.element)


def _diversify_ranked_top3(candidates: list[RootCauseCandidate]) -> list[RootCauseCandidate]:
    if not candidates:
        return []
    return _selected_first_with_diverse_top3(candidates[0], candidates)


def _selected_first_with_diverse_top3(
    selected_candidate: RootCauseCandidate,
    ranked_candidates: list[RootCauseCandidate],
) -> list[RootCauseCandidate]:
    remaining = [
        candidate
        for candidate in ranked_candidates
        if not _same_candidate_element(candidate, selected_candidate)
    ]
    selected = [selected_candidate]
    diversified: list[RootCauseCandidate] = []
    target_size = min(3, len(remaining) + 1)
    while len(selected) < target_size and remaining:
        index = _next_diverse_candidate_index(remaining, selected)
        diversified.append(remaining.pop(index))
        selected.append(diversified[-1])
    return [selected_candidate, *diversified, *remaining]


def _next_diverse_candidate_index(
    candidates: list[RootCauseCandidate],
    selected: list[RootCauseCandidate],
) -> int:
    used_root_types = {candidate.root_cause_type for candidate in selected}
    passes = (
        lambda candidate: candidate.root_cause_type not in used_root_types
        and not _is_redundant_with_selected(candidate, selected),
        lambda candidate: not _is_redundant_with_selected(candidate, selected),
        lambda candidate: candidate.root_cause_type not in used_root_types,
    )
    for predicate in passes:
        for index, candidate in enumerate(candidates):
            if predicate(candidate):
                return index
    return 0


def _is_redundant_with_selected(
    candidate: RootCauseCandidate,
    selected: list[RootCauseCandidate],
) -> bool:
    primary_pair = _primary_pair(candidate)
    if primary_pair is None:
        return False
    return any(
        existing.root_cause_type == candidate.root_cause_type
        and primary_pair in _candidate_pairs(existing)
        for existing in selected
    )


def _primary_pair(candidate: RootCauseCandidate) -> tuple[str, str] | None:
    if candidate.dimension is None or candidate.element is None:
        return None
    return (candidate.dimension, str(candidate.element))


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
        required_bad_direction=_target_bad_direction(repository=repository, run_id=run_id),
    ):
        return None
    for candidate in ranked_candidates:
        if _same_candidate_element(candidate, persisted_selected_candidate):
            return candidate
    return _candidate_with_rank_evidence(persisted_selected_candidate, f"{run_id}:E_rank")


def _signal_verified_non_interaction_candidate_for_interaction(
    *,
    repository: Any,
    run_id: str,
    persisted_selected_candidate: RootCauseCandidate | None,
    ranked_candidates: list[RootCauseCandidate],
) -> RootCauseCandidate | None:
    if persisted_selected_candidate is None:
        return None
    if persisted_selected_candidate.root_cause_type != RootCauseType.INTERACTION_CHANNEL_CATEGORY.value:
        return None
    required_bad_direction = _target_bad_direction(repository=repository, run_id=run_id)
    if not _has_matching_signal_evidence(
        repository=repository,
        run_id=run_id,
        candidate=persisted_selected_candidate,
        required_bad_direction=required_bad_direction,
    ):
        return None
    selected_primary_pair = _primary_pair(persisted_selected_candidate)
    if selected_primary_pair is None:
        return None
    for candidate in ranked_candidates:
        if candidate.root_cause_type == RootCauseType.INTERACTION_CHANNEL_CATEGORY.value:
            continue
        if _primary_pair(candidate) != selected_primary_pair:
            continue
        if _has_matching_signal_evidence(
            repository=repository,
            run_id=run_id,
            candidate=candidate,
            required_bad_direction=required_bad_direction,
        ):
            return candidate
    return None


def _embedded_verified_ranked_candidate(
    *,
    repository: Any,
    run_id: str,
    persisted_selected_candidate: RootCauseCandidate | None,
    ranked_candidates: list[RootCauseCandidate],
    adtributor_audit: dict[str, str],
    adtributor_pair_ranks: dict[tuple[str, str], tuple[float, float]],
) -> RootCauseCandidate | None:
    if adtributor_audit.get("adtributor_status") != "applied":
        return None
    if persisted_selected_candidate is None:
        return None
    if persisted_selected_candidate.root_cause_type != RootCauseType.STOCKOUT.value:
        return None

    selected_candidate = _matching_ranked_candidate(ranked_candidates, persisted_selected_candidate)
    if selected_candidate is None:
        return None
    selected_primary_pair = _primary_pair(selected_candidate)
    if selected_primary_pair is None:
        return None
    selected_pairs = _candidate_pairs(selected_candidate)
    selected_pair_rank = adtributor_pair_ranks.get(selected_primary_pair)
    if selected_pair_rank is None:
        return None

    required_bad_direction = _target_bad_direction(repository=repository, run_id=run_id)
    promotable: list[tuple[tuple[float, float], float, RootCauseCandidate]] = []
    for candidate in ranked_candidates:
        primary_pair = _primary_pair(candidate)
        if primary_pair is None:
            continue
        if primary_pair not in selected_pairs:
            continue
        if candidate.root_cause_type != RootCauseType.CAMPAIGN_TRAFFIC_DROP.value:
            continue
        if candidate.dimension != "channel":
            continue
        if not _has_matching_signal_evidence(
            repository=repository,
            run_id=run_id,
            candidate=candidate,
            required_bad_direction=required_bad_direction,
        ):
            continue
        candidate_pair_rank = adtributor_pair_ranks.get(primary_pair)
        if candidate_pair_rank is None or candidate_pair_rank < selected_pair_rank:
            continue
        promotable.append((candidate_pair_rank, float(candidate.eng_confidence), candidate))

    if not promotable:
        return None
    return max(promotable, key=lambda item: (item[0], item[1]))[2]


def _matching_ranked_candidate(
    ranked_candidates: list[RootCauseCandidate],
    candidate: RootCauseCandidate,
) -> RootCauseCandidate | None:
    for ranked_candidate in ranked_candidates:
        if _same_candidate_element(ranked_candidate, candidate):
            return ranked_candidate
    return None


def _has_matching_signal_evidence(
    *,
    repository: Any,
    run_id: str,
    candidate: RootCauseCandidate,
    required_bad_direction: bool | None = None,
) -> bool:
    if candidate.dimension is None or candidate.element is None:
        return False
    return _has_matching_signal_for_pair(
        repository=repository,
        run_id=run_id,
        dimension=candidate.dimension,
        element=str(candidate.element),
        required_bad_direction=required_bad_direction,
    )


def _has_matching_signal_for_pair(
    *,
    repository: Any,
    run_id: str,
    dimension: str,
    element: str,
    required_bad_direction: bool | None = None,
) -> bool:
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
        if summary.get("dimension") != dimension or str(summary.get("element")) != element:
            continue
        if summary.get("is_anomaly") is not True:
            continue
        if required_bad_direction is not None and summary.get("bad_direction") is not required_bad_direction:
            continue
        return True
    return False


def _interaction_promoted_candidate(
    *,
    repository: Any,
    run_id: str,
    metric_id: str,
    ranked_candidates: list[RootCauseCandidate],
) -> RootCauseCandidate | None:
    if metric_id not in {"gmv", "uv"}:
        return None
    if not _target_is_bad_direction_anomaly(repository=repository, run_id=run_id):
        return None
    for candidate in ranked_candidates:
        if candidate.root_cause_type == RootCauseType.INTERACTION_CHANNEL_CATEGORY.value:
            return candidate.model_copy(
                update={
                    "evidence_ids": _interaction_evidence_ids(
                        repository=repository,
                        run_id=run_id,
                        pairs=_candidate_pairs(candidate),
                        base_evidence_ids=candidate.evidence_ids,
                    )
                }
            )
        if candidate.dimension not in {"channel", "category"}:
            continue
        if _has_matching_signal_evidence(
            repository=repository,
            run_id=run_id,
            candidate=candidate,
            required_bad_direction=True,
        ):
            continue
        pairs = _candidate_pairs(candidate)
        if not _has_dimension(pairs, "channel") or not _has_dimension(pairs, "category"):
            continue
        if _has_any_pair_matching_signal_evidence(
            repository=repository,
            run_id=run_id,
            pairs=pairs,
            required_bad_direction=True,
        ):
            continue
        evidence_ids = _interaction_evidence_ids(
            repository=repository,
            run_id=run_id,
            pairs=pairs,
            base_evidence_ids=candidate.evidence_ids,
        )
        e_rank_id = f"{run_id}:E_rank"
        if e_rank_id not in evidence_ids:
            evidence_ids.append(e_rank_id)
        return candidate.model_copy(
            update={
                "root_cause_type": RootCauseType.INTERACTION_CHANNEL_CATEGORY.value,
                "evidence_ids": evidence_ids,
            }
        )
    return None


def _has_any_pair_matching_signal_evidence(
    *,
    repository: Any,
    run_id: str,
    pairs: set[tuple[str, str]],
    required_bad_direction: bool,
) -> bool:
    for dimension, element in pairs:
        if dimension not in {"channel", "category"}:
            continue
        if _has_matching_signal_for_pair(
            repository=repository,
            run_id=run_id,
            dimension=dimension,
            element=element,
            required_bad_direction=required_bad_direction,
        ):
            return True
    return False


def _interaction_evidence_ids(
    *,
    repository: Any,
    run_id: str,
    pairs: set[tuple[str, str]],
    base_evidence_ids: list[str],
) -> list[str]:
    evidence_ids = [*base_evidence_ids]
    interaction_pairs = {(dimension, element) for dimension, element in pairs if dimension in {"channel", "category"}}
    if not interaction_pairs:
        return _ordered_unique(evidence_ids)
    dimensions = {dimension for dimension, _ in interaction_pairs}
    rows = repository.get_evidences(run_id)
    for row in rows:
        if not isinstance(row, dict) or row.get("guard_status") != "passed":
            continue
        evidence_id = str(row.get("evidence_id") or "")
        if not evidence_id.startswith(f"{run_id}:"):
            continue
        alias = evidence_id.removeprefix(f"{run_id}:")
        summary = row.get("result_summary")
        if alias in {f"E2_{dimension}" for dimension in dimensions}:
            evidence_ids.append(evidence_id)
            continue
        if alias in {f"E_select_{dimension}" for dimension in dimensions}:
            evidence_ids.append(evidence_id)
            continue
        if alias in {f"E4_{dimension}" for dimension in dimensions}:
            evidence_ids.append(evidence_id)
            continue
        if alias == "E3" or alias.startswith("E3_"):
            if not isinstance(summary, dict):
                continue
            pair = (str(summary.get("dimension")), str(summary.get("element")))
            if pair in interaction_pairs:
                evidence_ids.append(evidence_id)
    return _ordered_unique(evidence_ids)


def _target_is_bad_direction_anomaly(*, repository: Any, run_id: str) -> bool:
    return _target_bad_direction(repository=repository, run_id=run_id) is True


def _target_bad_direction(*, repository: Any, run_id: str) -> bool | None:
    row = repository.get_evidence(run_id=run_id, evidence_id=f"{run_id}:E1")
    if not isinstance(row, dict) or row.get("guard_status") != "passed":
        return None
    summary = row.get("result_summary")
    if not isinstance(summary, dict):
        return None
    if summary.get("is_anomaly") is not True:
        return None
    bad_direction = summary.get("bad_direction")
    if isinstance(bad_direction, bool):
        return bad_direction
    return None


def _candidate_pairs(candidate: RootCauseCandidate) -> set[tuple[str, str]]:
    pairs = {(dimension, str(element)) for dimension, element in candidate.dimension_elements}
    if candidate.dimension is not None and candidate.element is not None:
        pairs.add((candidate.dimension, str(candidate.element)))
    return pairs


def _has_dimension(pairs: set[tuple[str, str]], dimension: str) -> bool:
    return any(pair_dimension == dimension for pair_dimension, _ in pairs)


def _enhance_with_adtributor(
    *,
    repository: Any,
    settings: Any,
    run_id: str,
    metric_id: str,
    candidates: list[RootCauseCandidate],
) -> tuple[list[RootCauseCandidate], dict[str, str], dict[tuple[str, str], tuple[float, float]]]:
    elements = _adtributor_elements_from_persisted_evidence(repository=repository, run_id=run_id)
    if not elements:
        return candidates, _adtributor_not_applicable("no persisted adtributor elements"), {}
    result = attribute_elements(
        metric_id=metric_id,
        elements=elements,
        t_ep=float(getattr(settings, "adtributor_t_ep", 0.67)),
        t_eep=float(getattr(settings, "adtributor_t_eep", 0.10)),
    )
    if not result.ok:
        return candidates, _adtributor_not_applicable(result.error_code or "ADTRIBUTOR_NOT_APPLICABLE"), {}
    score_by_pair = {
        (score.dimension, str(score.element)): score
        for score in result.element_scores
        if score.explanatory_power > 0
    }
    if not score_by_pair:
        return candidates, _adtributor_not_applicable("no positive adtributor scores"), {}
    pair_ranks = {pair: _adtributor_pair_rank(score) for pair, score in score_by_pair.items()}
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
    return enhanced, {"adtributor_status": "applied"}, pair_ranks


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


def _ordered_unique(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = str(value)
        if text not in unique:
            unique.append(text)
    return unique


def _error(action_name: str, error_code: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        observation=Observation(
            action_name=action_name,
            ok=False,
            error_code=error_code,
            message=message,
        )
    )

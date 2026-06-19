"""Cross-chain ContributionSet merge support."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from metric_rca.domain.enums import RootCauseType
from metric_rca.domain.models import ContributionSet, RootCauseCandidate


COMPOSITION_STRATEGY = "representative_source_merge_v1"
_CHANNEL_RUNNER_FLOOR = 0.20
_CONVERSION_CHANNEL_RUNNER_FLOOR = 0.20
_INTERACTION_REPRESENTATIVE_FLOOR = 0.60
_SINGLE_DRIVER_COLLAPSE_MIN_REPRESENTATIVES = 5
_SINGLE_DRIVER_CONFIDENCE_FLOOR = 0.90
_SINGLE_DRIVER_CONTRIBUTION_FLOOR = 0.90


class ContributionSetBuilder:
    def merge(self, *, run_id: str, source_sets: list[tuple[str, ContributionSet]]) -> ContributionSet:
        if not source_sets:
            raise ValueError("CONTRIBUTION_SET_MISSING")
        conversion_only_sources = _all_sources_are_conversion(source_sets)
        preferred_key = _preferred_candidate_key(
            source_sets,
            conversion_only_sources=conversion_only_sources,
        )
        candidates_by_key: dict[tuple[str, str | None, str | None], RootCauseCandidate] = {}
        representative_keys: list[tuple[str, str | None, str | None]] = []
        evidence_ids: list[str] = []
        chains: list[dict[str, Any]] = []
        chain_evidence_ids: list[str] = []

        for evidence_id, contribution_set in source_sets:
            if not evidence_id.startswith(f"{run_id}:"):
                raise ValueError("EVIDENCE_SCOPE_INVALID")
            _extend_unique(evidence_ids, contribution_set.evidence_ids)
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
            chain_evidence_ids.append(evidence_id)
            chains.extend(_chain_entries(evidence_id=evidence_id, contribution_set=contribution_set))
            _extend_unique_keys(
                representative_keys,
                _source_representative_keys(
                    contribution_set,
                    preferred_key=preferred_key,
                    conversion_only_sources=conversion_only_sources,
                ),
            )
            for candidate in contribution_set.candidates:
                key = _candidate_key(candidate)
                existing = candidates_by_key.get(key)
                if existing is None:
                    candidates_by_key[key] = candidate
                    continue
                candidates_by_key[key] = _merge_duplicate_candidate(existing, candidate)

        candidates = sorted(candidates_by_key.values(), key=_candidate_sort_key, reverse=True)
        if not candidates:
            raise ValueError("CONTRIBUTION_SET_MISSING")
        selected_candidate = candidates_by_key.get(preferred_key)
        if selected_candidate is None:
            raise ValueError("CONTRIBUTION_SET_SELECTED_MISSING")
        representative_keys = _compose_representative_keys(
            preferred_key=preferred_key,
            representative_keys=representative_keys,
            candidates_by_key=candidates_by_key,
        )
        representative_keys = _collapse_high_confidence_single_driver_keys(
            preferred_key=preferred_key,
            representative_keys=representative_keys,
            candidates_by_key=candidates_by_key,
        )
        _embed_material_campaign_channel_runners(
            representative_keys=representative_keys,
            candidates_by_key=candidates_by_key,
        )
        selected_candidate = candidates_by_key.get(preferred_key)
        if selected_candidate is None:
            raise ValueError("CONTRIBUTION_SET_SELECTED_MISSING")
        candidates = [candidates_by_key[key] for key in representative_keys]
        return ContributionSet(
            selected_candidate=selected_candidate,
            candidates=candidates,
            evidence_ids=evidence_ids,
            factor_graph={
                "chain_evidence_ids": chain_evidence_ids,
                "chains": chains,
                "composition_strategy": COMPOSITION_STRATEGY,
            },
            selection_evidence_id=candidates[0].evidence_ids[0] if candidates[0].evidence_ids else None,
        )


def _all_sources_are_conversion(source_sets: list[tuple[str, ContributionSet]]) -> bool:
    candidates = [candidate for _, source_set in source_sets for candidate in source_set.candidates]
    return bool(candidates) and all(
        candidate.root_cause_type == RootCauseType.CONVERSION_DROP.value
        for candidate in candidates
    )


def _preferred_candidate_key(
    source_sets: list[tuple[str, ContributionSet]],
    *,
    conversion_only_sources: bool,
) -> tuple[str, str | None, str | None]:
    candidates = [candidate for _, source_set in source_sets for candidate in source_set.candidates]
    if conversion_only_sources:
        return _candidate_key(max(candidates, key=_candidate_strength_key))
    return _candidate_key(source_sets[0][1].selected_candidate)


def _candidate_strength_key(candidate: RootCauseCandidate) -> tuple[float, float, float]:
    return (
        float(candidate.contribution_pct),
        float(candidate.signal_severity),
        float(candidate.eng_confidence),
    )


def _source_representative_keys(
    contribution_set: ContributionSet,
    *,
    preferred_key: tuple[str, str | None, str | None],
    conversion_only_sources: bool,
) -> list[tuple[str, str | None, str | None]]:
    selected_key = _candidate_key(contribution_set.selected_candidate)
    if (
        conversion_only_sources
        and contribution_set.selected_candidate.root_cause_type == RootCauseType.CONVERSION_DROP.value
        and selected_key != preferred_key
    ):
        return []
    keys = [selected_key]
    for candidate in contribution_set.candidates:
        key = _candidate_key(candidate)
        if key == selected_key:
            continue
        if _is_material_source_runner(candidate, selected_key=selected_key):
            keys.append(key)
    return keys


def _is_material_source_runner(
    candidate: RootCauseCandidate,
    *,
    selected_key: tuple[str, str | None, str | None],
) -> bool:
    if candidate.root_cause_type == RootCauseType.INTERACTION_CHANNEL_CATEGORY.value:
        return False
    if candidate.root_cause_type == RootCauseType.CONVERSION_DROP.value:
        return (
            selected_key[0] == RootCauseType.CONVERSION_DROP.value
            and selected_key[1] == "channel"
            and candidate.dimension == "channel"
            and float(candidate.contribution_pct) >= _CONVERSION_CHANNEL_RUNNER_FLOOR
        )
    return (
        candidate.root_cause_type == RootCauseType.CAMPAIGN_TRAFFIC_DROP.value
        and candidate.dimension == "channel"
        and float(candidate.contribution_pct) >= _CHANNEL_RUNNER_FLOOR
    )


def _compose_representative_keys(
    *,
    preferred_key: tuple[str, str | None, str | None],
    representative_keys: list[tuple[str, str | None, str | None]],
    candidates_by_key: dict[tuple[str, str | None, str | None], RootCauseCandidate],
) -> list[tuple[str, str | None, str | None]]:
    if preferred_key not in candidates_by_key:
        raise ValueError("CONTRIBUTION_SET_SELECTED_MISSING")
    ordered = [preferred_key]
    _extend_unique_keys(ordered, representative_keys)
    non_interaction_dimensions = {
        candidate.dimension
        for key in ordered
        if (candidate := candidates_by_key.get(key)) is not None
        and candidate.root_cause_type != RootCauseType.INTERACTION_CHANNEL_CATEGORY.value
        and candidate.dimension is not None
    }
    strong_interaction_dimensions = {
        candidate.dimension
        for key in ordered
        if (candidate := candidates_by_key.get(key)) is not None
        and candidate.root_cause_type == RootCauseType.INTERACTION_CHANNEL_CATEGORY.value
        and candidate.dimension in {"channel", "category"}
        and float(candidate.contribution_pct) >= _INTERACTION_REPRESENTATIVE_FLOOR
    }
    keep_interaction_representatives = len(strong_interaction_dimensions) >= 2

    composed: list[tuple[str, str | None, str | None]] = []
    for key in ordered:
        candidate = candidates_by_key.get(key)
        if candidate is None:
            continue
        if (
            key != preferred_key
            and candidate.root_cause_type == RootCauseType.INTERACTION_CHANNEL_CATEGORY.value
            and candidate.dimension in non_interaction_dimensions
            and not (
                keep_interaction_representatives
                and candidate.dimension in strong_interaction_dimensions
            )
        ):
            continue
        composed.append(key)
    if not composed:
        raise ValueError("CONTRIBUTION_SET_MISSING")
    return composed


def _collapse_high_confidence_single_driver_keys(
    *,
    preferred_key: tuple[str, str | None, str | None],
    representative_keys: list[tuple[str, str | None, str | None]],
    candidates_by_key: dict[tuple[str, str | None, str | None], RootCauseCandidate],
) -> list[tuple[str, str | None, str | None]]:
    if len(representative_keys) < _SINGLE_DRIVER_COLLAPSE_MIN_REPRESENTATIVES:
        return representative_keys
    selected = candidates_by_key.get(preferred_key)
    if selected is None:
        raise ValueError("CONTRIBUTION_SET_SELECTED_MISSING")
    if selected.root_cause_type == RootCauseType.INTERACTION_CHANNEL_CATEGORY.value:
        return representative_keys
    if float(selected.eng_confidence) < _SINGLE_DRIVER_CONFIDENCE_FLOOR:
        return representative_keys
    if float(selected.contribution_pct) < _SINGLE_DRIVER_CONTRIBUTION_FLOOR:
        return representative_keys
    if any(_is_interaction_key(key, candidates_by_key) for key in representative_keys):
        return representative_keys
    return [preferred_key]


def _is_interaction_key(
    key: tuple[str, str | None, str | None],
    candidates_by_key: dict[tuple[str, str | None, str | None], RootCauseCandidate],
) -> bool:
    candidate = candidates_by_key.get(key)
    return (
        candidate is not None
        and candidate.root_cause_type == RootCauseType.INTERACTION_CHANNEL_CATEGORY.value
    )


def _embed_material_campaign_channel_runners(
    *,
    representative_keys: list[tuple[str, str | None, str | None]],
    candidates_by_key: dict[tuple[str, str | None, str | None], RootCauseCandidate],
) -> None:
    material_pairs: list[tuple[str, str]] = []
    for key in representative_keys:
        candidate = candidates_by_key.get(key)
        if candidate is None or not _is_material_campaign_channel_runner(candidate):
            continue
        pair = _candidate_pair(candidate)
        if pair is not None and pair not in material_pairs:
            material_pairs.append(pair)
    if len(material_pairs) < 2:
        return

    for key in representative_keys:
        candidate = candidates_by_key.get(key)
        if (
            candidate is None
            or candidate.root_cause_type != RootCauseType.CAMPAIGN_TRAFFIC_DROP.value
            or candidate.dimension != "channel"
        ):
            continue
        candidates_by_key[key] = _candidate_with_dimension_elements(candidate, material_pairs)


def _is_material_campaign_channel_runner(candidate: RootCauseCandidate) -> bool:
    return (
        candidate.root_cause_type == RootCauseType.CAMPAIGN_TRAFFIC_DROP.value
        and candidate.dimension == "channel"
        and float(candidate.contribution_pct) >= _CHANNEL_RUNNER_FLOOR
    )


def _candidate_with_dimension_elements(
    candidate: RootCauseCandidate,
    additions: list[tuple[str, str]],
) -> RootCauseCandidate:
    dimension_elements = list(candidate.dimension_elements)
    primary_pair = _candidate_pair(candidate)
    if primary_pair is not None and primary_pair not in dimension_elements:
        dimension_elements.insert(0, primary_pair)
    for pair in additions:
        if pair not in dimension_elements:
            dimension_elements.append(pair)
    return candidate.model_copy(update={"dimension_elements": dimension_elements})


def _candidate_pair(candidate: RootCauseCandidate) -> tuple[str, str] | None:
    if candidate.dimension is None or candidate.element is None:
        return None
    return (candidate.dimension, str(candidate.element))


def _candidate_key(candidate: RootCauseCandidate) -> tuple[str, str | None, str | None]:
    return (candidate.root_cause_type, candidate.dimension, candidate.element)


def _merge_duplicate_candidate(left: RootCauseCandidate, right: RootCauseCandidate) -> RootCauseCandidate:
    selected = left if _candidate_sort_key(left) >= _candidate_sort_key(right) else right
    evidence_ids = [*left.evidence_ids]
    _extend_unique(evidence_ids, right.evidence_ids)
    dimension_elements = [*left.dimension_elements]
    for pair in right.dimension_elements:
        if pair not in dimension_elements:
            dimension_elements.append(pair)
    return selected.model_copy(
        update={
            "evidence_ids": evidence_ids,
            "dimension_elements": dimension_elements,
        }
    )


def _candidate_sort_key(candidate: RootCauseCandidate) -> tuple[float, float, float]:
    return (
        float(candidate.eng_confidence),
        float(candidate.contribution_pct),
        float(candidate.signal_severity),
    )


def _chain_entries(*, evidence_id: str, contribution_set: ContributionSet) -> list[dict[str, Any]]:
    raw = contribution_set.factor_graph.get("chains")
    if isinstance(raw, list) and all(isinstance(item, dict) for item in raw):
        return [{"evidence_id": evidence_id, **item} for item in raw]
    return [{"evidence_id": evidence_id, "factor_graph": contribution_set.factor_graph}]


def _extend_unique(values: list[str], additions: Iterable[str]) -> None:
    for item in additions:
        text = str(item)
        if text not in values:
            values.append(text)


def _extend_unique_keys(
    values: list[tuple[str, str | None, str | None]],
    additions: Iterable[tuple[str, str | None, str | None]],
) -> None:
    for item in additions:
        if item not in values:
            values.append(item)

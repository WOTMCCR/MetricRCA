"""Cross-chain ContributionSet merge support."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from metric_rca.domain.models import ContributionSet, RootCauseCandidate


class ContributionSetBuilder:
    def merge(self, *, run_id: str, source_sets: list[tuple[str, ContributionSet]]) -> ContributionSet:
        if not source_sets:
            raise ValueError("CONTRIBUTION_SET_MISSING")
        preferred_key = _candidate_key(source_sets[0][1].selected_candidate)
        candidates_by_key: dict[tuple[str, str | None, str | None], RootCauseCandidate] = {}
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
        candidates = [
            selected_candidate,
            *[candidate for candidate in candidates if _candidate_key(candidate) != preferred_key],
        ]
        return ContributionSet(
            selected_candidate=selected_candidate,
            candidates=candidates,
            evidence_ids=evidence_ids,
            factor_graph={
                "chain_evidence_ids": chain_evidence_ids,
                "chains": chains,
            },
            selection_evidence_id=candidates[0].evidence_ids[0] if candidates[0].evidence_ids else None,
        )


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

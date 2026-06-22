from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "metric_rca/runtime/ranking.py"


def replace_once(old: str, new: str) -> None:
    source = PATH.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"ranking.py: expected one replacement, found {count}")
    PATH.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "    signal_verified_non_interaction_candidate = _signal_verified_non_interaction_candidate_for_interaction(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n"
    "        persisted_selected_candidate=persisted_selected_candidate,\n"
    "        ranked_candidates=ranked_candidates,\n"
    "    )\n"
    "    signal_verified_candidate = _signal_verified_ranked_candidate(\n",
    "    interaction_verified_candidate = _interaction_verified_ranked_candidate(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n"
    "        metric_id=metric_id,\n"
    "        ranked_candidates=ranked_candidates,\n"
    "    )\n"
    "    signal_verified_candidate = _signal_verified_ranked_candidate(\n",
)

replace_once(
    "    elif signal_verified_non_interaction_candidate is not None:\n"
    "        selected_candidate = signal_verified_non_interaction_candidate\n"
    "        candidates = _selected_first_with_diverse_top3(selected_candidate, ranked_candidates)\n"
    "    elif signal_verified_candidate is not None:\n",
    "    elif interaction_verified_candidate is not None:\n"
    "        selected_candidate = interaction_verified_candidate\n"
    "        candidates = _selected_first_with_diverse_top3(selected_candidate, ranked_candidates)\n"
    "    elif signal_verified_candidate is not None:\n",
)

replace_once(
    "    if persisted_selected_candidate is None:\n"
    "        return None\n"
    "    if not _has_matching_signal_evidence(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n"
    "        candidate=persisted_selected_candidate,\n"
    "        required_bad_direction=_target_bad_direction(repository=repository, run_id=run_id),\n"
    "    ):\n",
    "    if persisted_selected_candidate is None:\n"
    "        return None\n"
    "    if persisted_selected_candidate.root_cause_type == RootCauseType.INTERACTION_CHANNEL_CATEGORY.value:\n"
    "        return None\n"
    "    if not _has_matching_signal_evidence(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n"
    "        candidate=persisted_selected_candidate,\n"
    "        required_bad_direction=_target_bad_direction(repository=repository, run_id=run_id),\n"
    "        required_signal_type=_signal_type_for_candidate(persisted_selected_candidate),\n"
    "    ):\n",
)

replace_once(
    "def _signal_verified_non_interaction_candidate_for_interaction(\n"
    "    *,\n"
    "    repository: Any,\n"
    "    run_id: str,\n"
    "    persisted_selected_candidate: RootCauseCandidate | None,\n"
    "    ranked_candidates: list[RootCauseCandidate],\n"
    ") -> RootCauseCandidate | None:\n"
    "    if persisted_selected_candidate is None:\n"
    "        return None\n"
    "    if persisted_selected_candidate.root_cause_type != RootCauseType.INTERACTION_CHANNEL_CATEGORY.value:\n"
    "        return None\n"
    "    required_bad_direction = _target_bad_direction(repository=repository, run_id=run_id)\n"
    "    if not _has_matching_signal_evidence(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n"
    "        candidate=persisted_selected_candidate,\n"
    "        required_bad_direction=required_bad_direction,\n"
    "    ):\n"
    "        return None\n"
    "    selected_primary_pair = _primary_pair(persisted_selected_candidate)\n"
    "    if selected_primary_pair is None:\n"
    "        return None\n"
    "    for candidate in ranked_candidates:\n"
    "        if candidate.root_cause_type == RootCauseType.INTERACTION_CHANNEL_CATEGORY.value:\n"
    "            continue\n"
    "        if _primary_pair(candidate) != selected_primary_pair:\n"
    "            continue\n"
    "        if _has_matching_signal_evidence(\n"
    "            repository=repository,\n"
    "            run_id=run_id,\n"
    "            candidate=candidate,\n"
    "            required_bad_direction=required_bad_direction,\n"
    "        ):\n"
    "            return candidate\n"
    "    return None\n\n\n",
    "def _interaction_verified_ranked_candidate(\n"
    "    *,\n"
    "    repository: Any,\n"
    "    run_id: str,\n"
    "    metric_id: str,\n"
    "    ranked_candidates: list[RootCauseCandidate],\n"
    ") -> RootCauseCandidate | None:\n"
    "    if metric_id not in {\"gmv\", \"uv\"}:\n"
    "        return None\n"
    "    if not _target_is_bad_direction_anomaly(repository=repository, run_id=run_id):\n"
    "        return None\n"
    "    for candidate in ranked_candidates:\n"
    "        if candidate.root_cause_type != RootCauseType.INTERACTION_CHANNEL_CATEGORY.value:\n"
    "            continue\n"
    "        if _has_verified_interaction_mechanism_evidence(\n"
    "            repository=repository,\n"
    "            run_id=run_id,\n"
    "            candidate=candidate,\n"
    "            required_bad_direction=True,\n"
    "        ):\n"
    "            return candidate.model_copy(\n"
    "                update={\n"
    "                    \"evidence_ids\": _interaction_evidence_ids(\n"
    "                        repository=repository,\n"
    "                        run_id=run_id,\n"
    "                        pairs=_candidate_pairs(candidate),\n"
    "                        base_evidence_ids=candidate.evidence_ids,\n"
    "                    )\n"
    "                }\n"
    "            )\n"
    "    return None\n\n\n",
)

replace_once(
    "        if candidate.dimension != \"channel\":\n"
    "            continue\n"
    "        if not _has_matching_signal_evidence(\n"
    "            repository=repository,\n"
    "            run_id=run_id,\n"
    "            candidate=candidate,\n"
    "            required_bad_direction=required_bad_direction,\n"
    "        ):\n",
    "        if candidate.dimension != \"channel\":\n"
    "            continue\n"
    "        if not _has_matching_signal_evidence(\n"
    "            repository=repository,\n"
    "            run_id=run_id,\n"
    "            candidate=candidate,\n"
    "            required_bad_direction=required_bad_direction,\n"
    "            required_signal_type=\"campaign\",\n"
    "        ):\n",
)

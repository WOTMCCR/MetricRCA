from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected one replacement, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "metric_rca/runtime/plan_compiler.py",
    "    if experience_advice is None:\n"
    "        return list(canonical_lanes)\n",
    "    if experience_advice is None or experience_advice.memory_mode != \"priority_only\":\n"
    "        return list(canonical_lanes)\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "        if required_signal_type is not None and signal_type != required_signal_type:\n"
    "            continue\n",
    "        if required_signal_type == \"interaction\" and signal_type != \"interaction\":\n"
    "            continue\n"
    "        if (\n"
    "            required_signal_type not in {None, \"interaction\"}\n"
    "            and signal_type not in {None, required_signal_type}\n"
    "        ):\n"
    "            continue\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "    interaction_verified_candidate = _interaction_verified_ranked_candidate(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n"
    "        metric_id=metric_id,\n"
    "        ranked_candidates=ranked_candidates,\n"
    "    )\n"
    "    signal_verified_candidate = _signal_verified_ranked_candidate(\n",
    "    interaction_verified_candidate = _interaction_verified_ranked_candidate(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n"
    "        metric_id=metric_id,\n"
    "        ranked_candidates=ranked_candidates,\n"
    "    )\n"
    "    signal_verified_non_interaction_candidate = (\n"
    "        _signal_verified_non_interaction_candidate_for_unverified_interaction(\n"
    "            repository=repository,\n"
    "            run_id=run_id,\n"
    "            persisted_selected_candidate=persisted_selected_candidate,\n"
    "            ranked_candidates=ranked_candidates,\n"
    "        )\n"
    "    )\n"
    "    signal_verified_candidate = _signal_verified_ranked_candidate(\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "    elif interaction_verified_candidate is not None:\n"
    "        selected_candidate = interaction_verified_candidate\n"
    "        candidates = _selected_first_with_diverse_top3(selected_candidate, ranked_candidates)\n"
    "    elif signal_verified_candidate is not None:\n",
    "    elif interaction_verified_candidate is not None:\n"
    "        selected_candidate = interaction_verified_candidate\n"
    "        candidates = _selected_first_with_diverse_top3(selected_candidate, ranked_candidates)\n"
    "    elif signal_verified_non_interaction_candidate is not None:\n"
    "        selected_candidate = signal_verified_non_interaction_candidate\n"
    "        candidates = _selected_first_with_diverse_top3(selected_candidate, ranked_candidates)\n"
    "    elif signal_verified_candidate is not None:\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "def _embedded_verified_ranked_candidate(\n",
    "def _signal_verified_non_interaction_candidate_for_unverified_interaction(\n"
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
    "    if _has_verified_interaction_mechanism_evidence(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n"
    "        candidate=persisted_selected_candidate,\n"
    "        required_bad_direction=True,\n"
    "    ):\n"
    "        return None\n"
    "    selected_primary_pair = _primary_pair(persisted_selected_candidate)\n"
    "    if selected_primary_pair is None:\n"
    "        return None\n"
    "    required_bad_direction = _target_bad_direction(repository=repository, run_id=run_id)\n"
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
    "            required_signal_type=_signal_type_for_candidate(candidate),\n"
    "        ):\n"
    "            return candidate\n"
    "    return None\n\n\n"
    "def _embedded_verified_ranked_candidate(\n",
)

replace_once(
    "tests/test_tools.py",
    "            \"dimension\": \"channel\",\n"
    "            \"element\": \"paid_ads\",\n"
    "            \"delta_pct\": 0.02,\n"
    "            \"is_anomaly\": False,\n",
    "            \"signal_type\": \"campaign\",\n"
    "            \"dimension\": \"channel\",\n"
    "            \"element\": \"paid_ads\",\n"
    "            \"delta_pct\": 0.02,\n"
    "            \"is_anomaly\": False,\n",
)

replace_once(
    "tests/test_tools.py",
    "            \"dimension\": \"category\",\n"
    "            \"element\": \"electronics\",\n"
    "            \"delta_pct\": 0.01,\n"
    "            \"is_anomaly\": False,\n"
    "            \"bad_direction\": False,\n"
    "        },\n"
    "    }\n"
    "    repo.persisted_evidence[\"run-1:E4_channel\"] = {\n",
    "            \"signal_type\": \"inventory\",\n"
    "            \"dimension\": \"category\",\n"
    "            \"element\": \"electronics\",\n"
    "            \"delta_pct\": 0.01,\n"
    "            \"is_anomaly\": False,\n"
    "            \"bad_direction\": False,\n"
    "        },\n"
    "    }\n"
    "    repo.persisted_evidence[\"run-1:E3_ch_int\"] = {\n"
    "        \"evidence_id\": \"run-1:E3_ch_int\",\n"
    "        \"run_id\": \"run-1\",\n"
    "        \"guard_status\": \"passed\",\n"
    "        \"result_summary\": {\n"
    "            \"signal_type\": \"interaction\",\n"
    "            \"dimension\": \"channel\",\n"
    "            \"element\": \"paid_ads\",\n"
    "            \"is_anomaly\": True,\n"
    "            \"bad_direction\": True,\n"
    "        },\n"
    "    }\n"
    "    repo.persisted_evidence[\"run-1:E3_cat_int\"] = {\n"
    "        \"evidence_id\": \"run-1:E3_cat_int\",\n"
    "        \"run_id\": \"run-1\",\n"
    "        \"guard_status\": \"passed\",\n"
    "        \"result_summary\": {\n"
    "            \"signal_type\": \"interaction\",\n"
    "            \"dimension\": \"category\",\n"
    "            \"element\": \"electronics\",\n"
    "            \"is_anomaly\": True,\n"
    "            \"bad_direction\": True,\n"
    "        },\n"
    "    }\n"
    "    repo.persisted_evidence[\"run-1:E4_channel\"] = {\n",
)

replace_once(
    "tests/test_tools.py",
    '    assert "run-1:E3_cat_electronics" in selected["evidence_ids"]\n',
    '    assert "run-1:E3_ch_int" in selected["evidence_ids"]\n'
    '    assert "run-1:E3_cat_int" in selected["evidence_ids"]\n',
)

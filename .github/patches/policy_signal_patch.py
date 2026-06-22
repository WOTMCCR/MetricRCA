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
    "metric_rca/runtime/ranking.py",
    "from typing import Any\n\nfrom metric_rca.domain.enums import RootCauseType\n",
    "from typing import Any\n\nfrom metric_rca.business.policy_registry import select_signal_type\nfrom metric_rca.domain.enums import RootCauseType\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "    embedded_verified_candidate = _embedded_verified_ranked_candidate(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n",
    "    embedded_verified_candidate = _embedded_verified_ranked_candidate(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n"
    "        metric_id=metric_id,\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "        _signal_verified_non_interaction_candidate_for_unverified_interaction(\n"
    "            repository=repository,\n"
    "            run_id=run_id,\n",
    "        _signal_verified_non_interaction_candidate_for_unverified_interaction(\n"
    "            repository=repository,\n"
    "            run_id=run_id,\n"
    "            metric_id=metric_id,\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "    signal_verified_candidate = _signal_verified_ranked_candidate(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n",
    "    signal_verified_candidate = _signal_verified_ranked_candidate(\n"
    "        repository=repository,\n"
    "        run_id=run_id,\n"
    "        metric_id=metric_id,\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "def _signal_verified_ranked_candidate(\n"
    "    *,\n"
    "    repository: Any,\n"
    "    run_id: str,\n",
    "def _signal_verified_ranked_candidate(\n"
    "    *,\n"
    "    repository: Any,\n"
    "    run_id: str,\n"
    "    metric_id: str,\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "        required_signal_type=_signal_type_for_candidate(persisted_selected_candidate),\n",
    "        required_signal_type=_signal_type_for_candidate(\n"
    "            metric_id=metric_id,\n"
    "            candidate=persisted_selected_candidate,\n"
    "        ),\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "def _signal_verified_non_interaction_candidate_for_unverified_interaction(\n"
    "    *,\n"
    "    repository: Any,\n"
    "    run_id: str,\n",
    "def _signal_verified_non_interaction_candidate_for_unverified_interaction(\n"
    "    *,\n"
    "    repository: Any,\n"
    "    run_id: str,\n"
    "    metric_id: str,\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "            required_signal_type=_signal_type_for_candidate(candidate),\n",
    "            required_signal_type=_signal_type_for_candidate(\n"
    "                metric_id=metric_id,\n"
    "                candidate=candidate,\n"
    "            ),\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "def _embedded_verified_ranked_candidate(\n"
    "    *,\n"
    "    repository: Any,\n"
    "    run_id: str,\n",
    "def _embedded_verified_ranked_candidate(\n"
    "    *,\n"
    "    repository: Any,\n"
    "    run_id: str,\n"
    "    metric_id: str,\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "            required_signal_type=\"campaign\",\n",
    "            required_signal_type=_signal_type_for_candidate(\n"
    "                metric_id=metric_id,\n"
    "                candidate=candidate,\n"
    "            ),\n",
)

replace_once(
    "metric_rca/runtime/ranking.py",
    "def _signal_type_for_candidate(candidate: RootCauseCandidate) -> str | None:\n"
    "    by_root_cause = {\n"
    "        RootCauseType.CAMPAIGN_TRAFFIC_DROP.value: \"campaign\",\n"
    "        RootCauseType.CONVERSION_DROP.value: \"conversion\",\n"
    "        RootCauseType.STOCKOUT.value: \"inventory\",\n"
    "        RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value: \"refund_quality\",\n"
    "        RootCauseType.INTERACTION_CHANNEL_CATEGORY.value: \"interaction\",\n"
    "    }\n"
    "    return by_root_cause.get(candidate.root_cause_type)\n",
    "def _signal_type_for_candidate(\n"
    "    *,\n"
    "    metric_id: str,\n"
    "    candidate: RootCauseCandidate,\n"
    ") -> str | None:\n"
    "    if candidate.dimension is None:\n"
    "        return None\n"
    "    try:\n"
    "        return select_signal_type(\n"
    "            metric_id=metric_id,\n"
    "            dimension=candidate.dimension,\n"
    "            root_cause_type=candidate.root_cause_type,\n"
    "        )\n"
    "    except ValueError:\n"
    "        return None\n",
)

replace_once(
    "metric_rca/runtime/plan_compiler.py",
    "    actions: list[RcaAction] = []\n"
    "    e4_aliases: list[str] = []\n"
    "    selection_aliases: list[str] = []\n"
    "    e3_aliases: list[str] = []\n",
    "    actions: list[RcaAction] = []\n",
)

replace_once(
    "metric_rca/runtime/plan_compiler.py",
    "        e4_alias_set.add(e4_alias)\n"
    "        e3_alias_set.add(e3_alias)\n"
    "        e4_aliases.append(e4_alias)\n"
    "        e3_aliases.append(e3_alias)\n",
    "        e4_alias_set.add(e4_alias)\n"
    "        e3_alias_set.add(e3_alias)\n",
)

replace_once(
    "metric_rca/runtime/plan_compiler.py",
    "            dynamic_selection_aliases.add(selection_alias)\n"
    "            selection_aliases.append(selection_alias)\n"
    "            selection_alias_by_lane[lane_key] = selection_alias\n",
    "            dynamic_selection_aliases.add(selection_alias)\n"
    "            selection_alias_by_lane[lane_key] = selection_alias\n",
)

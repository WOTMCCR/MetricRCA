from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "metric_rca/runtime/plan_compiler.py"


def replace_once(old: str, new: str) -> None:
    source = PATH.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"plan_compiler.py: expected one replacement, found {count}")
    PATH.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "def _broad_actions(parsed_intent: ParsedIntent, policy: DiscoveryPolicy, *, validate_dimensions: Any) -> list[RcaAction]:\n",
    "def _broad_actions(\n"
    "    parsed_intent: ParsedIntent,\n"
    "    policy: DiscoveryPolicy,\n"
    "    *,\n"
    "    validate_dimensions: Any,\n"
    "    experience_advice: AttributionExperienceAdvice | None = None,\n"
    ") -> list[RcaAction]:\n",
)

replace_once(
    "                policy=policy,\n"
    "                first_action_index=len(actions) + 2,\n"
    "                validate_dimensions=validate_dimensions,\n"
    "            )\n"
    "        )\n"
    "        return actions\n"
    "    next_index = len(actions) + 2\n",
    "                policy=policy,\n"
    "                first_action_index=len(actions) + 2,\n"
    "                validate_dimensions=validate_dimensions,\n"
    "                experience_advice=experience_advice,\n"
    "            )\n"
    "        )\n"
    "        return actions\n"
    "    next_index = len(actions) + 2\n",
)

replace_once(
    "def _parallel_broad_contribution_chains(\n"
    "    *,\n"
    "    parsed_intent: ParsedIntent,\n"
    "    policy: DiscoveryPolicy,\n"
    "    first_action_index: int,\n"
    "    validate_dimensions: Any,\n"
    "    scoped_elements: dict[str, str] | None = None,\n"
    "    scoped_filters: dict[str, dict[str, str]] | None = None,\n"
    "    explicit_scope: dict[str, str] | None = None,\n"
    ") -> list[RcaAction]:\n"
    "    lanes = _discovery_lanes(\n"
    "        parsed_intent=parsed_intent,\n"
    "        policy=policy,\n"
    "        validate_dimensions=validate_dimensions,\n"
    "    )\n"
    "    actions: list[RcaAction] = []\n"
    "    e4_aliases: list[str] = []\n"
    "    selection_aliases: list[str] = []\n"
    "    e3_aliases: list[str] = []\n",
    "def _parallel_broad_contribution_chains(\n"
    "    *,\n"
    "    parsed_intent: ParsedIntent,\n"
    "    policy: DiscoveryPolicy,\n"
    "    first_action_index: int,\n"
    "    validate_dimensions: Any,\n"
    "    scoped_elements: dict[str, str] | None = None,\n"
    "    scoped_filters: dict[str, dict[str, str]] | None = None,\n"
    "    explicit_scope: dict[str, str] | None = None,\n"
    "    experience_advice: AttributionExperienceAdvice | None = None,\n"
    ") -> list[RcaAction]:\n"
    "    canonical_lanes = _discovery_lanes(\n"
    "        parsed_intent=parsed_intent,\n"
    "        policy=policy,\n"
    "        validate_dimensions=validate_dimensions,\n"
    "    )\n"
    "    lanes = _prioritize_discovery_lanes(canonical_lanes, experience_advice)\n"
    "    actions: list[RcaAction] = []\n"
    "    e4_aliases: list[str] = []\n"
    "    selection_aliases: list[str] = []\n"
    "    e3_aliases: list[str] = []\n"
    "    e4_alias_by_lane: dict[tuple[str, str, str | None], str] = {}\n"
    "    selection_alias_by_lane: dict[tuple[str, str, str | None], str] = {}\n"
    "    e3_alias_by_lane: dict[tuple[str, str, str | None], str] = {}\n",
)

replace_once(
    "        e4_alias_set.add(e4_alias)\n"
    "        e3_alias_set.add(e3_alias)\n"
    "        e4_aliases.append(e4_alias)\n"
    "        e3_aliases.append(e3_alias)\n"
    "        scope_policy_args = _explicit_scope_policy_args(lane, explicit_scope=explicit_scope)\n",
    "        e4_alias_set.add(e4_alias)\n"
    "        e3_alias_set.add(e3_alias)\n"
    "        e4_aliases.append(e4_alias)\n"
    "        e3_aliases.append(e3_alias)\n"
    "        lane_key = _discovery_lane_key(lane)\n"
    "        e4_alias_by_lane[lane_key] = e4_alias\n"
    "        e3_alias_by_lane[lane_key] = e3_alias\n"
    "        scope_policy_args = _explicit_scope_policy_args(lane, explicit_scope=explicit_scope)\n",
)

replace_once(
    "            dynamic_selection_aliases.add(selection_alias)\n"
    "            selection_aliases.append(selection_alias)\n"
    "            actions.append(\n",
    "            dynamic_selection_aliases.add(selection_alias)\n"
    "            selection_aliases.append(selection_alias)\n"
    "            selection_alias_by_lane[lane_key] = selection_alias\n"
    "            actions.append(\n",
)

replace_once(
    "    actions.append(\n"
    "        RcaAction(\n"
    "            action_id=f\"A{next_index}\",\n"
    "            kind=\"merge_contribution_sets\",\n"
    "            args={\n"
    "                \"metric_id\": parsed_intent.metric_id,\n"
    "                \"target_date\": parsed_intent.target_date,\n"
    "                \"source_evidence_aliases\": e4_aliases,\n"
    "            },\n"
    "            requires=e4_aliases,\n"
    "            produces=[\"E4\"],\n"
    "        )\n"
    "    )\n"
    "    actions.append(\n"
    "        RcaAction(\n"
    "            action_id=f\"A{next_index + 1}\",\n"
    "            kind=\"rank_root_causes\",\n"
    "            args={\"metric_id\": parsed_intent.metric_id, \"target_date\": parsed_intent.target_date},\n"
    "            requires=_ordered_unique_list(\n"
    "                [\n"
    "                    \"E1\",\n"
    "                    *[f\"E2_{dimension}\" for dimension in policy.required_drilldowns],\n"
    "                    *selection_aliases,\n"
    "                    *e3_aliases,\n"
    "                    *e4_aliases,\n"
    "                    \"E4\",\n"
    "                ]\n"
    "            ),\n"
    "            produces=[\"E_rank\"],\n"
    "        )\n"
    "    )\n"
    "    return actions\n",
    "    canonical_e4_aliases = [e4_alias_by_lane[_discovery_lane_key(lane)] for lane in canonical_lanes]\n"
    "    canonical_e3_aliases = [e3_alias_by_lane[_discovery_lane_key(lane)] for lane in canonical_lanes]\n"
    "    canonical_selection_aliases = [\n"
    "        selection_alias_by_lane[_discovery_lane_key(lane)]\n"
    "        for lane in canonical_lanes\n"
    "        if _discovery_lane_key(lane) in selection_alias_by_lane\n"
    "    ]\n"
    "    merge_args: dict[str, Any] = {\n"
    "        \"metric_id\": parsed_intent.metric_id,\n"
    "        \"target_date\": parsed_intent.target_date,\n"
    "        \"source_evidence_aliases\": canonical_e4_aliases,\n"
    "    }\n"
    "    if experience_advice is not None:\n"
    "        merge_args[\"experience_advice\"] = experience_advice.model_dump(mode=\"json\")\n"
    "    actions.append(\n"
    "        RcaAction(\n"
    "            action_id=f\"A{next_index}\",\n"
    "            kind=\"merge_contribution_sets\",\n"
    "            args=merge_args,\n"
    "            requires=canonical_e4_aliases,\n"
    "            produces=[\"E4\"],\n"
    "        )\n"
    "    )\n"
    "    actions.append(\n"
    "        RcaAction(\n"
    "            action_id=f\"A{next_index + 1}\",\n"
    "            kind=\"rank_root_causes\",\n"
    "            args={\"metric_id\": parsed_intent.metric_id, \"target_date\": parsed_intent.target_date},\n"
    "            requires=_ordered_unique_list(\n"
    "                [\n"
    "                    \"E1\",\n"
    "                    *[f\"E2_{dimension}\" for dimension in policy.required_drilldowns],\n"
    "                    *canonical_selection_aliases,\n"
    "                    *canonical_e3_aliases,\n"
    "                    *canonical_e4_aliases,\n"
    "                    \"E4\",\n"
    "                ]\n"
    "            ),\n"
    "            produces=[\"E_rank\"],\n"
    "        )\n"
    "    )\n"
    "    return actions\n",
)

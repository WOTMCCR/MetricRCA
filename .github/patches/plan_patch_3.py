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
    "def _compatibility_alias(*, field: str, declared: str | None, allocated: str) -> str:\n",
    "def _prioritize_discovery_lanes(\n"
    "    canonical_lanes: list[DiscoveryLane],\n"
    "    experience_advice: AttributionExperienceAdvice | None,\n"
    ") -> list[DiscoveryLane]:\n"
    "    if experience_advice is None:\n"
    "        return list(canonical_lanes)\n"
    "    lanes_by_key = {_discovery_lane_key(lane): lane for lane in canonical_lanes}\n"
    "    prioritized: list[DiscoveryLane] = []\n"
    "    for lane_ref in experience_advice.execution_lane_priority:\n"
    "        lane = lanes_by_key.get(lane_ref.key())\n"
    "        if lane is not None and lane not in prioritized:\n"
    "            prioritized.append(lane)\n"
    "    for lane in canonical_lanes:\n"
    "        if lane not in prioritized:\n"
    "            prioritized.append(lane)\n"
    "    if {_discovery_lane_key(lane) for lane in prioritized} != set(lanes_by_key):\n"
    "        raise PlanCompilerError(\"EXPERIENCE_POLICY_INVALID\", \"experience priority changed lane coverage\")\n"
    "    return prioritized\n\n\n"
    "def _discovery_lane_key(lane: DiscoveryLane) -> tuple[str, str, str | None]:\n"
    "    return (lane.dimension, lane.signal_type, lane.alias_discriminator)\n\n\n"
    "def _compatibility_alias(*, field: str, declared: str | None, allocated: str) -> str:\n",
)

replace_once(
    "def _policy_with_memory_hints(\n"
    "    parsed_intent: ParsedIntent,\n"
    "    policy: DiscoveryPolicy,\n"
    "    memory_hints: list[CasePrior],\n"
    "    *,\n"
    "    validate_dimensions: Any,\n"
    ") -> DiscoveryPolicy:\n"
    "    if parsed_intent.analysis_strategy != \"standard\":\n"
    "        return policy\n"
    "    if not memory_hints or not policy.required_drilldowns:\n"
    "        return policy\n"
    "    for hint in sorted(memory_hints, key=lambda item: item.confidence, reverse=True):\n"
    "        if hint.metric_id != parsed_intent.metric_id or hint.confidence < 0.70:\n"
    "            continue\n"
    "        preferred_signal_types = set(hint.preferred_signal_types)\n"
    "        for dimension in hint.preferred_dimensions:\n"
    "            if dimension not in policy.required_drilldowns:\n"
    "                continue\n"
    "            try:\n"
    "                signal_type = _signal_type_for_metric_dimension(\n"
    "                    parsed_intent.metric_id,\n"
    "                    dimension,\n"
    "                    validate_dimensions=validate_dimensions,\n"
    "                )\n"
    "            except PlanCompilerError:\n"
    "                continue\n"
    "            if preferred_signal_types and signal_type not in preferred_signal_types:\n"
    "                continue\n"
    "            return DiscoveryPolicy(\n"
    "                required_drilldowns=policy.required_drilldowns,\n"
    "                first_signal_dimension=dimension,\n"
    "                first_signal_type=signal_type,\n"
    "                first_signal_element=None,\n"
    "                enforce_first_signal_top_candidate=False,\n"
    "                element_selection=policy.element_selection,\n"
    "            )\n"
    "    return policy\n\n\n",
    "",
)

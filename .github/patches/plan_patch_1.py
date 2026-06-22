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
    "from metric_rca.runtime.plan_validator import PlanValidationError, validate_plan_actions\n"
    "from metric_rca.business.discovery_policy import DiscoveryLane, DiscoveryPolicy, discovery_policy_from_intent\n",
    "from metric_rca.runtime.plan_validator import PlanValidationError, validate_plan_actions\n"
    "from metric_rca.business.attribution_experience import (\n"
    "    AttributionExperienceAdvice,\n"
    "    AttributionExperienceAdvisor,\n"
    ")\n"
    "from metric_rca.business.discovery_policy import DiscoveryLane, DiscoveryPolicy, discovery_policy_from_intent\n",
)

replace_once(
    "class RcaPlanCompiler:\n"
    "    def __init__(self, *, metric_service: MetricMetadataProvider | None = None) -> None:\n"
    "        self._metric_service = metric_service\n",
    "class RcaPlanCompiler:\n"
    "    def __init__(\n"
    "        self,\n"
    "        *,\n"
    "        metric_service: MetricMetadataProvider | None = None,\n"
    "        experience_advisor: AttributionExperienceAdvisor | None = None,\n"
    "    ) -> None:\n"
    "        self._metric_service = metric_service\n"
    "        self._experience_advisor = experience_advisor or AttributionExperienceAdvisor()\n",
)

replace_once(
    '        scope_mode = "unscoped"\n',
    '        scope_mode = "unscoped"\n'
    '        experience_advice: AttributionExperienceAdvice | None = None\n',
)

replace_once(
    "            if discovery_policy.scope_mode == \"explicit_multi_driver\":\n"
    "                actions.extend(\n"
    "                    _discovery_actions(\n"
    "                        parsed_intent,\n"
    "                        discovery_policy,\n"
    "                        validate_dimensions=validate_dimensions,\n"
    "                        explicit_scope=explicit_scope,\n"
    "                    )\n"
    "                )\n"
    "                scope_mode = \"explicit_multi_driver\"\n",
    "            if discovery_policy.scope_mode == \"explicit_multi_driver\":\n"
    "                experience_advice = self._experience_advisor.advise(\n"
    "                    parsed_intent=parsed_intent,\n"
    "                    available_lanes=_discovery_lanes(\n"
    "                        parsed_intent=parsed_intent,\n"
    "                        policy=discovery_policy,\n"
    "                        validate_dimensions=validate_dimensions,\n"
    "                    ),\n"
    "                    memory_hints=(),\n"
    "                    allow_memory_priority=False,\n"
    "                )\n"
    "                actions.extend(\n"
    "                    _discovery_actions(\n"
    "                        parsed_intent,\n"
    "                        discovery_policy,\n"
    "                        validate_dimensions=validate_dimensions,\n"
    "                        explicit_scope=explicit_scope,\n"
    "                        experience_advice=experience_advice,\n"
    "                    )\n"
    "                )\n"
    "                scope_mode = \"explicit_multi_driver\"\n",
)

replace_once(
    "        else:\n"
    "            discovery_policy = _discovery_policy_for_intent(parsed_intent, validate_dimensions=validate_dimensions)\n"
    "            actions.extend(\n"
    "                _broad_actions(\n"
    "                    parsed_intent,\n"
    "                    _policy_with_memory_hints(\n"
    "                        parsed_intent,\n"
    "                        discovery_policy,\n"
    "                        memory_hints or [],\n"
    "                        validate_dimensions=validate_dimensions,\n"
    "                    ),\n"
    "                    validate_dimensions=validate_dimensions,\n"
    "                )\n"
    "            )\n",
    "        else:\n"
    "            discovery_policy = _discovery_policy_for_intent(parsed_intent, validate_dimensions=validate_dimensions)\n"
    "            experience_advice = self._experience_advisor.advise(\n"
    "                parsed_intent=parsed_intent,\n"
    "                available_lanes=_discovery_lanes(\n"
    "                    parsed_intent=parsed_intent,\n"
    "                    policy=discovery_policy,\n"
    "                    validate_dimensions=validate_dimensions,\n"
    "                ),\n"
    "                memory_hints=memory_hints or [],\n"
    "                allow_memory_priority=not explicit_scope,\n"
    "            )\n"
    "            actions.extend(\n"
    "                _broad_actions(\n"
    "                    parsed_intent,\n"
    "                    discovery_policy,\n"
    "                    validate_dimensions=validate_dimensions,\n"
    "                    experience_advice=experience_advice,\n"
    "                )\n"
    "            )\n",
)

replace_once(
    "            budget=_plan_budget(actions, budget),\n"
    "            memory_hints=memory_hints or [],\n"
    "        )\n",
    "            budget=_plan_budget(actions, budget),\n"
    "            memory_hints=memory_hints or [],\n"
    "            experience_advice=experience_advice,\n"
    "        )\n",
)

replace_once(
    "def _discovery_actions(\n"
    "    parsed_intent: ParsedIntent,\n"
    "    policy: DiscoveryPolicy,\n"
    "    *,\n"
    "    validate_dimensions: Any,\n"
    "    explicit_scope: dict[str, str] | None = None,\n"
    ") -> list[RcaAction]:\n",
    "def _discovery_actions(\n"
    "    parsed_intent: ParsedIntent,\n"
    "    policy: DiscoveryPolicy,\n"
    "    *,\n"
    "    validate_dimensions: Any,\n"
    "    explicit_scope: dict[str, str] | None = None,\n"
    "    experience_advice: AttributionExperienceAdvice | None = None,\n"
    ") -> list[RcaAction]:\n",
)

replace_once(
    "            explicit_scope=explicit_scope,\n"
    "            scoped_filters=scoped_filters if explicit_scope else None,\n"
    "        )\n"
    "    )\n"
    "    return actions\n\n\n"
    "def _scoped_interaction_actions(\n",
    "            explicit_scope=explicit_scope,\n"
    "            scoped_filters=scoped_filters if explicit_scope else None,\n"
    "            experience_advice=experience_advice,\n"
    "        )\n"
    "    )\n"
    "    return actions\n\n\n"
    "def _scoped_interaction_actions(\n",
)

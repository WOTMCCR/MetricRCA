from __future__ import annotations

from datetime import date

import pytest

from metric_rca.business.attribution_experience import (
    AttributionExperienceAdvisor,
    AttributionExperienceCatalog,
    AttributionExperienceError,
)
from metric_rca.business.policy_registry import GMV_STANDARD_DISCOVERY_LANES
from metric_rca.runtime.plan_models import CasePrior
from metric_rca.services.metric_contracts import ParsedIntent


def _gmv_intent() -> ParsedIntent:
    return ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 1),
        question_family="gmv_drop",
        analysis_strategy="standard",
    )


def _lane_keys(lanes) -> list[tuple[str, str, str | None]]:
    return [(lane.dimension, lane.signal_type, lane.alias_discriminator) for lane in lanes]


def test_default_catalog_resolves_generic_gmv_playbook() -> None:
    advisor = AttributionExperienceAdvisor()
    advice = advisor.advise(
        parsed_intent=_gmv_intent(),
        available_lanes=GMV_STANDARD_DISCOVERY_LANES,
    )

    assert advice is not None
    assert advice.playbook_id == "gmv-business-decomposition-v1"
    assert advice.memory_mode == "disabled"
    assert _lane_keys(advice.required_lanes) == _lane_keys(GMV_STANDARD_DISCOVERY_LANES)
    assert {hypothesis.hypothesis_id for hypothesis in advice.hypotheses} >= {
        "traffic-volume",
        "conversion-quality",
        "inventory-availability",
        "basket-value",
        "channel-category-interaction",
        "residual-gap",
    }


def test_memory_can_only_prioritize_an_existing_lane() -> None:
    advisor = AttributionExperienceAdvisor()
    prior = CasePrior(
        metric_id="gmv",
        preferred_dimensions=["product", "not_a_policy_dimension"],
        preferred_signal_types=["inventory"],
        prior_root_causes=["stockout"],
        confidence=0.92,
        source_memory_ids=["memory-1"],
    )

    advice = advisor.advise(
        parsed_intent=_gmv_intent(),
        available_lanes=GMV_STANDARD_DISCOVERY_LANES,
        memory_hints=[prior],
    )

    assert advice is not None
    assert advice.memory_mode == "priority_only"
    assert advice.source_memory_ids == ["memory-1"]
    assert _lane_keys(advice.execution_lane_priority)[0] == ("product", "inventory", None)
    assert set(_lane_keys(advice.execution_lane_priority)) == set(_lane_keys(GMV_STANDARD_DISCOVERY_LANES))
    assert _lane_keys(advice.required_lanes) == _lane_keys(GMV_STANDARD_DISCOVERY_LANES)


def test_nonstandard_strategy_disables_memory_priority() -> None:
    advisor = AttributionExperienceAdvisor()
    intent = _gmv_intent().model_copy(update={"analysis_strategy": "channel_first"})
    prior = CasePrior(
        metric_id="gmv",
        preferred_dimensions=["product"],
        preferred_signal_types=["inventory"],
        confidence=0.99,
        source_memory_ids=["memory-1"],
    )

    advice = advisor.advise(
        parsed_intent=intent,
        available_lanes=GMV_STANDARD_DISCOVERY_LANES,
        memory_hints=[prior],
    )

    assert advice is not None
    assert advice.memory_mode == "disabled"
    assert advice.source_memory_ids == []
    assert _lane_keys(advice.execution_lane_priority)[0] == ("channel", "campaign", None)


def test_catalog_rejects_answer_bearing_fields_before_validation() -> None:
    with pytest.raises(AttributionExperienceError) as exc:
        AttributionExperienceCatalog.from_mapping(
            {
                "version": 1,
                "playbooks": [
                    {
                        "playbook_id": "invalid",
                        "metric_ids": ["gmv"],
                        "case_id": "forbidden",
                    }
                ],
            }
        )

    assert exc.value.code == "EXPERIENCE_ANSWER_BEARING_CONFIG"


def test_catalog_rejects_evaluation_identity_in_free_text() -> None:
    with pytest.raises(AttributionExperienceError) as exc:
        AttributionExperienceCatalog.from_mapping(
            {
                "version": 1,
                "playbooks": [
                    {
                        "playbook_id": "invalid",
                        "metric_ids": ["gmv"],
                        "hypotheses": [
                            {
                                "hypothesis_id": "bad",
                                "pitfalls": ["copy MC06_net_gmv_multi_driver"],
                            }
                        ],
                    }
                ],
            }
        )

    assert exc.value.code == "EXPERIENCE_ANSWER_BEARING_CONFIG"

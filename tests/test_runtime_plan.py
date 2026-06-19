from __future__ import annotations

from datetime import date

import pytest

from metric_rca.domain.models import MetricDefinition
from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_compiler import PlanCompilerError, RcaPlanCompiler
from metric_rca.runtime.plan_models import CasePrior
from metric_rca.services.metric_contracts import ParsedIntent


def _compiler(*, family: str = "gmv_family") -> RcaPlanCompiler:
    return RcaPlanCompiler(metric_service=_MetricCatalog(family))


class _MetricCatalog:
    def __init__(self, family: str) -> None:
        self.family = family

    def get_metric_definition(self, metric_id: str) -> MetricDefinition:
        return MetricDefinition(
            metric_id=metric_id,
            display_name=metric_id,
            formula="test",
            metric_family=self.family,
            source_table="fact_order",
            allowed_dimensions=["channel", "category", "device", "product"],
        )


def test_plan_compiler_builds_explicit_slice_chain() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        dimension="channel",
        element="paid_ads",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    assert [action.kind for action in plan.actions] == [
        "detect_anomaly",
        "drilldown_dimension",
        "fetch_related_signal",
        "calculate_contribution",
        "rank_root_causes",
    ]
    assert plan.explicit_scope == {"channel": "paid_ads"}
    assert plan.actions[2].args["signal_type"] == "campaign"
    assert plan.actions[2].args["element"] == "paid_ads"
    assert plan.actions[2].produces == ["E3"]
    assert plan.actions[3].requires == ["E1", "E2_channel", "E3"]


def test_plan_compiler_builds_broad_uv_discovery_from_metric_policy() -> None:
    parsed = ParsedIntent(
        metric_id="uv",
        target_date=date(2026, 6, 5),
        question_family="uv_drop",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    assert [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"] == [
        "channel"
    ]
    select_action = next(action for action in plan.actions if action.kind == "select_signal_element")
    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal")
    contribution_action = next(action for action in plan.actions if action.kind == "calculate_contribution")
    rank_action = plan.actions[-1]
    assert select_action.args["dimension"] == "channel"
    assert select_action.args["signal_type"] == "campaign"
    assert select_action.produces == ["E_select_channel"]
    assert signal_action.args["signal_type"] == "campaign"
    assert signal_action.args["dimension"] == "channel"
    assert signal_action.requires == ["E1", "E2_channel", "E_select_channel"]
    assert signal_action.dynamic is True
    assert contribution_action.requires == ["E1", "E2_channel", "E_select_channel", "E3"]
    assert rank_action.requires == ["E1", "E2_channel", "E_select_channel", "E3", "E4"]


def test_plan_compiler_builds_refund_discovery_even_with_nonstandard_strategy() -> None:
    parsed = ParsedIntent(
        metric_id="refund_rate",
        target_date=date(2026, 6, 5),
        question_family="refund_rate_increase",
        analysis_strategy="channel_first",
    )

    plan = _compiler(family="rate_family").compile(run_id="run-1", parsed_intent=parsed)

    drilldowns = [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"]
    assert drilldowns == ["product"]
    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal")
    assert signal_action.args["dimension"] == "product"
    assert signal_action.args["signal_type"] == "refund_quality"
    assert signal_action.args["element_selection"] == "signal_level"
    assert signal_action.dynamic is True


def test_plan_compiler_builds_pay_cvr_channel_first_multi_signal_discovery() -> None:
    parsed = ParsedIntent(
        metric_id="pay_cvr",
        target_date=date(2026, 5, 28),
        question_family="pay_cvr_drop",
        analysis_strategy="standard",
    )

    plan = _compiler(family="rate_family").compile(run_id="run-1", parsed_intent=parsed)

    drilldowns = [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"]
    signal_actions = [action for action in plan.actions if action.kind == "fetch_related_signal"]
    contribution_actions = [action for action in plan.actions if action.kind == "calculate_contribution"]
    merge_action = next(action for action in plan.actions if action.kind == "merge_contribution_sets")

    assert drilldowns == ["channel", "device"]
    assert [action.args["dimension"] for action in signal_actions] == ["channel", "device"]
    assert all(action.args["signal_type"] == "conversion" for action in signal_actions)
    assert [action.args["dimension"] for action in contribution_actions] == ["channel", "device"]
    assert merge_action.args["source_evidence_aliases"] == ["E4_channel", "E4_device"]


def test_plan_compiler_builds_broad_gmv_product_first_with_all_required_drilldowns() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        analysis_strategy="product_first",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    drilldowns = [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"]
    assert drilldowns == ["channel", "category", "product"]
    signal_actions = [action for action in plan.actions if action.kind == "fetch_related_signal"]
    assert signal_actions[0].args["dimension"] == "product"
    assert signal_actions[0].args["signal_type"] == "inventory"
    rank_action = plan.actions[-1]
    assert rank_action.requires[-1] == "E4"


def test_plan_compiler_builds_signal_first_gmv_without_hardcoded_element() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        analysis_strategy="signal_first",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    select_action = next(action for action in plan.actions if action.kind == "select_signal_element")
    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal" and action.args["dimension"] == "channel")
    contribution_action = next(action for action in plan.actions if action.kind == "calculate_contribution" and action.args["dimension"] == "channel")
    rank_action = plan.actions[-1]
    assert select_action.args["dimension"] == "channel"
    assert select_action.args["signal_type"] == "campaign"
    assert select_action.args["element_selection"] == "signal_anomaly"
    assert select_action.requires == ["E1", "E2_channel"]
    assert select_action.produces == ["E_select_channel"]
    assert signal_action.args["dimension"] == "channel"
    assert signal_action.args["signal_type"] == "campaign"
    assert signal_action.args["element"] is None
    assert signal_action.args["element_selection"] == "signal_anomaly"
    assert signal_action.requires == ["E1", "E2_channel", "E_select_channel"]
    assert signal_action.dynamic is True
    assert contribution_action.args["element"] is None
    assert contribution_action.args["element_selection"] == "signal_anomaly"
    assert contribution_action.requires == ["E1", "E2_channel", "E_select_channel", "E3_ch"]
    assert contribution_action.dynamic is True
    assert "E_select_channel" in rank_action.requires


def test_plan_compiler_builds_broad_gmv_multisignal_discovery_lanes() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 1),
        question_family="gmv_drop",
        analysis_strategy="standard",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    selections = [action for action in plan.actions if action.kind == "select_signal_element"]
    signal_actions = [action for action in plan.actions if action.kind == "fetch_related_signal"]
    contribution_actions = [action for action in plan.actions if action.kind == "calculate_contribution"]
    merge_action = next(action for action in plan.actions if action.kind == "merge_contribution_sets")

    assert [(action.args["dimension"], action.args["signal_type"], action.produces[0]) for action in selections] == [
        ("channel", "campaign", "E_select_channel"),
        ("channel", "conversion", "E_select_ch_conversion"),
        ("category", "inventory", "E_select_category"),
        ("product", "inventory", "E_select_product"),
        ("channel", "interaction", "E_select_ch_interaction"),
        ("category", "interaction", "E_select_cat_interaction"),
    ]
    assert [(action.args["dimension"], action.args["signal_type"], action.produces[0]) for action in signal_actions] == [
        ("channel", "campaign", "E3_ch"),
        ("channel", "conversion", "E3_ch_conversion"),
        ("category", "inventory", "E3_cat"),
        ("product", "inventory", "E3_prod"),
        ("channel", "interaction", "E3_ch_interaction"),
        ("category", "interaction", "E3_cat_interaction"),
    ]
    assert [(action.args["dimension"], action.args["evidence_alias"]) for action in contribution_actions] == [
        ("channel", "E4_channel"),
        ("channel", "E4_channel_conversion"),
        ("category", "E4_category"),
        ("product", "E4_product"),
        ("channel", "E4_channel_interaction"),
        ("category", "E4_category_interaction"),
    ]
    assert merge_action.args["source_evidence_aliases"] == [
        "E4_channel",
        "E4_channel_conversion",
        "E4_category",
        "E4_product",
        "E4_channel_interaction",
        "E4_category_interaction",
    ]
    assert len({action.produces[0] for action in selections}) == len(selections)
    assert len({action.produces[0] for action in signal_actions}) == len(signal_actions)


def test_plan_compiler_sizes_multisignal_gmv_budget_from_action_costs() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 1),
        question_family="gmv_drop",
        analysis_strategy="standard",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    assert len(plan.actions) == 24
    assert plan.budget["max_steps"] == 24
    assert plan.budget["max_drilldown_depth"] == 3
    assert plan.budget["max_query"] >= 70


@pytest.mark.parametrize(
    ("question_family", "expected_dimension", "expected_signal_type"),
    [
        ("channel_gmv_anomaly", "channel", "campaign"),
        ("category_gmv_anomaly", "category", "inventory"),
    ],
)
def test_plan_compiler_builds_broad_gmv_anomaly_discovery_policy(
    question_family: str,
    expected_dimension: str,
    expected_signal_type: str,
) -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 2),
        question_family=question_family,
        analysis_strategy="standard",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    drilldowns = [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"]
    select_action = next(action for action in plan.actions if action.kind == "select_signal_element")
    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal")
    assert drilldowns == ["channel", "category", "product"]
    assert select_action.args["dimension"] == expected_dimension
    assert select_action.args["signal_type"] == expected_signal_type
    assert signal_action.args["dimension"] == expected_dimension
    assert signal_action.args["signal_type"] == expected_signal_type


@pytest.mark.parametrize(
    ("metric_id", "question_family"),
    [
        ("gmv", "interaction_gmv_anomaly"),
        ("uv", "interaction_uv_anomaly"),
    ],
)
def test_plan_compiler_builds_cross_dimension_interaction_chains(metric_id: str, question_family: str) -> None:
    parsed = ParsedIntent(
        metric_id=metric_id,
        target_date=date(2026, 5, 31),
        question_family=question_family,
        analysis_strategy="standard",
    )

    plan = _compiler(family="gmv_family" if metric_id == "gmv" else "rate_family").compile(
        run_id="run-1",
        parsed_intent=parsed,
    )

    drilldowns = [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"]
    signal_actions = [action for action in plan.actions if action.kind == "fetch_related_signal"]
    merge_action = next(action for action in plan.actions if action.kind == "merge_contribution_sets")

    assert drilldowns == ["channel", "category"]
    assert [action.args["dimension"] for action in signal_actions] == ["channel", "category"]
    assert all(action.args["signal_type"] == "interaction" for action in signal_actions)
    assert merge_action.args["source_evidence_aliases"] == ["E4_channel", "E4_category"]


def test_plan_compiler_builds_scoped_channel_category_interaction_chains() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 5, 31),
        question_family="interaction_gmv_anomaly",
        analysis_strategy="standard",
        filters={"channel": "paid_ads", "category": "electronics"},
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    drilldowns = [action for action in plan.actions if action.kind == "drilldown_dimension"]
    signal_actions = [action for action in plan.actions if action.kind == "fetch_related_signal"]
    contribution_actions = [action for action in plan.actions if action.kind == "calculate_contribution"]
    merge_action = next(action for action in plan.actions if action.kind == "merge_contribution_sets")

    assert plan.explicit_scope == {"channel": "paid_ads", "category": "electronics"}
    assert [action.args["dimension"] for action in drilldowns] == ["channel", "category"]
    assert drilldowns[0].args["filters"] == {"category": "electronics"}
    assert drilldowns[1].args["filters"] == {"channel": "paid_ads"}
    assert [action.args["dimension"] for action in signal_actions] == ["channel", "category"]
    assert all(action.args["signal_type"] == "interaction" for action in signal_actions)
    assert signal_actions[0].args["element"] == "paid_ads"
    assert signal_actions[0].args["filters"] == {"category": "electronics"}
    assert signal_actions[1].args["element"] == "electronics"
    assert signal_actions[1].args["filters"] == {"channel": "paid_ads"}
    assert [action.args["element"] for action in contribution_actions] == ["paid_ads", "electronics"]
    assert merge_action.args["source_evidence_aliases"] == ["E4_channel", "E4_category"]


def test_plan_compiler_expands_net_gmv_paid_ads_slice_to_multi_driver_discovery() -> None:
    parsed = ParsedIntent(
        metric_id="net_gmv",
        target_date=date(2026, 5, 29),
        question_family="net_gmv_drop",
        analysis_strategy="standard",
        dimension="channel",
        element="paid_ads",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    drilldowns = [action for action in plan.actions if action.kind == "drilldown_dimension"]
    selections = [action for action in plan.actions if action.kind == "select_signal_element"]
    signal_actions = [action for action in plan.actions if action.kind == "fetch_related_signal"]
    contribution_actions = [action for action in plan.actions if action.kind == "calculate_contribution"]
    merge_action = next(action for action in plan.actions if action.kind == "merge_contribution_sets")

    assert plan.explicit_scope == {"channel": "paid_ads"}
    assert [action.args["dimension"] for action in drilldowns] == ["channel", "category"]
    assert [action.args["filters"] for action in drilldowns] == [{}, {"channel": "paid_ads"}]
    assert [(action.args["dimension"], action.args["signal_type"], action.produces[0]) for action in selections] == [
        ("category", "inventory", "E_select_category"),
        ("channel", "conversion", "E_select_ch_conversion"),
    ]
    assert [action.args["filters"] for action in selections] == [{}, {}]
    assert [(action.args["dimension"], action.args["signal_type"], action.args["element"]) for action in signal_actions] == [
        ("channel", "campaign", "paid_ads"),
        ("category", "inventory", None),
        ("channel", "conversion", None),
    ]
    assert [(action.produces, action.args["evidence_alias"]) for action in signal_actions] == [
        (["E3_ch_campaign"], "E3_ch_campaign"),
        (["E3_cat"], "E3_cat"),
        (["E3_ch_conversion"], "E3_ch_conversion"),
    ]
    assert [action.dynamic for action in signal_actions] == [False, True, True]
    assert [action.args["filters"] for action in signal_actions] == [{}, {}, {}]
    assert signal_actions[2].args["element_selection"] == "signal_anomaly"
    assert signal_actions[2].args["explicit_scope_policy"] == "global_explanatory"
    assert [action.requires[-1] for action in contribution_actions] == [
        "E3_ch_campaign",
        "E3_cat",
        "E3_ch_conversion",
    ]
    assert [(action.args["dimension"], action.args["element"], action.args["evidence_alias"]) for action in contribution_actions] == [
        ("channel", "paid_ads", "E4_channel"),
        ("category", None, "E4_category"),
        ("channel", None, "E4_channel_conversion"),
    ]
    assert [action.args["filters"] for action in contribution_actions] == [{}, {"channel": "paid_ads"}, {}]
    assert contribution_actions[2].args["explicit_scope_policy"] == "global_explanatory"
    assert merge_action.args["source_evidence_aliases"] == ["E4_channel", "E4_category", "E4_channel_conversion"]
    assert plan.scope_mode == "explicit_multi_driver"
    assert "metric_rca/runtime/ranking.py" not in str(plan.model_dump(mode="json"))


def test_plan_compiler_uses_memory_prior_to_change_discovery_signal_dimension() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        analysis_strategy="standard",
    )
    prior = CasePrior(
        metric_id="gmv",
        preferred_dimensions=["product"],
        preferred_signal_types=["inventory"],
        prior_root_causes=["stockout"],
        confidence=0.82,
        source_memory_ids=["mem-product-stockout"],
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed, memory_hints=[prior])

    drilldowns = [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"]
    select_action = next(action for action in plan.actions if action.kind == "select_signal_element")
    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal" and action.args["dimension"] == "product")
    contribution_action = next(action for action in plan.actions if action.kind == "calculate_contribution" and action.args["dimension"] == "product")
    rank_action = plan.actions[-1]
    assert drilldowns == ["channel", "category", "product"]
    assert select_action.args["dimension"] == "product"
    assert select_action.args["signal_type"] == "inventory"
    assert select_action.produces == ["E_select_product"]
    assert signal_action.args["dimension"] == "product"
    assert signal_action.args["signal_type"] == "inventory"
    assert signal_action.requires == ["E1", "E2_product", "E_select_product"]
    assert contribution_action.requires == ["E1", "E2_product", "E_select_product", "E3_prod"]
    assert rank_action.requires[-1] == "E4"


def test_plan_compiler_does_not_let_memory_override_explicit_analysis_strategy() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        analysis_strategy="product_first",
    )
    prior = CasePrior(
        metric_id="gmv",
        preferred_dimensions=["channel"],
        preferred_signal_types=["campaign"],
        prior_root_causes=["campaign_traffic_drop"],
        confidence=0.92,
        source_memory_ids=["mem-campaign-prior"],
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed, memory_hints=[prior])

    select_action = next(action for action in plan.actions if action.kind == "select_signal_element")
    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal")
    assert select_action.args["dimension"] == "product"
    assert select_action.args["signal_type"] == "inventory"
    assert signal_action.args["dimension"] == "product"
    assert signal_action.args["signal_type"] == "inventory"


def test_plan_compiler_builds_parallel_broad_gmv_contribution_chains() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 1),
        question_family="gmv_drop",
        analysis_strategy="standard",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    contribution_actions = [action for action in plan.actions if action.kind == "calculate_contribution"]
    merge_action = next(action for action in plan.actions if action.kind == "merge_contribution_sets")
    rank_action = plan.actions[-1]

    assert [action.args["dimension"] for action in contribution_actions] == [
        "channel",
        "channel",
        "category",
        "product",
        "channel",
        "category",
    ]
    assert [action.produces for action in contribution_actions] == [
        ["E4_channel"],
        ["E4_channel_conversion"],
        ["E4_category"],
        ["E4_product"],
        ["E4_channel_interaction"],
        ["E4_category_interaction"],
    ]
    assert [action.args["evidence_alias"] for action in contribution_actions] == [
        "E4_channel",
        "E4_channel_conversion",
        "E4_category",
        "E4_product",
        "E4_channel_interaction",
        "E4_category_interaction",
    ]
    assert merge_action.requires == [
        "E4_channel",
        "E4_channel_conversion",
        "E4_category",
        "E4_product",
        "E4_channel_interaction",
        "E4_category_interaction",
    ]
    assert merge_action.produces == ["E4"]
    assert merge_action.args["source_evidence_aliases"] == [
        "E4_channel",
        "E4_channel_conversion",
        "E4_category",
        "E4_product",
        "E4_channel_interaction",
        "E4_category_interaction",
    ]
    assert rank_action.requires[-1] == "E4"


def test_plan_compiler_keeps_explicit_slice_single_canonical_e4() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 1),
        question_family="gmv_drop",
        dimension="channel",
        element="paid_ads",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    assert "merge_contribution_sets" not in [action.kind for action in plan.actions]
    contribution_action = next(action for action in plan.actions if action.kind == "calculate_contribution")
    assert contribution_action.produces == ["E4"]
    assert "evidence_alias" not in contribution_action.args


def test_plan_compiler_does_not_require_selection_evidence_for_explicit_slice() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        dimension="channel",
        element="paid_ads",
    )

    plan = _compiler().compile(run_id="run-1", parsed_intent=parsed)

    assert "select_signal_element" not in [action.kind for action in plan.actions]
    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal")
    contribution_action = next(action for action in plan.actions if action.kind == "calculate_contribution")
    rank_action = next(action for action in plan.actions if action.kind == "rank_root_causes")
    assert signal_action.requires == ["E1", "E2_channel"]
    assert contribution_action.requires == ["E1", "E2_channel", "E3"]
    assert "E_select_channel" not in rank_action.requires


def test_plan_compiler_fails_fast_when_unscoped_metric_has_no_discovery_policy() -> None:
    parsed = ParsedIntent(
        metric_id="aov",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
    )

    with pytest.raises(PlanCompilerError) as excinfo:
        _compiler().compile(run_id="run-1", parsed_intent=parsed)

    assert excinfo.value.code == "DISCOVERY_POLICY_MISSING"


def test_evidence_graph_scopes_and_matches_current_run_aliases() -> None:
    graph = EvidenceGraph(run_id="run-1")

    graph.add_ids(["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_paid_ads"])

    assert graph.has_alias("E1") is True
    assert graph.has_alias("E2") is True
    assert graph.has_alias("E3_ch") is True
    assert graph.matching("E2") == ["run-1:E2_channel"]
    assert {"E1", "E2", "E2_channel", "E3", "E3_ch_paid_ads"}.issubset(graph.aliases())


def test_evidence_graph_rejects_foreign_run_evidence() -> None:
    graph = EvidenceGraph(run_id="run-1")

    with pytest.raises(ValueError, match="EVIDENCE_SCOPE_INVALID"):
        graph.add_ids(["other-run:E1"])

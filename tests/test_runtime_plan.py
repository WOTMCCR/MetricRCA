from __future__ import annotations

from datetime import date

import pytest

from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_compiler import PlanCompilerError, RcaPlanCompiler
from metric_rca.services.metric_contracts import ParsedIntent


def test_plan_compiler_builds_explicit_slice_chain() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        dimension="channel",
        element="paid_ads",
    )

    plan = RcaPlanCompiler().compile(run_id="run-1", parsed_intent=parsed)

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
    assert plan.actions[3].requires == ["E1", "E2_channel", "E3"]


def test_plan_compiler_builds_broad_uv_discovery_from_metric_policy() -> None:
    parsed = ParsedIntent(
        metric_id="uv",
        target_date=date(2026, 6, 5),
        question_family="uv_drop",
    )

    plan = RcaPlanCompiler().compile(run_id="run-1", parsed_intent=parsed)

    assert [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"] == [
        "channel"
    ]
    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal")
    assert signal_action.args["signal_type"] == "campaign"
    assert signal_action.args["dimension"] == "channel"
    assert signal_action.dynamic is True


def test_plan_compiler_builds_refund_discovery_even_with_nonstandard_strategy() -> None:
    parsed = ParsedIntent(
        metric_id="refund_rate",
        target_date=date(2026, 6, 5),
        question_family="refund_rate_increase",
        analysis_strategy="channel_first",
    )

    plan = RcaPlanCompiler().compile(run_id="run-1", parsed_intent=parsed)

    drilldowns = [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"]
    assert drilldowns == ["product"]
    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal")
    assert signal_action.args["dimension"] == "product"
    assert signal_action.args["signal_type"] == "refund_quality"
    assert signal_action.args["element_selection"] == "signal_level"
    assert signal_action.dynamic is True


def test_plan_compiler_builds_broad_gmv_product_first_with_all_required_drilldowns() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        analysis_strategy="product_first",
    )

    plan = RcaPlanCompiler().compile(run_id="run-1", parsed_intent=parsed)

    drilldowns = [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"]
    assert drilldowns == ["channel", "category", "product"]
    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal")
    assert signal_action.args["dimension"] == "product"
    assert signal_action.args["signal_type"] == "inventory"
    rank_action = plan.actions[-1]
    assert rank_action.requires == ["E1", "E2_channel", "E2_category", "E2_product", "E3", "E4"]


def test_plan_compiler_builds_signal_first_gmv_without_hardcoded_element() -> None:
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        analysis_strategy="signal_first",
    )

    plan = RcaPlanCompiler().compile(run_id="run-1", parsed_intent=parsed)

    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal")
    contribution_action = next(action for action in plan.actions if action.kind == "calculate_contribution")
    assert signal_action.args["dimension"] == "channel"
    assert signal_action.args["signal_type"] == "campaign"
    assert signal_action.args["element"] is None
    assert signal_action.args["element_selection"] == "signal_anomaly"
    assert signal_action.dynamic is True
    assert contribution_action.args["element"] is None
    assert contribution_action.args["element_selection"] == "signal_anomaly"
    assert contribution_action.dynamic is True


def test_plan_compiler_fails_fast_when_unscoped_metric_has_no_discovery_policy() -> None:
    parsed = ParsedIntent(
        metric_id="aov",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
    )

    with pytest.raises(PlanCompilerError) as excinfo:
        RcaPlanCompiler().compile(run_id="run-1", parsed_intent=parsed)

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

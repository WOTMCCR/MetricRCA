from __future__ import annotations

from metric_rca.domain.models import ContributionSet, RootCauseCandidate
from metric_rca.runtime.contribution_set_builder import ContributionSetBuilder


def test_contribution_set_builder_merges_cross_chain_candidates_and_deduplicates() -> None:
    paid_ads_low = _candidate("campaign_traffic_drop", "channel", "paid_ads", 0.32, ["run-1:E4_channel"])
    paid_ads_high = _candidate("campaign_traffic_drop", "channel", "paid_ads", 0.52, ["run-1:E4_category"])
    electronics = _candidate("stockout", "category", "electronics", 0.95, ["run-1:E4_category"])
    channel_set = ContributionSet(
        selected_candidate=paid_ads_low,
        candidates=[paid_ads_low],
        evidence_ids=["run-1:E1", "run-1:E4_channel"],
        factor_graph={"chains": [{"dimension": "channel"}]},
        selection_evidence_id="run-1:E_select_channel",
    )
    category_set = ContributionSet(
        selected_candidate=electronics,
        candidates=[electronics, paid_ads_high],
        evidence_ids=["run-1:E1", "run-1:E4_category"],
        factor_graph={"chains": [{"dimension": "category"}]},
        selection_evidence_id="run-1:E_select_category",
    )

    merged = ContributionSetBuilder().merge(
        run_id="run-1",
        source_sets=[("run-1:E4_channel", channel_set), ("run-1:E4_category", category_set)],
    )

    assert merged.selected_candidate.contribution_pct == paid_ads_high.contribution_pct
    assert merged.selected_candidate.evidence_ids == ["run-1:E4_channel", "run-1:E4_category"]
    assert [(c.root_cause_type, c.dimension, c.element) for c in merged.candidates] == [
        ("campaign_traffic_drop", "channel", "paid_ads"),
        ("stockout", "category", "electronics"),
    ]
    assert merged.evidence_ids == ["run-1:E1", "run-1:E4_channel", "run-1:E4_category"]
    assert merged.factor_graph["chain_evidence_ids"] == ["run-1:E4_channel", "run-1:E4_category"]
    assert merged.factor_graph["chains"][0]["dimension"] == "channel"
    assert merged.factor_graph["chains"][1]["dimension"] == "category"


def _candidate(
    root_cause_type: str,
    dimension: str,
    element: str,
    contribution_pct: float,
    evidence_ids: list[str],
) -> RootCauseCandidate:
    return RootCauseCandidate(
        root_cause_type=root_cause_type,
        dimension=dimension,
        element=element,
        contribution_pct=contribution_pct,
        signal_severity=1.0,
        evidence_support=1.0,
        eng_confidence=contribution_pct,
        verdict="likely",
        evidence_ids=evidence_ids,
    )

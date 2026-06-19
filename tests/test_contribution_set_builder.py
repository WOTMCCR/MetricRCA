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


def test_contribution_set_builder_keeps_representatives_and_prunes_duplicate_interactions() -> None:
    paid_ads = _candidate(
        "campaign_traffic_drop",
        "channel",
        "paid_ads",
        0.72,
        ["run-1:E4_channel"],
        signal_severity=0.44,
        eng_confidence=0.32,
    )
    affiliate_campaign = _candidate(
        "campaign_traffic_drop",
        "channel",
        "affiliate",
        0.28,
        ["run-1:E4_channel"],
        signal_severity=0.32,
        eng_confidence=0.28,
    )
    social_noise = _candidate(
        "campaign_traffic_drop",
        "channel",
        "social",
        0.001,
        ["run-1:E4_channel"],
        signal_severity=0.001,
        eng_confidence=0.0,
    )
    affiliate_conversion = _candidate(
        "conversion_drop",
        "channel",
        "affiliate",
        0.28,
        ["run-1:E4_channel_conversion"],
        signal_severity=0.32,
        eng_confidence=0.09,
    )
    electronics = _candidate(
        "stockout",
        "category",
        "electronics",
        0.48,
        ["run-1:E4_category"],
        signal_severity=1.0,
        eng_confidence=0.48,
    )
    fashion_tail = _candidate(
        "stockout",
        "category",
        "fashion",
        0.29,
        ["run-1:E4_category"],
        signal_severity=0.19,
        eng_confidence=0.55,
    )
    product_two = _candidate(
        "stockout",
        "product",
        "2",
        0.24,
        ["run-1:E4_product"],
        signal_severity=1.0,
        eng_confidence=0.24,
    )
    interaction_paid_ads = _candidate(
        "interaction_channel_category",
        "channel",
        "paid_ads",
        0.72,
        ["run-1:E4_channel_interaction"],
        signal_severity=0.44,
        eng_confidence=0.32,
    )

    merged = ContributionSetBuilder().merge(
        run_id="run-1",
        source_sets=[
            (
                "run-1:E4_channel",
                _set(paid_ads, [paid_ads, affiliate_campaign, social_noise], "run-1:E4_channel"),
            ),
            (
                "run-1:E4_channel_conversion",
                _set(affiliate_conversion, [paid_ads, affiliate_conversion], "run-1:E4_channel_conversion"),
            ),
            (
                "run-1:E4_category",
                _set(electronics, [electronics, fashion_tail], "run-1:E4_category"),
            ),
            ("run-1:E4_product", _set(product_two, [product_two], "run-1:E4_product")),
            (
                "run-1:E4_channel_interaction",
                _set(interaction_paid_ads, [interaction_paid_ads, affiliate_campaign], "run-1:E4_channel_interaction"),
            ),
        ],
    )

    assert [(c.root_cause_type, c.dimension, c.element) for c in merged.candidates] == [
        ("campaign_traffic_drop", "channel", "paid_ads"),
        ("campaign_traffic_drop", "channel", "affiliate"),
        ("conversion_drop", "channel", "affiliate"),
        ("stockout", "category", "electronics"),
        ("stockout", "product", "2"),
    ]
    assert ("channel", "paid_ads") in merged.candidates[0].dimension_elements
    assert ("channel", "affiliate") in merged.candidates[0].dimension_elements
    assert ("channel", "social") not in merged.candidates[0].dimension_elements


def test_contribution_set_builder_drops_non_representative_source_tails() -> None:
    selected = _candidate("campaign_traffic_drop", "channel", "paid_ads", 0.62, ["run-1:E4_channel"])
    material_runner = _candidate("campaign_traffic_drop", "channel", "social", 0.23, ["run-1:E4_channel"])
    weak_tail = _candidate("campaign_traffic_drop", "channel", "affiliate", 0.14, ["run-1:E4_channel"])

    merged = ContributionSetBuilder().merge(
        run_id="run-1",
        source_sets=[("run-1:E4_channel", _set(selected, [selected, material_runner, weak_tail], "run-1:E4_channel"))],
    )

    assert [(c.root_cause_type, c.dimension, c.element) for c in merged.candidates] == [
        ("campaign_traffic_drop", "channel", "paid_ads"),
        ("campaign_traffic_drop", "channel", "social"),
    ]


def test_contribution_set_builder_keeps_only_selected_conversion_source_representatives() -> None:
    paid_ads = _candidate("conversion_drop", "channel", "paid_ads", 0.29, ["run-1:E4_channel"])
    social_runner = _candidate("conversion_drop", "channel", "social", 0.28, ["run-1:E4_channel"])
    affiliate_runner = _candidate("conversion_drop", "channel", "affiliate", 0.24, ["run-1:E4_channel"])
    mobile = _candidate("conversion_drop", "device", "mobile", 0.51, ["run-1:E4_device"])

    merged = ContributionSetBuilder().merge(
        run_id="run-1",
        source_sets=[
            ("run-1:E4_channel", _set(paid_ads, [paid_ads, social_runner, affiliate_runner], "run-1:E4_channel")),
            ("run-1:E4_device", _set(mobile, [mobile], "run-1:E4_device")),
        ],
    )

    assert [(c.root_cause_type, c.dimension, c.element) for c in merged.candidates] == [
        ("conversion_drop", "channel", "paid_ads"),
        ("conversion_drop", "device", "mobile"),
    ]


def test_contribution_set_builder_keeps_cross_dimension_interaction_representatives() -> None:
    paid_ads = _candidate("campaign_traffic_drop", "channel", "paid_ads", 0.79, ["run-1:E4_channel"])
    electronics = _candidate("stockout", "category", "electronics", 0.73, ["run-1:E4_category"])
    channel_interaction = _candidate(
        "interaction_channel_category",
        "channel",
        "paid_ads",
        0.79,
        ["run-1:E4_channel_interaction"],
    )
    category_interaction = _candidate(
        "interaction_channel_category",
        "category",
        "electronics",
        0.73,
        ["run-1:E4_category_interaction"],
    )

    merged = ContributionSetBuilder().merge(
        run_id="run-1",
        source_sets=[
            ("run-1:E4_channel", _set(paid_ads, [paid_ads], "run-1:E4_channel")),
            ("run-1:E4_category", _set(electronics, [electronics], "run-1:E4_category")),
            (
                "run-1:E4_channel_interaction",
                _set(channel_interaction, [channel_interaction], "run-1:E4_channel_interaction"),
            ),
            (
                "run-1:E4_category_interaction",
                _set(category_interaction, [category_interaction], "run-1:E4_category_interaction"),
            ),
        ],
    )

    assert [(c.root_cause_type, c.dimension, c.element) for c in merged.candidates] == [
        ("campaign_traffic_drop", "channel", "paid_ads"),
        ("stockout", "category", "electronics"),
        ("interaction_channel_category", "channel", "paid_ads"),
        ("interaction_channel_category", "category", "electronics"),
    ]


def _set(selected: RootCauseCandidate, candidates: list[RootCauseCandidate], evidence_id: str) -> ContributionSet:
    return ContributionSet(
        selected_candidate=selected,
        candidates=candidates,
        evidence_ids=[evidence_id],
        factor_graph={"chains": [{"dimension": selected.dimension}]},
        selection_evidence_id=None,
    )


def _candidate(
    root_cause_type: str,
    dimension: str,
    element: str,
    contribution_pct: float,
    evidence_ids: list[str],
    *,
    signal_severity: float = 1.0,
    eng_confidence: float | None = None,
    dimension_elements: list[tuple[str, str]] | None = None,
) -> RootCauseCandidate:
    return RootCauseCandidate(
        root_cause_type=root_cause_type,
        dimension=dimension,
        element=element,
        contribution_pct=contribution_pct,
        signal_severity=signal_severity,
        evidence_support=1.0,
        eng_confidence=contribution_pct if eng_confidence is None else eng_confidence,
        verdict="likely",
        evidence_ids=evidence_ids,
        dimension_elements=dimension_elements or [],
    )

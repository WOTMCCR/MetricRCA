from __future__ import annotations

import pytest

from metric_rca.agent.evidence_aliases import (
    allocate_discovery_lane_aliases,
    evidence_alias_fits,
)
from metric_rca.business.policy_registry import DiscoveryLane


def test_allocator_assigns_stable_multisignal_aliases() -> None:
    lanes = allocate_discovery_lane_aliases(
        (
            DiscoveryLane(dimension="channel", signal_type="campaign"),
            DiscoveryLane(
                dimension="channel",
                signal_type="conversion",
                element_selection="signal_anomaly",
            ),
            DiscoveryLane(dimension="category", signal_type="inventory"),
            DiscoveryLane(
                dimension="channel",
                signal_type="interaction",
                element_selection="signal_anomaly",
            ),
            DiscoveryLane(
                dimension="category",
                signal_type="interaction",
                element_selection="signal_anomaly",
            ),
        )
    )

    assert [lane.selection_alias for lane in lanes] == [
        "E_select_channel",
        "E_select_channel_conv",
        "E_select_category",
        "E_select_channel_int",
        "E_select_category_int",
    ]
    assert [lane.signal_evidence_alias for lane in lanes] == [
        "E3_ch",
        "E3_ch_conversion",
        "E3_cat",
        "E3_ch_int",
        "E3_cat_int",
    ]
    assert [lane.evidence_alias for lane in lanes] == [
        "E4_channel",
        "E4_channel_conversion",
        "E4_category",
        "E4_channel_int",
        "E4_category_int",
    ]


def test_allocator_preserves_known_element_signal_identity() -> None:
    lanes = allocate_discovery_lane_aliases(
        (
            DiscoveryLane(
                dimension="channel",
                signal_type="campaign",
                element_binding="explicit_scope",
            ),
        )
    )

    assert lanes[0].signal_evidence_alias == "E3_ch_campaign"


def test_allocator_detects_policy_alias_drift() -> None:
    with pytest.raises(ValueError, match="EVIDENCE_ALIAS_POLICY_DRIFT"):
        allocate_discovery_lane_aliases(
            (
                DiscoveryLane(
                    dimension="channel",
                    signal_type="campaign",
                    evidence_alias="E4_wrong",
                ),
            )
        )


def test_round_20_multisignal_aliases_fit_persisted_id_budget() -> None:
    run_id = "ptv-cycle-20260618-2358-round-4c1db5ea-r2"
    lanes = allocate_discovery_lane_aliases(
        (
            DiscoveryLane(dimension="channel", signal_type="campaign"),
            DiscoveryLane(dimension="channel", signal_type="conversion"),
            DiscoveryLane(dimension="category", signal_type="inventory"),
            DiscoveryLane(dimension="product", signal_type="inventory"),
            DiscoveryLane(dimension="channel", signal_type="interaction"),
            DiscoveryLane(dimension="category", signal_type="interaction"),
        )
    )

    aliases = [
        alias
        for lane in lanes
        for alias in (
            lane.selection_alias,
            lane.signal_evidence_alias,
            lane.evidence_alias,
        )
        if alias is not None
    ]
    assert all(evidence_alias_fits(run_id, alias) for alias in aliases)

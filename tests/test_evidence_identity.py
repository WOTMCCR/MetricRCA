from __future__ import annotations

import pytest

from metric_rca.runtime.evidence_identity import (
    EvidenceIdentityError,
    alias_matches,
    compose_evidence_id,
    lane_evidence_aliases,
    split_evidence_id,
)


def test_evidence_identity_round_trip() -> None:
    evidence_id = compose_evidence_id("run-1", "E_select_channel_int")

    identity = split_evidence_id(evidence_id)

    assert identity.run_id == "run-1"
    assert identity.alias == "E_select_channel_int"
    assert identity.evidence_id == evidence_id


def test_run_id_and_alias_have_independent_budgets() -> None:
    evidence_id = compose_evidence_id("r" * 64, "E_" + "a" * 94)

    assert len(evidence_id) <= 192


def test_overlong_run_id_fails_before_plan_execution() -> None:
    with pytest.raises(EvidenceIdentityError) as excinfo:
        compose_evidence_id("r" * 65, "E1")

    assert excinfo.value.code == "RUN_ID_TOO_LONG"


def test_lane_alias_allocator_preserves_current_gmv_names() -> None:
    campaign = lane_evidence_aliases("channel", "campaign")
    conversion = lane_evidence_aliases("channel", "conversion")
    interaction = lane_evidence_aliases("category", "interaction")

    assert campaign.selection == "E_select_ch_campaign"
    assert campaign.signal == "E3_ch_campaign"
    assert campaign.contribution == "E4_channel_campaign"
    assert conversion.selection == "E_select_channel_conv"
    assert conversion.signal == "E3_ch_conversion"
    assert conversion.contribution == "E4_channel_conversion"
    assert interaction.selection == "E_select_category_int"
    assert interaction.signal == "E3_cat_int"
    assert interaction.contribution == "E4_category_int"


def test_lane_alias_allocator_rejects_unknown_discriminator() -> None:
    with pytest.raises(EvidenceIdentityError) as excinfo:
        lane_evidence_aliases("channel", "new_signal_type")

    assert excinfo.value.code == "EVIDENCE_ALIAS_DISCRIMINATOR_UNKNOWN"


def test_alias_matching_supports_evidence_families() -> None:
    assert alias_matches("E2_channel", "E2") is True
    assert alias_matches("E_select_channel_int", "E_select_channel") is True
    assert alias_matches("E3_cat_int", "E3_ch") is False

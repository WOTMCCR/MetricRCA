from __future__ import annotations

from typing import Any

from metric_rca.reporting.projector import (
    numeric_claims_from_e4,
    project_candidate_from_e4,
    project_candidates_from_e4,
)


def test_legacy_selected_candidate_fixture_is_migrated_out_of_production_projection() -> None:
    summary = {
        "selected_candidate": _legacy_candidate(),
        "candidates": [_legacy_candidate()],
    }

    assert _legacy_project_candidate(summary) == {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": "paid_ads",
        "verdict": "confirmed",
    }
    assert project_candidate_from_e4(summary) is None
    assert project_candidates_from_e4(summary) == []
    assert numeric_claims_from_e4(summary, "run-legacy:E4") == []


def _legacy_project_candidate(summary: dict[str, Any]) -> dict[str, Any] | None:
    selected = summary.get("selected_candidate")
    if not isinstance(selected, dict):
        return None
    projected = {
        "root_cause_type": selected.get("root_cause_type"),
        "dimension": selected.get("dimension"),
        "element": selected.get("element"),
        "verdict": selected.get("verdict"),
    }
    if any(value in (None, "") for value in projected.values()):
        return None
    return projected


def _legacy_candidate() -> dict[str, Any]:
    return {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": "paid_ads",
        "contribution_pct": 0.9,
        "eng_confidence": 0.85,
        "verdict": "confirmed",
        "evidence_ids": ["run-legacy:E1", "run-legacy:E2", "run-legacy:E3", "run-legacy:E4"],
    }

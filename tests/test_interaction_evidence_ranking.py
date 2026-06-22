from __future__ import annotations

from metric_rca.domain.models import RootCauseCandidate
from metric_rca.runtime.ranking import (
    _interaction_promoted_candidate,
    _interaction_verified_ranked_candidate,
    _signal_verified_ranked_candidate,
)


class _EvidenceRepository:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def get_evidences(self, run_id: str) -> list[dict]:
        return [row for row in self._rows if row.get("run_id") == run_id]

    def get_evidence(self, *, run_id: str, evidence_id: str):
        for row in self._rows:
            if row.get("run_id") == run_id and row.get("evidence_id") == evidence_id:
                return row
        return None


def _candidate(root_cause_type: str) -> RootCauseCandidate:
    return RootCauseCandidate(
        root_cause_type=root_cause_type,
        dimension="channel",
        element="channel_a",
        dimension_elements=[("channel", "channel_a"), ("category", "category_b")],
        contribution_pct=0.70,
        signal_severity=0.80,
        evidence_support=1.0,
        eng_confidence=0.56,
        verdict="likely",
        evidence_ids=["run:E4"],
    )


def _row(alias: str, summary: dict) -> dict:
    return {
        "run_id": "run",
        "evidence_id": f"run:{alias}",
        "guard_status": "passed",
        "result_summary": summary,
    }


def _repository(*, include_category_interaction: bool) -> _EvidenceRepository:
    rows = [
        _row("E1", {"is_anomaly": True, "bad_direction": True}),
        _row(
            "E3_channel_int",
            {
                "signal_type": "interaction",
                "dimension": "channel",
                "element": "channel_a",
                "is_anomaly": True,
                "bad_direction": True,
            },
        ),
        _row(
            "E3_channel_campaign",
            {
                "signal_type": "campaign",
                "dimension": "channel",
                "element": "channel_a",
                "is_anomaly": True,
                "bad_direction": True,
            },
        ),
    ]
    if include_category_interaction:
        rows.append(
            _row(
                "E3_category_int",
                {
                    "signal_type": "interaction",
                    "dimension": "category",
                    "element": "category_b",
                    "is_anomaly": True,
                    "bad_direction": True,
                },
            )
        )
    return _EvidenceRepository(rows)


def test_two_sided_interaction_evidence_beats_same_cell_campaign() -> None:
    repository = _repository(include_category_interaction=True)
    campaign = _candidate("campaign_traffic_drop")
    interaction = _candidate("interaction_channel_category")

    selected = _interaction_verified_ranked_candidate(
        repository=repository,
        run_id="run",
        metric_id="gmv",
        ranked_candidates=[campaign, interaction],
    )

    assert selected is not None
    assert selected.root_cause_type == "interaction_channel_category"
    assert _signal_verified_ranked_candidate(
        repository=repository,
        run_id="run",
        metric_id="gmv",
        persisted_selected_candidate=interaction,
        ranked_candidates=[campaign, interaction],
    ) is None


def test_one_sided_interaction_evidence_cannot_override_campaign() -> None:
    repository = _repository(include_category_interaction=False)
    campaign = _candidate("campaign_traffic_drop")
    interaction = _candidate("interaction_channel_category")

    assert (
        _interaction_verified_ranked_candidate(
            repository=repository,
            run_id="run",
            metric_id="gmv",
            ranked_candidates=[interaction, campaign],
        )
        is None
    )
    assert (
        _interaction_promoted_candidate(
            repository=repository,
            run_id="run",
            metric_id="gmv",
            ranked_candidates=[interaction, campaign],
        )
        is None
    )

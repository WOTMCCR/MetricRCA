from __future__ import annotations

from datetime import date
from typing import Any

from metric_rca.agent.tools.merge_contribution_sets import merge_contribution_sets
from metric_rca.agent.tools.schemas import MergeContributionSetsArgs
from metric_rca.domain.models import ContributionSet, RootCauseCandidate


def test_merge_contribution_sets_persists_canonical_e4_without_sql() -> None:
    repo = _Repo(
        {
            "run-1:E4_channel": _e4_row(
                "run-1:E4_channel",
                _candidate("campaign_traffic_drop", "channel", "paid_ads", 0.52),
                decomposition={"largest_drop_factor": "aov"},
            ),
            "run-1:E4_category": _e4_row(
                "run-1:E4_category",
                _candidate("stockout", "category", "electronics", 0.35),
            ),
        }
    )

    result = merge_contribution_sets(
        MergeContributionSetsArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 1),
            source_evidence_aliases=["E4_channel", "E4_category"],
        ),
        repository=repo,
    )

    assert result.observation.ok is True
    assert result.sql_count == 0
    assert result.observation.evidence_ids == ["run-1:E4"]
    persisted = repo.persisted["run-1:E4"]
    contribution_set = persisted["result_summary"]["contribution_set"]
    assert contribution_set["selected_candidate"]["element"] == "paid_ads"
    assert [candidate["element"] for candidate in contribution_set["candidates"]] == ["paid_ads", "electronics"]
    assert contribution_set["evidence_ids"] == ["run-1:E4_channel", "run-1:E4_category", "run-1:E4"]
    assert contribution_set["selected_candidate"]["evidence_ids"][-1] == "run-1:E4"
    assert persisted["result_summary"]["decomposition"] == {"largest_drop_factor": "aov"}
    assert persisted["sql_text"] == "SELECT 1"


def test_merge_contribution_sets_persists_representative_candidate_projection() -> None:
    paid_ads = _candidate("campaign_traffic_drop", "channel", "paid_ads", 0.72)
    affiliate = _candidate("campaign_traffic_drop", "channel", "affiliate", 0.28)
    weak_tail = _candidate("campaign_traffic_drop", "channel", "social", 0.001)
    interaction_paid_ads = _candidate("interaction_channel_category", "channel", "paid_ads", 0.72)
    repo = _Repo(
        {
            "run-1:E4_channel": _e4_row(
                "run-1:E4_channel",
                paid_ads,
                candidates=[paid_ads, affiliate, weak_tail],
            ),
            "run-1:E4_channel_interaction": _e4_row(
                "run-1:E4_channel_interaction",
                interaction_paid_ads,
                candidates=[interaction_paid_ads],
            ),
        }
    )

    result = merge_contribution_sets(
        MergeContributionSetsArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 1),
            source_evidence_aliases=["E4_channel", "E4_channel_interaction"],
        ),
        repository=repo,
    )

    assert result.observation.ok is True
    persisted = repo.persisted["run-1:E4"]
    contribution_set = persisted["result_summary"]["contribution_set"]
    assert [
        (candidate["root_cause_type"], candidate["dimension"], candidate["element"])
        for candidate in contribution_set["candidates"]
    ] == [
        ("campaign_traffic_drop", "channel", "paid_ads"),
        ("campaign_traffic_drop", "channel", "affiliate"),
    ]
    assert persisted["result_summary"]["candidate_composition_strategy"] == "representative_source_merge_v1"


def test_merge_contribution_sets_resolves_selected_runner_source_summary() -> None:
    paid_ads = _candidate("conversion_drop", "channel", "paid_ads", 0.30, eng_confidence=0.20)
    social_runner = _candidate("conversion_drop", "channel", "social", 0.60, eng_confidence=0.90)
    mobile = _candidate("conversion_drop", "device", "mobile", 0.40, eng_confidence=0.40)
    repo = _Repo(
        {
            "run-1:E4_channel": _e4_row(
                "run-1:E4_channel",
                paid_ads,
                candidates=[paid_ads, social_runner],
                decomposition={"selected_source": "channel"},
            ),
            "run-1:E4_device": _e4_row(
                "run-1:E4_device",
                mobile,
                candidates=[mobile],
                decomposition={"selected_source": "device"},
            ),
        }
    )

    result = merge_contribution_sets(
        MergeContributionSetsArgs(
            run_id="run-1",
            metric_id="pay_cvr",
            target_date=date(2026, 6, 1),
            source_evidence_aliases=["E4_channel", "E4_device"],
        ),
        repository=repo,
    )

    assert result.observation.ok is True
    persisted = repo.persisted["run-1:E4"]
    contribution_set = persisted["result_summary"]["contribution_set"]
    assert (
        contribution_set["selected_candidate"]["root_cause_type"],
        contribution_set["selected_candidate"]["dimension"],
        contribution_set["selected_candidate"]["element"],
    ) == ("conversion_drop", "channel", "social")
    assert persisted["result_summary"]["decomposition"] == {"selected_source": "channel"}


class _Repo:
    def __init__(self, persisted: dict[str, dict[str, Any]]) -> None:
        self.persisted = persisted

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, Any] | None:
        row = self.persisted.get(evidence_id)
        if row is None or row["run_id"] != run_id:
            return None
        return row

    def create_evidence(self, row: dict[str, Any]) -> None:
        self.persisted[row["evidence_id"]] = dict(row)


def _e4_row(
    evidence_id: str,
    selected: RootCauseCandidate,
    *,
    candidates: list[RootCauseCandidate] | None = None,
    decomposition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contribution_candidates = [selected] if candidates is None else candidates
    contribution_set = ContributionSet(
        selected_candidate=selected,
        candidates=contribution_candidates,
        evidence_ids=[evidence_id],
        factor_graph={"dimension": selected.dimension},
        selection_evidence_id=None,
    )
    result_summary = {
        "metric_id": "gmv",
        "contribution_set": contribution_set.model_dump(mode="json"),
        "selected_candidate": selected.model_dump(mode="json"),
        "candidates": [candidate.model_dump(mode="json") for candidate in contribution_candidates],
    }
    if decomposition is not None:
        result_summary["decomposition"] = decomposition
    return {
        "evidence_id": evidence_id,
        "run_id": "run-1",
        "query_spec": {"metric_id": "gmv", "time_range": {"start_date": "2026-06-01", "end_date": "2026-06-01"}},
        "sql_text": "SELECT 1",
        "sql_hash": "0" * 64,
        "guard_status": "passed",
        "data_source": "fact_order",
        "result_summary": result_summary,
    }


def _candidate(
    root_cause_type: str,
    dimension: str,
    element: str,
    contribution_pct: float,
    *,
    eng_confidence: float | None = None,
) -> RootCauseCandidate:
    return RootCauseCandidate(
        root_cause_type=root_cause_type,
        dimension=dimension,
        element=element,
        contribution_pct=contribution_pct,
        signal_severity=1.0,
        evidence_support=1.0,
        eng_confidence=contribution_pct if eng_confidence is None else eng_confidence,
        verdict="likely",
        evidence_ids=[],
    )

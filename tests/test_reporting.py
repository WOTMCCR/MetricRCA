from __future__ import annotations

from datetime import date
from typing import Any

from metric_rca.reporting.projector import (
    build_report_from_persisted_artifacts,
    evidence_by_alias,
    numeric_claims_from_e4,
    project_candidate_from_e4,
    project_candidates_from_e4,
)


def test_projector_builds_report_from_persisted_e4_selected_candidate() -> None:
    report = build_report_from_persisted_artifacts(
        agent_run=_agent_run(status="succeeded"),
        evidences=_evidences(e4_summary={"selected_candidate": _candidate()}),
        tasks=[],
    )

    assert report == {
        "status": "succeeded",
        "metric_id": "gmv",
        "target_date": "2026-06-05",
        "top_candidate": {
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "verdict": "confirmed",
        },
        "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4", "run-1:E_rank"],
        "numeric_claims": [
            {"name": "contribution_pct", "value": 0.9, "evidence_id": "run-1:E4"}
        ],
    }


def test_projector_rejects_missing_e4() -> None:
    report = build_report_from_persisted_artifacts(
        agent_run=_agent_run(status="succeeded"),
        evidences=_evidences(include_e4=False),
        tasks=[],
    )

    assert report is None


def test_projector_rejects_malformed_e4() -> None:
    malformed_inputs = [
        {},
        {"selected_candidate": None},
        {"selected_candidate": {"root_cause_type": "campaign_traffic_drop"}},
        {"selected_candidate": {**_candidate(), "contribution_pct": True}},
    ]

    for summary in malformed_inputs:
        report = build_report_from_persisted_artifacts(
            agent_run=_agent_run(status="succeeded"),
            evidences=_evidences(e4_summary=summary),
            tasks=[],
        )

        assert report is None


def test_projector_rejects_missing_or_malformed_e4_for_succeeded_run() -> None:
    assert (
        build_report_from_persisted_artifacts(
            agent_run=_agent_run(status="succeeded"),
            evidences=_evidences(include_e4=False),
            tasks=[],
        )
        is None
    )
    assert (
        build_report_from_persisted_artifacts(
            agent_run=_agent_run(status="succeeded"),
            evidences=_evidences(e4_summary={"selected_candidate": {"verdict": "confirmed"}}),
            tasks=[],
        )
        is None
    )


def test_projector_report_has_no_unverified_numeric_fields() -> None:
    report = build_report_from_persisted_artifacts(
        agent_run=_agent_run(status="succeeded"),
        evidences=_evidences(e4_summary={"selected_candidate": _candidate()}),
        tasks=[],
    )

    assert report is not None
    assert "contribution_pct" not in report["top_candidate"]
    assert "signal_severity" not in report["top_candidate"]
    assert "evidence_support" not in report["top_candidate"]
    assert "eng_confidence" not in report["top_candidate"]
    assert report["numeric_claims"] == [
        {"name": "contribution_pct", "value": 0.9, "evidence_id": "run-1:E4"}
    ]


def test_projector_rejects_foreign_or_missing_candidate_evidence_ids() -> None:
    foreign = {
        **_candidate(),
        "evidence_ids": ["run-1:E1", "foreign:E2", "run-1:E3", "run-1:E4"],
    }
    missing = {
        **_candidate(),
        "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E4"],
    }

    assert (
        build_report_from_persisted_artifacts(
            agent_run=_agent_run(status="succeeded"),
            evidences=_evidences(e4_summary={"selected_candidate": foreign}),
            tasks=[],
        )
        is None
    )
    assert (
        build_report_from_persisted_artifacts(
            agent_run=_agent_run(status="succeeded"),
            evidences=_evidences(e4_summary={"selected_candidate": missing}),
            tasks=[],
        )
        is None
    )


def test_projector_rejects_candidate_evidence_not_persisted_passed() -> None:
    evidences = _evidences(e4_summary={"selected_candidate": _candidate()})
    evidences[1] = {**evidences[1], "guard_status": "rejected"}

    report = build_report_from_persisted_artifacts(
        agent_run=_agent_run(status="succeeded"),
        evidences=evidences,
        tasks=[],
    )

    assert report is None


def test_projector_no_unverified_numeric_fields() -> None:
    test_projector_report_has_no_unverified_numeric_fields()


def test_projector_no_anomaly_e1_only() -> None:
    report = build_report_from_persisted_artifacts(
        agent_run=_agent_run(status="no_anomaly"),
        evidences=[_evidence("run-1:E1")],
        tasks=[],
    )

    assert report == {
        "status": "no_anomaly",
        "metric_id": "gmv",
        "target_date": "2026-06-05",
        "evidence_ids": ["run-1:E1"],
    }


def test_projector_failed_run_no_report() -> None:
    report = build_report_from_persisted_artifacts(
        agent_run=_agent_run(status="failed", error_code="REFLECTION_REPAIR_FAILED"),
        evidences=_evidences(e4_summary={"selected_candidate": _candidate()}),
        tasks=[],
    )

    assert report is None


def test_projector_no_anomaly_rejects_task_or_candidate() -> None:
    with_task = build_report_from_persisted_artifacts(
        agent_run=_agent_run(status="no_anomaly"),
        evidences=[_evidence("run-1:E1")],
        tasks=[{"task_id": "task-1"}],
    )
    with_extra_evidence = build_report_from_persisted_artifacts(
        agent_run=_agent_run(status="no_anomaly"),
        evidences=[_evidence("run-1:E1"), _evidence("run-1:E2")],
        tasks=[],
    )

    assert with_task is None
    assert with_extra_evidence is None


def test_projector_helpers_are_deterministic_and_alias_scoped() -> None:
    evidences = _evidences(e4_summary={"selected_candidate": _candidate()})
    by_alias = evidence_by_alias([*evidences, _evidence("foreign:E1")], run_id="run-1")
    candidate = project_candidate_from_e4({"selected_candidate": _candidate()})
    claims = numeric_claims_from_e4({"selected_candidate": _candidate()}, "run-1:E4")

    assert sorted(by_alias) == ["E1", "E2", "E3", "E4", "E_rank"]
    assert candidate == {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": "paid_ads",
        "verdict": "confirmed",
    }
    assert claims == [{"name": "contribution_pct", "value": 0.9, "evidence_id": "run-1:E4"}]


def test_project_candidates_from_e4_projects_top_k_display_fields() -> None:
    candidates = project_candidates_from_e4(
        {
            "selected_candidate": _candidate(),
            "candidates": [
                _candidate(),
                {
                    **_candidate(),
                    "element": "organic",
                    "verdict": "likely",
                    "contribution_pct": 0.1,
                    "eng_confidence": 0.25,
                },
            ],
        }
    )

    assert candidates == [
        {
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "verdict": "confirmed",
            "contribution_pct": 0.9,
            "eng_confidence": 0.85,
                "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4", "run-1:E_rank"],
        },
        {
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "organic",
            "verdict": "likely",
            "contribution_pct": 0.1,
            "eng_confidence": 0.25,
                "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4", "run-1:E_rank"],
        },
    ]


def test_project_candidates_from_e4_rejects_malformed_top_k_without_partial_projection() -> None:
    candidates = project_candidates_from_e4(
        {
            "candidates": [
                _candidate(),
                {**_candidate(), "element": ""},
            ],
        }
    )

    assert candidates == []


def _agent_run(*, status: str, error_code: str | None = None) -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "question": "Why did yesterday GMV drop?",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "status": status,
        "error_code": error_code,
    }


def _candidate() -> dict[str, Any]:
    return {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": "paid_ads",
        "contribution_pct": 0.9,
        "signal_severity": 0.8,
        "evidence_support": 1.0,
        "eng_confidence": 0.85,
        "verdict": "confirmed",
        "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4", "run-1:E_rank"],
    }


def _evidence(evidence_id: str, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "run_id": evidence_id.split(":", maxsplit=1)[0],
        "guard_status": "passed",
        "result_summary": summary or {"value": 1.0},
    }


def _evidences(
    *,
    include_e4: bool = True,
    e4_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    evidences = [_evidence("run-1:E1"), _evidence("run-1:E2"), _evidence("run-1:E3")]
    if include_e4:
        evidences.append(_evidence("run-1:E4", summary=e4_summary))
        evidences.append(_evidence("run-1:E_rank", summary=e4_summary))
    return evidences

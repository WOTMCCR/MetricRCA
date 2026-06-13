from __future__ import annotations

from datetime import date, datetime
from typing import Any

from metric_rca.agent.reflection import verify_reflection
from metric_rca.domain.models import Evidence, ReflectionResult, RootCauseCandidate
from metric_rca.guardrails.query_spec import build_query_spec


def test_reflection_missing_evidence_fails_no_report() -> None:
    state = _state(candidates=[_candidate(evidence_ids=[])], evidences=[])

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id={})

    assert result.passed is False
    assert "evidence_coverage" in _checks(result)


def test_reflection_candidate_without_current_run_evidence_fails() -> None:
    state = _state(
        candidates=[_candidate(evidence_ids=["foreign-run:E1"])],
        evidences=[_evidence("run-1:E1")],
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "required_evidence_present" in _checks(result)


def test_reflection_guard_status_not_passed_fails() -> None:
    state = _state(evidences=[_evidence("run-1:E1", guard_status="rejected")])

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "sql_guard_status" in _checks(result)


def test_reflection_untraceable_numeric_claim_fails() -> None:
    state = _state(report={"status": "succeeded", "numeric_claims": [{"name": "drop_pct", "value": 0.12345}]})

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "numeric_traceability" in _checks(result)


def test_reflection_time_range_mismatch_fails() -> None:
    state = _state(evidences=[_evidence("run-1:E1", target_date=date(2026, 6, 4))])

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "time_range_consistency" in _checks(result)


def test_reflection_metric_mismatch_fails() -> None:
    state = _state(evidences=[_evidence("run-1:E1", metric_id="pay_cvr")])

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "metric_consistency" in _checks(result)


def test_reflection_low_attribution_coverage_returns_ATTRIBUTION_COVERAGE_LOW() -> None:
    state = _state(candidates=[_candidate(contribution_pct=0.40, verdict="likely")])

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "ATTRIBUTION_COVERAGE_LOW" in _checks(result)


def test_reflection_no_anomaly_cannot_have_operation_task() -> None:
    state = {
        "run_id": "run-1",
        "status": "no_anomaly",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "candidates": [],
        "evidences": [_evidence("run-1:E1")],
        "operation_tasks": [{"task_id": "run-1:task"}],
    }

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "no_anomaly_task_behavior" in _checks(result)


def test_no_anomaly_with_E2_E3_or_E4_fails_reflection() -> None:
    state = {
        "run_id": "run-1",
        "status": "no_anomaly",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "candidates": [],
        "evidences": [_evidence("run-1:E1"), _evidence("run-1:E2")],
        "trace_nodes": ["detect_anomaly", "rank_root_causes"],
    }

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "no_anomaly_evidence_scope" in _checks(result)
    assert "no_anomaly_downstream_trace" in _checks(result)


def test_reflection_suggests_repair_action_for_missing_e4() -> None:
    state = _state(
        candidates=[_candidate(evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"])],
        evidences=[_evidence("run-1:E1"), _evidence("run-1:E2"), _evidence("run-1:E3")],
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    issue = next(issue for issue in result.issues if issue.suggested_action is not None)
    assert issue.suggested_action.action == "calculate_contribution"
    assert issue.suggested_action.args["evidence_ids"] == ["run-1:E1", "run-1:E2", "run-1:E3"]


def test_reflection_state_only_fabricated_evidence_fails() -> None:
    state = _state()

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id={})

    assert result.passed is False
    assert "persisted_evidence" in _checks(result)


def test_reflection_persisted_evidence_sql_hash_mismatch_fails() -> None:
    state = _state()
    persisted = _persisted_rows(state["evidences"])
    persisted["run-1:E4"]["sql_hash"] = "1" * 64

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=persisted)

    assert result.passed is False
    assert "persisted_evidence" in _checks(result)


def test_reflection_top_candidate_must_match_persisted_e4_selected_candidate() -> None:
    state_candidate = _candidate(contribution_pct=0.90)
    persisted_candidate = _candidate(contribution_pct=0.10)
    evidences = [
        _evidence("run-1:E1"),
        _evidence("run-1:E2"),
        _evidence("run-1:E3"),
        _evidence("run-1:E4", summary={"selected_candidate": state_candidate.model_dump(mode="json")}),
    ]
    state = _state(candidates=[state_candidate], evidences=evidences)
    persisted = _persisted_rows(evidences)
    persisted["run-1:E4"]["result_summary"] = {
        "selected_candidate": persisted_candidate.model_dump(mode="json")
    }

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=persisted)

    assert result.passed is False
    assert "candidate_traceability" in _checks(result)


def test_reflection_without_persisted_evidence_map_fails_in_v2_mode() -> None:
    state = _state()

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=None)

    assert result.passed is False
    assert "persisted_evidence" in _checks(result)


def test_reflection_persisted_evidence_result_summary_mismatch_fails() -> None:
    state = _state()
    persisted = _persisted_rows(state["evidences"])
    persisted["run-1:E4"]["result_summary"] = {"metric_id": "gmv", "value": 0.10}

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=persisted)

    assert result.passed is False
    assert "persisted_evidence" in _checks(result)


def test_reflection_numeric_claim_must_match_persisted_result_summary_not_state_summary() -> None:
    evidences = [
        _evidence("run-1:E1"),
        _evidence("run-1:E2"),
        _evidence("run-1:E3"),
        _evidence("run-1:E4", summary={"metric_id": "gmv", "target_date": "2026-06-05", "value": 7.77}),
    ]
    state = _state(
        evidences=evidences,
        report={"status": "succeeded", "numeric_claims": [{"name": "unsafe_state_only", "value": 7.77}]},
    )
    persisted = _persisted_rows(evidences)
    persisted["run-1:E4"]["result_summary"] = {"metric_id": "gmv", "target_date": "2026-06-05", "value": 0.90}

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=persisted)

    assert result.passed is False
    assert "numeric_traceability" in _checks(result)


def test_reflection_wrong_signal_type_for_candidate_fails() -> None:
    state = _state(
        evidences=[
            _evidence("run-1:E1"),
            _evidence("run-1:E2"),
            _evidence(
                "run-1:E3",
                summary={
                    "signal_type": "inventory",
                    "signal_metric_id": "stockout_rate",
                    "dimension": "channel",
                    "element": "paid_ads",
                    "value": 0.90,
                },
            ),
            _evidence("run-1:E4"),
        ]
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "signal_consistency" in _checks(result)


def test_reflection_repair_count_increment_without_new_evidence_does_not_pass() -> None:
    state = _state(
        repair_count=1,
        candidates=[_candidate(evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"])],
        evidences=[_evidence("run-1:E1"), _evidence("run-1:E2"), _evidence("run-1:E3")],
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "required_evidence_present" in _checks(result)


def test_reflection_passes_only_with_current_run_persisted_e1_to_e4() -> None:
    state = _state()

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result == ReflectionResult(passed=True, issues=[], repaired=False, repair_count=0)


def _checks(result) -> set[str]:
    return {issue.check for issue in result.issues}


def _state(**overrides: Any) -> dict[str, Any]:
    candidate = _candidate()
    evidences = [
        _evidence("run-1:E1"),
        _evidence("run-1:E2"),
        _evidence(
            "run-1:E3",
            summary={
                "signal_type": "campaign",
                "signal_metric_id": "gmv",
                "dimension": "channel",
                "element": "paid_ads",
                "value": 0.90,
            },
        ),
        _evidence("run-1:E4", summary={"selected_candidate": candidate.model_dump(mode="json"), "value": 0.90}),
    ]
    state = {
        "run_id": "run-1",
        "status": "running",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "parsed_spec": {"filters": {}},
        "candidates": [candidate],
        "evidences": evidences,
        "repair_count": 0,
        "report": None,
    }
    state.update(overrides)
    return state


def _candidate(
    *,
    contribution_pct: float = 0.90,
    verdict: str = "confirmed",
    evidence_ids: list[str] | None = None,
) -> RootCauseCandidate:
    return RootCauseCandidate(
        root_cause_type="campaign_traffic_drop",
        dimension="channel",
        element="paid_ads",
        contribution_pct=contribution_pct,
        signal_severity=0.90,
        evidence_support=1.0,
        eng_confidence=0.90,
        verdict=verdict,
        evidence_ids=evidence_ids
        if evidence_ids is not None
        else ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"],
    )


def _evidence(
    evidence_id: str,
    *,
    metric_id: str = "gmv",
    target_date: date = date(2026, 6, 5),
    guard_status: str = "passed",
    summary: dict[str, Any] | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        query_spec=build_query_spec(metric_id=metric_id, start_date=target_date, end_date=target_date),
        sql="SELECT 1",
        sql_hash="0" * 64,
        guard_status=guard_status,
        result_summary=summary or {"metric_id": metric_id, "target_date": str(target_date), "value": 0.90},
        data_source="fact_order",
        created_at=datetime(2026, 6, 5),
    )


def _persisted_rows(evidences: list[Evidence]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for evidence in evidences:
        rows[evidence.evidence_id] = {
            "evidence_id": evidence.evidence_id,
            "run_id": evidence.evidence_id.split(":", maxsplit=1)[0],
            "query_spec": evidence.query_spec.model_dump(mode="json"),
            "sql_text": evidence.sql,
            "sql_hash": evidence.sql_hash,
            "guard_status": evidence.guard_status,
            "result_summary": evidence.result_summary,
            "data_source": evidence.data_source,
            "created_at": evidence.created_at,
        }
    return rows

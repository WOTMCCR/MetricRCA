from __future__ import annotations

from datetime import date, datetime
from typing import Any

from metric_rca.agent.graph import route_after_reflection
from metric_rca.agent.nodes.create_tasks import create_tasks
from metric_rca.agent.nodes.generate_report import generate_report
from metric_rca.agent.nodes.reflection_verify import reflection_verify
from metric_rca.agent.reflection import verify_reflection
from metric_rca.config.settings import Settings
from metric_rca.domain.models import Evidence, ReflectionResult, RootCauseCandidate
from metric_rca.guardrails.query_spec import build_query_spec


def test_reflection_missing_evidence_fails_no_report() -> None:
    state = _state(candidates=[_candidate(evidence_ids=[])], evidences=[])

    verified = reflection_verify(state, dependencies=_Dependencies())
    reported = generate_report({**state, **verified}, dependencies=_Dependencies())

    assert verified["reflection"].passed is False
    assert verified["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert route_after_reflection({**state, **verified}, dependencies=_Dependencies()) == "error_return"
    assert "report" not in reported
    assert reported["error_code"] == "REFLECTION_REPAIR_FAILED"


def test_reflection_candidate_without_current_run_evidence_fails() -> None:
    state = _state(
        candidates=[_candidate(evidence_ids=["foreign-run:E1"])],
        evidences=[_evidence("run-1:E1")],
    )

    result = verify_reflection(state, max_repair=1)

    assert result.passed is False
    assert "current_run_evidence" in _checks(result)


def test_reflection_guard_status_not_passed_fails() -> None:
    state = _state(
        candidates=[_candidate()],
        evidences=[_evidence("run-1:E1", guard_status="rejected")],
    )

    result = verify_reflection(state, max_repair=1)

    assert result.passed is False
    assert "sql_guard_status" in _checks(result)


def test_reflection_untraceable_numeric_claim_fails() -> None:
    state = _state(
        report={"status": "succeeded", "numeric_claims": [{"name": "drop_pct", "value": 0.12345}]}
    )

    result = verify_reflection(state, max_repair=1)

    assert result.passed is False
    assert "numeric_traceability" in _checks(result)


def test_reflection_time_range_mismatch_fails() -> None:
    state = _state(evidences=[_evidence("run-1:E1", target_date=date(2026, 6, 4))])

    result = verify_reflection(state, max_repair=1)

    assert result.passed is False
    assert "time_range_consistency" in _checks(result)


def test_reflection_metric_mismatch_fails() -> None:
    state = _state(evidences=[_evidence("run-1:E1", metric_id="pay_cvr")])

    result = verify_reflection(state, max_repair=1)

    assert result.passed is False
    assert "metric_consistency" in _checks(result)


def test_reflection_low_attribution_coverage_returns_ATTRIBUTION_COVERAGE_LOW() -> None:
    state = _state(candidates=[_candidate(contribution_pct=0.40, verdict="likely")])

    result = verify_reflection(state, max_repair=1)

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

    result = verify_reflection(state, max_repair=1)

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
        "trace_nodes": ["parse_question", "read_memory", "attribute_rank"],
    }

    result = verify_reflection(state, max_repair=1)

    assert result.passed is False
    assert "no_anomaly_evidence_scope" in _checks(result)
    assert "no_anomaly_downstream_trace" in _checks(result)


def test_no_anomaly_state_only_evidence_fails_reflection_node() -> None:
    state = {
        "run_id": "run-1",
        "status": "no_anomaly",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "candidates": [],
        "evidences": [_evidence("run-1:E1")],
    }

    verified = reflection_verify(state, dependencies=_Dependencies(persisted_evidence={}))

    assert verified["reflection"].passed is False
    assert verified["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert "persisted_evidence" in _checks(verified["reflection"])


def test_reflection_insufficient_evidence_blocks_confirmed_causal_language() -> None:
    state = _state(
        candidates=[_candidate(evidence_ids=["run-1:E1"], verdict="likely")],
        report={"status": "succeeded", "summary": "GMV 下降是 paid ads 导致"},
    )

    result = verify_reflection(state, max_repair=1)

    assert result.passed is False
    assert "causal_language" in _checks(result)


def test_reflection_repair_suggested_action_reenters_react_tool_guard_repo_and_generates_new_evidence() -> None:
    state = _state(
        candidates=[_candidate(evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"])],
        evidences=[_evidence("run-1:E1"), _evidence("run-1:E2"), _evidence("run-1:E3")],
    )

    verified = reflection_verify(state, dependencies=_Dependencies())

    assert verified["reflection"].passed is False
    assert verified.get("error_code") is None
    assert verified["repair_count"] == 1
    assert route_after_reflection({**state, **verified}, dependencies=_Dependencies()) == "react_step"
    issue = verified["reflection"].issues[0]
    assert issue.suggested_action is not None
    assert issue.suggested_action.action == "calculate_contribution"
    assert issue.suggested_action.args["evidence_ids"] == ["run-1:E1", "run-1:E2", "run-1:E3"]


def test_reflection_state_only_fabricated_evidence_fails() -> None:
    state = _state()

    verified = reflection_verify(
        state,
        dependencies=_Dependencies(persisted_evidence={}),
    )

    assert verified["reflection"].passed is False
    assert verified["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert "persisted_evidence" in _checks(verified["reflection"])


def test_reflection_persisted_evidence_sql_hash_mismatch_fails() -> None:
    state = _state()
    persisted = _persisted_rows(state["evidences"])
    persisted["run-1:E4"]["sql_hash"] = "1" * 64

    verified = reflection_verify(
        state,
        dependencies=_Dependencies(persisted_evidence=persisted),
    )

    assert verified["reflection"].passed is False
    assert verified["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert "persisted_evidence" in _checks(verified["reflection"])


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

    verified = reflection_verify(state, dependencies=_Dependencies(persisted_evidence=persisted))

    assert verified["reflection"].passed is False
    assert verified["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert "candidate_traceability" in _checks(verified["reflection"])


def test_reflection_without_persisted_evidence_map_fails_in_p3b_mode() -> None:
    state = _state()

    result = verify_reflection(
        state,
        max_repair=1,
        persisted_evidence_by_id=None,
    )

    assert result.passed is False
    assert "persisted_evidence" in _checks(result)


def test_reflection_persisted_evidence_run_id_or_guard_mismatch_fails() -> None:
    state = _state()
    for mutated_row in [
        {"run_id": "foreign-run", "guard_status": "passed", "sql_hash": "0" * 64},
        {"run_id": "run-1", "guard_status": "rejected", "sql_hash": "0" * 64},
    ]:
        persisted = _persisted_rows(state["evidences"])
        persisted["run-1:E4"].update(mutated_row)

        verified = reflection_verify(
            state,
            dependencies=_Dependencies(persisted_evidence=persisted),
        )

        assert verified["reflection"].passed is False
        assert verified["error_code"] == "REFLECTION_REPAIR_FAILED"
        assert "persisted_evidence" in _checks(verified["reflection"])


def test_reflection_persisted_evidence_result_summary_mismatch_fails() -> None:
    state = _state()
    persisted = _persisted_rows(state["evidences"])
    persisted["run-1:E4"]["result_summary"] = {"metric_id": "gmv", "target_date": "2026-06-05", "value": 0.10}

    verified = reflection_verify(state, dependencies=_Dependencies(persisted_evidence=persisted))

    assert verified["reflection"].passed is False
    assert verified["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert "persisted_evidence" in _checks(verified["reflection"])


def test_reflection_persisted_evidence_query_spec_mismatch_fails() -> None:
    state = _state()
    persisted = _persisted_rows(state["evidences"])
    persisted["run-1:E4"]["query_spec"]["metric_id"] = "pay_cvr"

    verified = reflection_verify(state, dependencies=_Dependencies(persisted_evidence=persisted))

    assert verified["reflection"].passed is False
    assert verified["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert "persisted_evidence" in _checks(verified["reflection"])


def test_reflection_numeric_claim_must_match_persisted_result_summary_not_state_summary() -> None:
    evidences = [
        _evidence("run-1:E1"),
        _evidence("run-1:E2"),
        _evidence(
            "run-1:E3",
            summary={"metric_id": "gmv", "target_date": "2026-06-05", "value": 0.90},
        ),
        _evidence(
            "run-1:E4",
            summary={"metric_id": "gmv", "target_date": "2026-06-05", "value": 7.77},
        ),
    ]
    state = _state(
        evidences=evidences,
        report={"status": "succeeded", "numeric_claims": [{"name": "unsafe_state_only", "value": 7.77}]},
    )
    persisted = _persisted_rows(evidences)
    persisted["run-1:E4"]["result_summary"] = {"metric_id": "gmv", "target_date": "2026-06-05", "value": 0.90}

    verified = reflection_verify(state, dependencies=_Dependencies(persisted_evidence=persisted))

    assert verified["reflection"].passed is False
    assert "numeric_traceability" in _checks(verified["reflection"])


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

    result = verify_reflection(state, max_repair=1)

    assert result.passed is False
    assert "signal_consistency" in _checks(result)


def test_reflection_e3_dimension_or_element_mismatch_fails() -> None:
    for summary in [
        {"signal_type": "campaign", "dimension": "category", "element": "paid_ads"},
        {"signal_type": "campaign", "dimension": "channel", "element": "organic"},
    ]:
        state = _state(
            evidences=[
                _evidence("run-1:E1"),
                _evidence("run-1:E2"),
                _evidence("run-1:E3", summary={**summary, "signal_metric_id": "gmv", "value": 0.90}),
                _evidence("run-1:E4"),
            ]
        )

        result = verify_reflection(state, max_repair=1)

        assert result.passed is False
        assert "signal_consistency" in _checks(result)


def test_reflection_repair_count_increment_without_new_evidence_does_not_pass() -> None:
    state = _state(
        repair_count=1,
        candidates=[_candidate(evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"])],
        evidences=[_evidence("run-1:E1"), _evidence("run-1:E2"), _evidence("run-1:E3")],
    )

    result = verify_reflection(state, max_repair=1)

    assert result.passed is False
    assert "required_evidence_present" in _checks(result)


def test_reflection_repair_failed_routes_error_return_no_report() -> None:
    state = _state(repair_count=1, candidates=[_candidate(evidence_ids=[])], evidences=[])

    verified = reflection_verify(state, dependencies=_Dependencies())
    reported = generate_report({**state, **verified}, dependencies=_Dependencies())
    tasked = create_tasks({**state, **verified}, dependencies=_Dependencies())

    assert verified["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert route_after_reflection({**state, **verified}, dependencies=_Dependencies()) == "error_return"
    assert "report" not in reported
    assert reported["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert tasked["error_code"] == "REFLECTION_REPAIR_FAILED"


def test_generate_report_has_no_unverified_numeric_claims() -> None:
    candidate = _candidate()
    e4_summary = {"selected_candidate": candidate.model_dump(mode="json")}
    evidences = [
        _evidence("run-1:E1"),
        _evidence("run-1:E2"),
        _evidence("run-1:E3"),
        _evidence("run-1:E4", summary=e4_summary),
    ]
    state = _state(
        candidates=[candidate],
        evidences=evidences,
        report={"status": "succeeded", "numeric_claims": [{"name": "ignored_prior_report", "value": 999.0}]},
        reflection=ReflectionResult(passed=True, issues=[], repaired=False, repair_count=0),
    )

    result = generate_report(state, dependencies=_Dependencies(persisted_evidence=_persisted_rows(evidences)))

    assert result["status"] == "succeeded"
    report = result["report"]

    assert report["top_candidate"] == {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": "paid_ads",
        "verdict": "confirmed",
    }
    assert "contribution_pct" not in report["top_candidate"]
    assert "signal_severity" not in report["top_candidate"]
    assert "evidence_support" not in report["top_candidate"]
    assert "eng_confidence" not in report["top_candidate"]

    assert report["numeric_claims"] == [
        {"name": "contribution_pct", "value": 0.90, "evidence_id": "run-1:E4"}
    ]


def test_generate_report_rejects_unverified_top_candidate_numeric_fields() -> None:
    state_candidate = _candidate(contribution_pct=0.90)
    persisted_candidate = _candidate(contribution_pct=0.10)

    evidences = [
        _evidence("run-1:E1"),
        _evidence("run-1:E2"),
        _evidence("run-1:E3"),
        _evidence("run-1:E4", summary={"selected_candidate": state_candidate.model_dump(mode="json")}),
    ]
    persisted = _persisted_rows(evidences)
    persisted["run-1:E4"]["result_summary"] = {
        "selected_candidate": persisted_candidate.model_dump(mode="json")
    }

    state = _state(
        candidates=[state_candidate],
        evidences=evidences,
        reflection=ReflectionResult(passed=True, issues=[], repaired=False, repair_count=0),
    )

    result = generate_report(state, dependencies=_Dependencies(persisted_evidence=persisted))

    assert result["status"] == "failed"
    assert result["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert "report" not in result


def test_generate_report_after_failed_or_missing_reflection_fails() -> None:
    for reflection in [None, ReflectionResult(passed=False, issues=[], repaired=False, repair_count=0)]:
        state = _state(reflection=reflection)

        result = generate_report(state, dependencies=_Dependencies())

        assert result["status"] == "failed"
        assert result["error_code"] == "REFLECTION_REPAIR_FAILED"
        assert "report" not in result


def test_final_report_numbers_are_traceable_to_persisted_evidence() -> None:
    candidate = _candidate(contribution_pct=0.90)
    persisted_candidate = _candidate(contribution_pct=0.10)

    evidences = [
        _evidence("run-1:E1"),
        _evidence("run-1:E2"),
        _evidence("run-1:E3"),
        _evidence("run-1:E4", summary={"selected_candidate": candidate.model_dump(mode="json")}),
    ]
    persisted = _persisted_rows(evidences)
    persisted["run-1:E4"]["result_summary"] = {
        "selected_candidate": persisted_candidate.model_dump(mode="json")
    }
    state = _state(
        candidates=[candidate],
        evidences=evidences,
        reflection=ReflectionResult(passed=True, issues=[], repaired=False, repair_count=0),
    )

    result = generate_report(state, dependencies=_Dependencies(persisted_evidence=persisted))

    assert result["status"] == "failed"
    assert result["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert "report" not in result


def _state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "run_id": "run-1",
        "status": "running",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "parsed_spec": {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "dimension": None,
            "element": None,
            "filters": {},
        },
        "candidates": [_candidate()],
        "evidences": [_evidence("run-1:E1"), _evidence("run-1:E2"), _evidence("run-1:E3"), _evidence("run-1:E4")],
        "repair_count": 0,
    }
    state.update(overrides)
    return state


def _candidate(
    *,
    evidence_ids: list[str] | None = None,
    contribution_pct: float = 0.90,
    verdict: str = "confirmed",
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
        evidence_ids=evidence_ids if evidence_ids is not None else ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"],
    )


def _evidence(
    evidence_id: str,
    *,
    metric_id: str = "gmv",
    target_date: date = date(2026, 6, 5),
    guard_status: str = "passed",
    summary: dict[str, Any] | None = None,
) -> Evidence:
    if summary is None and evidence_id.endswith(":E3"):
        summary = {
            "metric_id": metric_id,
            "target_date": str(target_date),
            "signal_type": "campaign",
            "signal_metric_id": "gmv",
            "dimension": "channel",
            "element": "paid_ads",
            "value": 0.90,
        }
    return Evidence(
        evidence_id=evidence_id,
        query_spec=build_query_spec(
            metric_id=metric_id,
            start_date=target_date,
            end_date=target_date,
            purpose="current",
        ),
        sql="SELECT 1",
        sql_hash="0" * 64,
        guard_status=guard_status,
        result_summary=summary or {"metric_id": metric_id, "target_date": str(target_date), "value": 0.90},
        data_source="fact_order",
        created_at=datetime(2026, 6, 5),
    )


def _checks(result: ReflectionResult) -> set[str]:
    return {issue.check for issue in result.issues}


def _persisted_rows(evidences: list[Evidence]) -> dict[str, dict[str, Any]]:
    return {
        evidence.evidence_id: {
            "evidence_id": evidence.evidence_id,
            "run_id": evidence.evidence_id.split(":", maxsplit=1)[0],
            "guard_status": evidence.guard_status,
            "sql_hash": evidence.sql_hash,
            "query_spec": evidence.query_spec.model_dump(mode="json"),
            "result_summary": dict(evidence.result_summary),
        }
        for evidence in evidences
    }


class _Dependencies:
    def __init__(self, *, persisted_evidence: dict[str, dict[str, Any]] | None = None) -> None:
        self.settings = Settings.model_construct(max_repair=1)
        default_state = _state()
        default_persisted = _persisted_rows(default_state["evidences"])
        self.repository = _Repository(default_persisted if persisted_evidence is None else persisted_evidence)
        self.trace_writer = _TraceWriter()


class _Repository:
    def __init__(self, persisted_evidence: dict[str, dict[str, Any]]) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.persisted_evidence = persisted_evidence

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, Any] | None:
        row = self.persisted_evidence.get(evidence_id)
        if row and row.get("run_id") == run_id:
            return row
        return None

    def create_operation_task(self, row: dict[str, Any]) -> None:
        self.tasks.append(row)


class _TraceWriter:
    def write_step(self, **kwargs: Any) -> None:
        return None

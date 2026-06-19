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
    issue = next(issue for issue in result.issues if issue.check == "ATTRIBUTION_COVERAGE_LOW")
    assert issue.suggested_action is not None
    assert issue.suggested_action.action == "rank_root_causes"
    assert issue.suggested_action.args["metric_id"] == "gmv"


def test_reflection_does_not_loop_low_coverage_after_adtributor_rank_evidence() -> None:
    candidate = _candidate(
        contribution_pct=0.3283161395107113,
        evidence_ids=[
            "run-1:E1",
            "run-1:E2_channel",
            "run-1:E_select_channel",
            "run-1:E3_ch",
            "run-1:E4_channel",
            "run-1:E4",
            "run-1:E_rank",
        ],
        dimension_elements=[("channel", "paid_ads")],
    )
    contribution_summary = _contribution_summary(candidate)
    contribution_summary["ranker"] = "adtributor_internal"
    contribution_summary["adtributor_status"] = "applied"
    evidences = [
        _evidence("run-1:E1"),
        _evidence("run-1:E2_channel"),
        _evidence(
            "run-1:E_select_channel",
            summary={
                "signal_type": "campaign",
                "signal_metric_id": "gmv",
                "dimension": "channel",
                "selected_element": "paid_ads",
                "candidate_scores": [{"element": "paid_ads", "signal_score": 0.9748593844296922}],
            },
        ),
        _evidence(
            "run-1:E3_ch",
            summary={
                "signal_type": "campaign",
                "signal_metric_id": "gmv",
                "dimension": "channel",
                "element": "paid_ads",
                "value": 0.9748593844296922,
            },
        ),
        _evidence("run-1:E4_channel"),
        _evidence("run-1:E4", summary=contribution_summary),
        _evidence("run-1:E_rank", summary=contribution_summary),
    ]
    state = _state(candidates=[candidate], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    assert result.passed is True
    assert "ATTRIBUTION_COVERAGE_LOW" not in _checks(result)


def test_reflection_keeps_low_coverage_when_candidate_does_not_bind_e4() -> None:
    candidate = _candidate(
        contribution_pct=0.3283161395107113,
        verdict="possible",
        evidence_ids=["run-1:E_rank"],
    )
    contribution_summary = _contribution_summary(candidate)
    contribution_summary["ranker"] = "adtributor_internal"
    contribution_summary["adtributor_status"] = "applied"
    evidences = [
        _evidence("run-1:E4", summary=contribution_summary),
        _evidence("run-1:E_rank", summary=contribution_summary),
    ]
    state = _state(candidates=[candidate], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    assert result.passed is False
    assert "ATTRIBUTION_COVERAGE_LOW" in _checks(result)


def test_reflection_keeps_low_coverage_when_e4_is_not_persisted() -> None:
    candidate = _candidate(contribution_pct=0.3283161395107113)
    contribution_summary = _contribution_summary(candidate)
    contribution_summary["ranker"] = "adtributor_internal"
    contribution_summary["adtributor_status"] = "applied"
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
        _evidence("run-1:E4", summary=contribution_summary),
        _evidence("run-1:E_rank", summary=contribution_summary),
    ]
    persisted = _persisted_rows(evidences)
    persisted.pop("run-1:E4")
    state = _state(candidates=[candidate], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=persisted)

    assert result.passed is False
    assert "ATTRIBUTION_COVERAGE_LOW" in _checks(result)
    assert "persisted_evidence" in _checks(result)


def test_reflection_keeps_low_coverage_when_only_e4_marks_adtributor_rank() -> None:
    candidate = _candidate(contribution_pct=0.3283161395107113)
    e4_summary = _contribution_summary(candidate)
    e4_summary["ranker"] = "adtributor_internal"
    e4_summary["adtributor_status"] = "applied"
    e_rank_summary = _contribution_summary(candidate)
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
        _evidence("run-1:E4", summary=e4_summary),
        _evidence("run-1:E_rank", summary=e_rank_summary),
    ]
    state = _state(candidates=[candidate], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    assert result.passed is False
    assert "ATTRIBUTION_COVERAGE_LOW" in _checks(result)


def test_reflection_keeps_low_coverage_when_candidate_does_not_bind_e_rank() -> None:
    candidate = _candidate(
        contribution_pct=0.3283161395107113,
        verdict="possible",
        evidence_ids=["run-1:E4"],
    )
    contribution_summary = _contribution_summary(candidate)
    contribution_summary["ranker"] = "adtributor_internal"
    contribution_summary["adtributor_status"] = "applied"
    evidences = [
        _evidence("run-1:E4", summary=contribution_summary),
        _evidence("run-1:E_rank", summary=contribution_summary),
    ]
    state = _state(candidates=[candidate], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    assert result.passed is False
    assert "ATTRIBUTION_COVERAGE_LOW" in _checks(result)


def test_reflection_keeps_low_coverage_when_e_rank_persisted_summary_mismatches_state() -> None:
    candidate = _candidate(contribution_pct=0.3283161395107113)
    contribution_summary = _contribution_summary(candidate)
    contribution_summary["ranker"] = "adtributor_internal"
    contribution_summary["adtributor_status"] = "applied"
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
        _evidence("run-1:E4", summary=contribution_summary),
        _evidence("run-1:E_rank", summary=contribution_summary),
    ]
    persisted = _persisted_rows(evidences)
    persisted["run-1:E_rank"]["result_summary"] = _contribution_summary(_candidate(contribution_pct=0.95))
    state = _state(candidates=[candidate], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=persisted)

    assert result.passed is False
    assert "ATTRIBUTION_COVERAGE_LOW" in _checks(result)
    assert "persisted_evidence" in _checks(result)


def test_reflection_accepts_low_coverage_rate_candidate_with_matching_signal() -> None:
    candidate = _candidate(
        root_cause_type="complaint_or_quality_issue",
        dimension="product",
        element="5",
        contribution_pct=0.35980648069208854,
        verdict="likely",
        evidence_ids=["run-1:E1", "run-1:E2_product", "run-1:E3_prod_5", "run-1:E4", "run-1:E_rank"],
    )
    evidences = [
        _evidence("run-1:E1", metric_id="refund_rate"),
        _evidence("run-1:E2_product", metric_id="refund_rate"),
        _evidence(
            "run-1:E3_prod_5",
            metric_id="complaint_rate",
            summary={
                "signal_type": "refund_quality",
                "signal_metric_id": "complaint_rate",
                "dimension": "product",
                "element": "5",
                "value": 0.90,
            },
        ),
        _evidence("run-1:E4", metric_id="refund_rate", summary={"selected_candidate": candidate.model_dump(mode="json")}),
        _evidence("run-1:E_rank", metric_id="refund_rate", summary={"selected_candidate": candidate.model_dump(mode="json")}),
    ]
    state = _state(
        metric_id="refund_rate",
        candidates=[candidate],
        evidences=evidences,
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    assert result.passed is True


def test_reflection_accepts_majority_rate_candidate_with_matching_signal() -> None:
    candidate = _candidate(
        root_cause_type="conversion_drop",
        dimension="device",
        element="mobile",
        contribution_pct=0.5120661916800736,
        verdict="likely",
        evidence_ids=["run-1:E1", "run-1:E2_device", "run-1:E3_dev_mobile", "run-1:E4", "run-1:E_rank"],
    )
    evidences = [
        _evidence("run-1:E1", metric_id="pay_cvr"),
        _evidence("run-1:E2_device", metric_id="pay_cvr"),
        _evidence(
            "run-1:E3_dev_mobile",
            metric_id="pay_cvr",
            summary={
                "signal_type": "conversion",
                "signal_metric_id": "pay_cvr",
                "dimension": "device",
                "element": "mobile",
                "value": 0.0092,
            },
        ),
        _evidence("run-1:E4", metric_id="pay_cvr", summary={"selected_candidate": candidate.model_dump(mode="json")}),
        _evidence("run-1:E_rank", metric_id="pay_cvr", summary={"selected_candidate": candidate.model_dump(mode="json")}),
    ]
    state = _state(
        metric_id="pay_cvr",
        candidates=[candidate],
        evidences=evidences,
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    assert result.passed is True


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


def test_reflection_repair_action_inherits_filters_from_persisted_e1() -> None:
    evidences = [
        _evidence("run-1:E1", summary={"metric_id": "gmv", "filters": {"category": "fashion"}, "value": 0.90}),
        _evidence("run-1:E2_category"),
        _evidence(
            "run-1:E3_cat_fashion",
            summary={
                "signal_type": "inventory",
                "signal_metric_id": "stockout_rate",
                "dimension": "category",
                "element": "fashion",
                "value": 0.90,
            },
        ),
    ]
    state = _state(
        parsed_spec={},
        candidates=[
            _candidate(
                dimension="category",
                element="fashion",
                evidence_ids=["run-1:E1", "run-1:E2_category", "run-1:E3_cat_fashion"],
            )
        ],
        evidences=evidences,
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    issue = next(issue for issue in result.issues if issue.suggested_action is not None)
    assert issue.suggested_action.action == "calculate_contribution"
    assert issue.suggested_action.args["filters"] == {"category": "fashion"}


def test_reflection_suggests_signal_repair_when_no_candidates_but_drilldowns_exist() -> None:
    evidences = [
        _evidence("run-1:E1"),
        _evidence(
            "run-1:E2_channel",
            summary={
                "metric_id": "gmv",
                "dimension": "channel",
                "candidates": [{"dimension": "channel", "element": "paid_ads"}],
            },
        ),
        _evidence(
            "run-1:E2_category",
            summary={
                "metric_id": "gmv",
                "dimension": "category",
                "candidates": [{"dimension": "category", "element": "electronics"}],
            },
        ),
        _evidence(
            "run-1:E2_product",
            summary={
                "metric_id": "gmv",
                "dimension": "product",
                "candidates": [{"dimension": "product", "element": "2"}],
            },
        ),
    ]
    state = _state(candidates=[], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    assert result.passed is False
    issue = next(issue for issue in result.issues if issue.check == "evidence_coverage")
    assert issue.suggested_action is not None
    assert issue.suggested_action.action == "fetch_related_signal"
    assert issue.suggested_action.args == {
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "signal_type": "campaign",
        "dimension": "channel",
        "element": "paid_ads",
        "evidence_ids": ["run-1:E1", "run-1:E2_channel"],
    }


def test_reflection_suggests_detect_repair_when_no_evidence_exists() -> None:
    state = _state(candidates=[], evidences=[], parsed_spec={"filters": {"channel": "paid_ads"}})

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id={})

    issue = next(issue for issue in result.issues if issue.check == "evidence_coverage")
    assert issue.suggested_action is not None
    assert issue.suggested_action.action == "detect_anomaly"
    assert issue.suggested_action.args == {
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "filters": {"channel": "paid_ads"},
    }


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
        _evidence("run-1:E4", summary=_contribution_summary(state_candidate)),
    ]
    state = _state(candidates=[state_candidate], evidences=evidences)
    persisted = _persisted_rows(evidences)
    persisted["run-1:E4"]["result_summary"] = _contribution_summary(persisted_candidate)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=persisted)

    assert result.passed is False
    assert "candidate_traceability" in _checks(result)


def test_reflection_rejects_legacy_selected_candidate_only_e4_summary() -> None:
    candidate = _candidate()
    evidences = [
        _evidence("run-1:E1"),
        _evidence("run-1:E2"),
        _evidence("run-1:E3"),
        _legacy_evidence("run-1:E4", summary={"selected_candidate": candidate.model_dump(mode="json")}),
        _evidence("run-1:E_rank", summary=_contribution_summary(candidate)),
    ]
    state = _state(candidates=[candidate], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

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


def test_reflection_accepts_related_signal_metric_for_aliased_e3_evidence() -> None:
    candidate = _candidate(evidence_ids=["run-1:E1", "run-1:E2_channel", "run-1:E3_channel_paid_ads", "run-1:E4", "run-1:E_rank"])
    state = _state(
        candidates=[candidate],
        evidences=[
            _evidence("run-1:E1"),
            _evidence("run-1:E2_channel"),
            _evidence(
                "run-1:E3_channel_paid_ads",
                metric_id="uv",
                summary={
                    "signal_type": "campaign",
                    "signal_metric_id": "uv",
                    "dimension": "channel",
                    "element": "paid_ads",
                    "value": 0.90,
                },
            ),
            _evidence("run-1:E4", summary={"selected_candidate": candidate.model_dump(mode="json"), "value": 0.90}),
            _evidence("run-1:E_rank", summary={"selected_candidate": candidate.model_dump(mode="json"), "value": 0.90}),
        ],
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is True


def test_reflection_accepts_related_signal_metric_for_dynamic_selection_evidence() -> None:
    candidate = _candidate(
        dimension="channel",
        element="organic",
        evidence_ids=[
            "run-1:E1",
            "run-1:E2_channel",
            "run-1:E_select_channel",
            "run-1:E3_ch_organic",
            "run-1:E4",
            "run-1:E_rank",
        ],
    )
    evidences = [
        _evidence("run-1:E1", metric_id="uv"),
        _evidence("run-1:E2_channel", metric_id="uv"),
        _evidence(
            "run-1:E_select_channel",
            metric_id="gmv",
            summary={
                "signal_type": "campaign",
                "signal_metric_id": "gmv",
                "dimension": "channel",
                "selected_element": "organic",
                "candidate_scores": [{"element": "organic", "signal_score": 0.88}],
            },
        ),
        _evidence(
            "run-1:E3_ch_organic",
            metric_id="gmv",
            summary={
                "signal_type": "campaign",
                "signal_metric_id": "gmv",
                "dimension": "channel",
                "element": "organic",
                "value": 0.90,
            },
        ),
        _evidence("run-1:E4", metric_id="uv", summary={"selected_candidate": candidate.model_dump(mode="json")}),
        _evidence("run-1:E_rank", metric_id="uv", summary={"selected_candidate": candidate.model_dump(mode="json")}),
    ]
    state = _state(metric_id="uv", candidates=[candidate], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    assert result.passed is True


def test_reflection_checks_signal_consistency_for_aliased_e3_evidence() -> None:
    state = _state(
        candidates=[
            _candidate(evidence_ids=["run-1:E1", "run-1:E2_channel", "run-1:E3_channel_paid_ads", "run-1:E4"])
        ],
        evidences=[
            _evidence("run-1:E1"),
            _evidence("run-1:E2_channel"),
            _evidence(
                "run-1:E3_channel_paid_ads",
                metric_id="stockout_rate",
                summary={
                    "signal_type": "inventory",
                    "signal_metric_id": "stockout_rate",
                    "dimension": "channel",
                    "element": "paid_ads",
                    "value": 0.90,
                },
            ),
            _evidence("run-1:E4"),
        ],
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "signal_consistency" in _checks(result)


def test_reflection_accepts_aov_drop_when_e4_decomposition_proves_aov_factor() -> None:
    candidate = _candidate(
        root_cause_type="aov_drop",
        dimension="category",
        element="fashion",
        evidence_ids=["run-1:E1", "run-1:E2_category", "run-1:E3_cat_fashion", "run-1:E4", "run-1:E_rank"],
    )
    state = _state(
        candidates=[candidate],
        evidences=[
            _evidence("run-1:E1"),
            _evidence("run-1:E2_category"),
            _evidence(
                "run-1:E3_cat_fashion",
                metric_id="stockout_rate",
                summary={
                    "signal_type": "inventory",
                    "signal_metric_id": "stockout_rate",
                    "dimension": "category",
                    "element": "fashion",
                    "value": 0.90,
                },
            ),
            _evidence(
                "run-1:E4",
                summary={
                    "selected_candidate": candidate.model_dump(mode="json"),
                    "decomposition": {"largest_drop_factor": "aov"},
                    "value": 0.90,
                },
            ),
            _evidence("run-1:E_rank", summary={"selected_candidate": candidate.model_dump(mode="json"), "value": 0.90}),
        ],
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is True


def test_reflection_rejects_aov_drop_with_unrelated_e3_even_when_e4_decomposition_is_aov() -> None:
    candidate = _candidate(
        root_cause_type="aov_drop",
        dimension="category",
        element="fashion",
        evidence_ids=["run-1:E1", "run-1:E2_category", "run-1:E3_cat_fashion", "run-1:E4"],
    )
    state = _state(
        candidates=[candidate],
        evidences=[
            _evidence("run-1:E1"),
            _evidence("run-1:E2_category"),
            _evidence(
                "run-1:E3_cat_fashion",
                metric_id="stockout_rate",
                summary={
                    "signal_type": "inventory",
                    "signal_metric_id": "stockout_rate",
                    "dimension": "category",
                    "element": "electronics",
                    "value": 0.90,
                },
            ),
            _evidence(
                "run-1:E4",
                summary={
                    "selected_candidate": candidate.model_dump(mode="json"),
                    "decomposition": {"largest_drop_factor": "aov"},
                    "value": 0.90,
                },
            ),
        ],
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is False
    assert "signal_consistency" in _checks(result)


def test_reflection_accepts_net_gmv_refund_quality_signal_for_quality_candidate() -> None:
    candidate = _candidate(
        root_cause_type="complaint_or_quality_issue",
        dimension="product",
        element="1",
        evidence_ids=["run-1:E1", "run-1:E2_product", "run-1:E3_prod_1", "run-1:E4", "run-1:E_rank"],
    )
    state = _state(
        metric_id="net_gmv",
        candidates=[candidate],
        evidences=[
            _evidence("run-1:E1", metric_id="net_gmv"),
            _evidence("run-1:E2_product", metric_id="net_gmv"),
            _evidence(
                "run-1:E3_prod_1",
                metric_id="complaint_rate",
                summary={
                    "signal_type": "refund_quality",
                    "signal_metric_id": "complaint_rate",
                    "dimension": "product",
                    "element": "1",
                    "value": 0.90,
                },
            ),
            _evidence(
                "run-1:E4",
                metric_id="net_gmv",
                summary={"selected_candidate": candidate.model_dump(mode="json"), "value": 0.90},
            ),
            _evidence("run-1:E_rank", metric_id="net_gmv", summary={"selected_candidate": candidate.model_dump(mode="json"), "value": 0.90}),
        ],
    )

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(state["evidences"]))

    assert result.passed is True


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


def test_reflection_warns_but_passes_when_cross_chain_contributions_overlap() -> None:
    paid_ads = _candidate(contribution_pct=0.70)
    electronics = _candidate(
        root_cause_type="stockout",
        dimension="category",
        element="electronics",
        contribution_pct=0.60,
        evidence_ids=["run-1:E1", "run-1:E2_category", "run-1:E3_category", "run-1:E4", "run-1:E_rank"],
    )
    summary = _contribution_summary(paid_ads)
    summary["candidates"] = [
        paid_ads.model_dump(mode="json"),
        electronics.model_dump(mode="json"),
    ]
    summary["contribution_set"]["candidates"] = summary["candidates"]
    summary["contribution_set"]["factor_graph"] = {"chain_evidence_ids": ["run-1:E4_channel", "run-1:E4_category"]}
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
        _evidence("run-1:E4", summary=summary),
        _evidence("run-1:E_rank", summary=summary),
    ]
    state = _state(candidates=[paid_ads], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    assert result.passed is True
    issue = next(issue for issue in result.issues if issue.check == "cross_chain_contribution_overlap")
    assert issue.severity == "warning"


def test_reflection_accepts_cross_chain_interaction_with_paired_non_causal_signals() -> None:
    candidate = _candidate(
        root_cause_type="interaction_channel_category",
        dimension="channel",
        element="paid_ads",
        evidence_ids=[
            "run-1:E1",
            "run-1:E2_channel",
            "run-1:E_select_channel",
            "run-1:E3_ch_paid_ads",
            "run-1:E4_channel",
            "run-1:E2_category",
            "run-1:E_select_category",
            "run-1:E3_cat_electronics",
            "run-1:E4_category",
            "run-1:E4",
            "run-1:E_rank",
        ],
        dimension_elements=[("channel", "paid_ads"), ("category", "electronics")],
    )
    summary = _contribution_summary(candidate)
    summary["contribution_set"]["factor_graph"] = {
        "chain_evidence_ids": ["run-1:E4_channel", "run-1:E4_category"]
    }
    evidences = [
        _evidence("run-1:E1", summary={"metric_id": "gmv", "is_anomaly": True, "bad_direction": True}),
        _evidence("run-1:E2_channel", summary={"metric_id": "gmv", "dimension": "channel"}),
        _evidence(
            "run-1:E_select_channel",
            summary={"signal_type": "campaign", "signal_metric_id": "gmv", "dimension": "channel"},
        ),
        _evidence(
            "run-1:E3_ch_paid_ads",
            summary={
                "signal_type": "campaign",
                "signal_metric_id": "gmv",
                "dimension": "channel",
                "element": "paid_ads",
                "is_anomaly": False,
                "bad_direction": False,
            },
        ),
        _evidence("run-1:E4_channel", summary={"metric_id": "gmv", "dimension": "channel"}),
        _evidence("run-1:E2_category", summary={"metric_id": "gmv", "dimension": "category"}),
        _evidence(
            "run-1:E_select_category",
            summary={"signal_type": "inventory", "signal_metric_id": "stockout_rate", "dimension": "category"},
        ),
        _evidence(
            "run-1:E3_cat_electronics",
            metric_id="stockout_rate",
            summary={
                "signal_type": "inventory",
                "signal_metric_id": "stockout_rate",
                "dimension": "category",
                "element": "electronics",
                "is_anomaly": False,
                "bad_direction": False,
            },
        ),
        _evidence("run-1:E4_category", summary={"metric_id": "gmv", "dimension": "category"}),
        _evidence("run-1:E4", summary=summary),
        _evidence("run-1:E_rank", summary=summary),
    ]
    state = _state(candidates=[candidate], evidences=evidences)

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id=_persisted_rows(evidences))

    assert result.passed is True


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
        _evidence("run-1:E_rank", summary={"selected_candidate": candidate.model_dump(mode="json"), "value": 0.90}),
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
    root_cause_type: str = "campaign_traffic_drop",
    dimension: str = "channel",
    element: str = "paid_ads",
    contribution_pct: float = 0.90,
    verdict: str = "confirmed",
    evidence_ids: list[str] | None = None,
    dimension_elements: list[tuple[str, str]] | None = None,
) -> RootCauseCandidate:
    return RootCauseCandidate(
        root_cause_type=root_cause_type,
        dimension=dimension,
        element=element,
        contribution_pct=contribution_pct,
        signal_severity=0.90,
        evidence_support=1.0,
        eng_confidence=0.90,
        verdict=verdict,
        evidence_ids=evidence_ids
        if evidence_ids is not None
        else ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4", "run-1:E_rank"],
        dimension_elements=dimension_elements or [],
    )


def _evidence(
    evidence_id: str,
    *,
    metric_id: str = "gmv",
    target_date: date = date(2026, 6, 5),
    guard_status: str = "passed",
    summary: dict[str, Any] | None = None,
) -> Evidence:
    if summary is not None:
        summary = _canonical_summary_for_evidence(evidence_id, summary)
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


def _legacy_evidence(
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


def _canonical_summary_for_evidence(evidence_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    if not (evidence_id.endswith(":E4") or evidence_id.endswith(":E_rank")):
        return summary
    if "contribution_set" in summary:
        return summary
    selected = summary.get("selected_candidate")
    if not isinstance(selected, dict):
        return summary
    return {**summary, **_contribution_summary(RootCauseCandidate.model_validate(selected))}


def _contribution_summary(candidate: RootCauseCandidate) -> dict[str, Any]:
    candidate_payload = candidate.model_dump(mode="json")
    return {
        "selected_candidate": candidate_payload,
        "candidates": [candidate_payload],
        "contribution_set": {
            "selected_candidate": candidate_payload,
            "candidates": [candidate_payload],
            "evidence_ids": candidate.evidence_ids,
            "factor_graph": {},
            "selection_evidence_id": None,
        },
    }


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

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from metric_rca.evals.grpo_dataset import build_grpo_trajectory, write_grpo_dataset
from metric_rca.evals.models import EvalCase, EvalRuntimeError, GroundTruth, PersistedArtifacts, RootCauseTruth
from metric_rca.evals.scorer import score_case


def test_grpo_trajectory_rewards_correct_traceable_final_answer() -> None:
    case = EvalCase(case_id="case-1", question="Why did GMV fall?", tags=("campaign",))
    ground_truth = GroundTruth(
        case_id="case-1",
        business_date=date(2026, 6, 5),
        metric_id="gmv",
        expected_anomaly=True,
        root_cause_type="campaign_traffic_drop",
        dimension="channel",
        element="paid_ads",
        root_causes=(
            RootCauseTruth(
                root_cause_type="campaign_traffic_drop",
                dimension="channel",
                element="paid_ads",
                weight=1.0,
            ),
        ),
    )
    artifacts = _artifacts("run-1")
    score = _score_from_artifacts(case_id="case-1", ground_truth=ground_truth, artifacts=artifacts)

    record = build_grpo_trajectory(
        eval_id="eval-1",
        eval_suite="regression",
        phase="baseline",
        case=case,
        ground_truth=ground_truth,
        score=score,
        artifacts=artifacts,
        predictions=[{"case_id": "case-1", "aspect": "outcome", "prediction": {"top1_ok": True}}],
    )

    assert record["judge"]["reward"] == 1.0
    assert record["final_answer"]["selected_candidate"]["element"] == "paid_ads"
    assert record["ground_truth"]["root_causes"][0]["element"] == "paid_ads"
    assert record["predictions"][0]["aspect"] == "outcome"
    assert record["trajectory"]["trace_steps"][0]["action"] == "detect_anomaly"


def test_grpo_trajectory_rewards_zero_when_answer_lacks_required_evidence() -> None:
    case = EvalCase(case_id="case-1", question="Why did GMV fall?", tags=("campaign",))
    ground_truth = GroundTruth(
        case_id="case-1",
        business_date=date(2026, 6, 5),
        metric_id="gmv",
        expected_anomaly=True,
        root_cause_type="campaign_traffic_drop",
        dimension="channel",
        element="paid_ads",
        root_causes=(
            RootCauseTruth(
                root_cause_type="campaign_traffic_drop",
                dimension="channel",
                element="paid_ads",
                weight=1.0,
            ),
        ),
    )
    artifacts = _artifacts("run-1", missing_evidence_alias="E3_ch_paid_ads")
    score = _score_from_artifacts(case_id="case-1", ground_truth=ground_truth, artifacts=artifacts)

    record = build_grpo_trajectory(
        eval_id="eval-1",
        eval_suite="regression",
        phase="baseline",
        case=case,
        ground_truth=ground_truth,
        score=score,
        artifacts=artifacts,
        predictions=[],
    )

    assert record["judge"]["reward"] == 0.0
    assert "evidence_coverage" in record["judge"]["failed_gates"]


def test_grpo_trajectory_rejects_score_that_disagrees_with_artifacts() -> None:
    case = EvalCase(case_id="case-1", question="Why did GMV fall?", tags=("campaign",))
    ground_truth = GroundTruth(
        case_id="case-1",
        business_date=date(2026, 6, 5),
        metric_id="gmv",
        expected_anomaly=True,
        root_cause_type="campaign_traffic_drop",
        dimension="channel",
        element="paid_ads",
        root_causes=(
            RootCauseTruth(
                root_cause_type="campaign_traffic_drop",
                dimension="channel",
                element="paid_ads",
                weight=1.0,
            ),
        ),
    )
    artifacts = _artifacts("run-1", selected=_candidate("run-1", element="organic"))
    forged_score = _score(final_run_id="run-1", selected=_candidate("run-1", element="paid_ads"))

    with pytest.raises(EvalRuntimeError) as exc_info:
        build_grpo_trajectory(
            eval_id="eval-1",
            eval_suite="regression",
            phase="baseline",
            case=case,
            ground_truth=ground_truth,
            score=forged_score,
            artifacts=artifacts,
            predictions=[],
        )

    assert exc_info.value.code == "GRPO_SCORE_ARTIFACT_MISMATCH"


def test_write_grpo_dataset_persists_baseline_and_memory_trajectories(tmp_path: Path) -> None:
    case = EvalCase(case_id="case-1", question="Why did GMV fall?", tags=("campaign",))
    ground_truth = GroundTruth(
        case_id="case-1",
        business_date=date(2026, 6, 5),
        metric_id="gmv",
        expected_anomaly=True,
        root_cause_type="campaign_traffic_drop",
        dimension="channel",
        element="paid_ads",
        root_causes=(
            RootCauseTruth(
                root_cause_type="campaign_traffic_drop",
                dimension="channel",
                element="paid_ads",
                weight=1.0,
            ),
        ),
    )
    predictions_dir = tmp_path / "eval-1"
    predictions_dir.mkdir(parents=True)
    (predictions_dir / "predictions.jsonl").write_text(
        json.dumps({"case_id": "case-1", "aspect": "intent", "prediction": {"metric_id": "gmv"}})
        + "\n"
    )
    baseline_artifacts = _artifacts("baseline-run")
    memory_artifacts = _artifacts("memory-run")
    baseline_score = _score_from_artifacts(case_id="case-1", ground_truth=ground_truth, artifacts=baseline_artifacts)
    memory_score = _score_from_artifacts(case_id="case-1", ground_truth=ground_truth, artifacts=memory_artifacts)
    artifacts_by_run = {"baseline-run": baseline_artifacts, "memory-run": memory_artifacts}

    paths = write_grpo_dataset(
        output_dir=tmp_path,
        eval_id="eval-1",
        eval_suite="regression",
        cases=[case],
        ground_truth={"case-1": ground_truth},
        case_scores=[baseline_score],
        memory_case_scores=[memory_score],
        repository=object(),
        artifact_reader=lambda _repo, run_id: artifacts_by_run[run_id],
    )

    records = [
        json.loads(line)
        for line in paths["trajectories_path"].read_text().splitlines()
        if line.strip()
    ]
    manifest = json.loads(paths["manifest_path"].read_text())
    assert [record["phase"] for record in records] == ["memory", "baseline"]
    assert [record["trajectory"]["run_id"] for record in records] == ["memory-run", "baseline-run"]
    assert records[0]["predictions"][0]["aspect"] == "intent"
    assert manifest["record_count"] == 2
    assert manifest["reward_rate"] == 1.0


def test_write_grpo_dataset_requires_predictions_jsonl(tmp_path: Path) -> None:
    case = EvalCase(case_id="case-1", question="Why did GMV fall?", tags=("campaign",))
    ground_truth = GroundTruth(
        case_id="case-1",
        business_date=date(2026, 6, 5),
        metric_id="gmv",
        expected_anomaly=True,
        root_cause_type="campaign_traffic_drop",
        dimension="channel",
        element="paid_ads",
        root_causes=(
            RootCauseTruth(
                root_cause_type="campaign_traffic_drop",
                dimension="channel",
                element="paid_ads",
                weight=1.0,
            ),
        ),
    )
    artifacts = _artifacts("baseline-run")
    score = _score_from_artifacts(case_id="case-1", ground_truth=ground_truth, artifacts=artifacts)

    with pytest.raises(EvalRuntimeError) as exc_info:
        write_grpo_dataset(
            output_dir=tmp_path,
            eval_id="eval-1",
            eval_suite="regression",
            cases=[case],
            ground_truth={"case-1": ground_truth},
            case_scores=[score],
            memory_case_scores=[],
            repository=object(),
            artifact_reader=lambda _repo, _run_id: artifacts,
        )

    assert exc_info.value.code == "GRPO_PREDICTIONS_MISSING"


def _score(
    *,
    final_run_id: str,
    selected: dict[str, object],
    evidence_coverage: float = 1.0,
) -> dict[str, object]:
    return {
        "case_id": "case-1",
        "intent_ok": 1,
        "anomaly_ok": 1,
        "top1_ok": 1,
        "top3_ok": 1,
        "dominant_top1_ok": 1,
        "root_cause_set_recall": 1.0,
        "root_cause_set_precision": 1.0,
        "weighted_explanation_coverage": 1.0,
        "top3_contains_all_major_causes": 1,
        "evidence_coverage": evidence_coverage,
        "sql_safe": 1,
        "reflection_repair_ok": 1,
        "report_traceable_ok": 1,
        "memory_pollution_ok": 1,
        "no_anomaly_task_ok": 1,
        "adtributor_used": 0,
        "multi_agent_path": "single_agent",
        "detail": {
            "status": "succeeded",
            "metric_id": "gmv",
            "selected_candidate": selected,
            "final_run_id": final_run_id,
            "sql_count": 2,
            "trace_step_count": 1,
            "memory_read_seen": False,
            "tool_sequence": ["detect_anomaly", "rank_root_causes"],
            "scenario_family": "campaign",
        },
    }


def _score_from_artifacts(
    *,
    case_id: str,
    ground_truth: GroundTruth,
    artifacts: PersistedArtifacts,
) -> dict[str, object]:
    score = score_case(case_id=case_id, ground_truth=ground_truth, artifacts=artifacts)
    score["detail"]["final_run_id"] = str(artifacts.agent_run["run_id"])
    score["detail"]["scenario_family"] = "campaign"
    return score


def _candidate(run_id: str = "run-1", *, element: str = "paid_ads") -> dict[str, object]:
    return {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": element,
        "contribution_pct": 0.72,
        "evidence_ids": [
            f"{run_id}:E1",
            f"{run_id}:E2_channel",
            f"{run_id}:E3_ch_paid_ads",
            f"{run_id}:E4",
            f"{run_id}:E_rank",
        ],
    }


def _artifacts(
    run_id: str,
    *,
    selected: dict[str, object] | None = None,
    missing_evidence_alias: str | None = None,
) -> PersistedArtifacts:
    selected_candidate = selected or _candidate(run_id)
    evidences = [
        {
            "evidence_id": f"{run_id}:E1",
            "run_id": run_id,
            "query_spec": {"metric_id": "gmv"},
            "sql_text": "SELECT 1",
            "sql_hash": "hash-e1",
            "guard_status": "passed",
            "result_summary": {"is_anomaly": True, "filters": {}},
            "data_source": "test",
        },
        {
            "evidence_id": f"{run_id}:E2_channel",
            "run_id": run_id,
            "query_spec": {"metric_id": "gmv", "group_by": ["channel"]},
            "sql_text": "SELECT 1",
            "sql_hash": "hash-e2",
            "guard_status": "passed",
            "result_summary": {"dimension": "channel"},
            "data_source": "test",
        },
        {
            "evidence_id": f"{run_id}:E3_ch_paid_ads",
            "run_id": run_id,
            "query_spec": {"metric_id": "gmv", "filters": {"channel": "paid_ads"}},
            "sql_text": "SELECT 1",
            "sql_hash": "hash-e3",
            "guard_status": "passed",
            "result_summary": {"signal_type": "campaign"},
            "data_source": "test",
        },
        {
            "evidence_id": f"{run_id}:E4",
            "run_id": run_id,
            "query_spec": {"metric_id": "gmv"},
            "sql_text": "SELECT 1",
            "sql_hash": "hash-e4",
            "guard_status": "passed",
            "result_summary": {
                "contribution_set": {
                    "selected_candidate": selected_candidate,
                    "candidates": [selected_candidate],
                }
            },
            "data_source": "test",
        },
        {
            "evidence_id": f"{run_id}:E_rank",
            "run_id": run_id,
            "query_spec": {"metric_id": "gmv"},
            "sql_text": "SELECT 1",
            "sql_hash": "hash-rank",
            "guard_status": "passed",
            "result_summary": {"selected_candidate": selected_candidate},
            "data_source": "test",
        },
    ]
    if missing_evidence_alias is not None:
        evidences = [row for row in evidences if not str(row["evidence_id"]).endswith(f":{missing_evidence_alias}")]
    return PersistedArtifacts(
        agent_run={
            "run_id": run_id,
            "question": "Why did GMV fall?",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "status": "succeeded",
            "error_code": None,
            "runtime_version": 3,
        },
        evidences=evidences,
        trace_steps=[
            {
                "step_id": f"{run_id}:trace:1",
                "run_id": run_id,
                "seq": 1,
                "node": "runtime_plan_executor",
                "action": "detect_anomaly",
                "input_summary": {"metric_id": "gmv"},
                "output_summary": {"ok": True},
                "error_code": None,
                "latency_ms": 3,
                "token_usage": None,
            }
        ],
        sql_audit=[
            {
                "run_id": run_id,
                "sql_text": "SELECT 1",
                "sql_hash": "hash",
                "guard_status": "passed",
                "guard_errors": [],
            }
        ],
        tasks=[],
        report={
            "status": "succeeded",
            "selected_candidate": selected_candidate,
            "evidence_ids": [f"{run_id}:E1"],
            "numeric_claims": [
                {
                    "name": "contribution_pct",
                    "value": 0.72,
                    "evidence_id": f"{run_id}:E4",
                }
            ],
        },
        memory_records=[],
    )

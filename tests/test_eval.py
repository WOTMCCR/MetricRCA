from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from metric_rca.evals.models import EvalRuntimeError, GroundTruth, PersistedArtifacts
from metric_rca.evals.runner import load_cases, run_eval
from metric_rca.evals.scorer import dangerous_sql_blocked, score_case, summarize_scores


def test_eval_loads_cases_and_ground_truth(tmp_path: Path) -> None:
    cases_path = _cases_file(tmp_path)
    cases = load_cases(cases_path)
    repo = _EvalRepository()

    run_eval(repository=repo, rca_runner=_fake_runner, cases_path=cases_path, output_dir=tmp_path, eval_id="eval-1")

    assert [case.case_id for case in cases] == ["gmv_paid_ads_drop", "gmv_no_anomaly"]
    assert repo.gt_requests == [["gmv_paid_ads_drop", "gmv_no_anomaly"]]


def test_eval_missing_ground_truth_returns_EVAL_GROUND_TRUTH_MISSING(tmp_path: Path) -> None:
    repo = _EvalRepository(missing_gt={"gmv_no_anomaly"})

    with pytest.raises(EvalRuntimeError) as exc:
        run_eval(
            repository=repo,
            rca_runner=_fake_runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
        )

    assert exc.value.code == "EVAL_GROUND_TRUTH_MISSING"


def test_eval_threshold_failure_returns_EVAL_THRESHOLD_NOT_MET(tmp_path: Path) -> None:
    repo = _EvalRepository(gt_element="paid_ads", persisted_element="organic")

    with pytest.raises(EvalRuntimeError) as exc:
        run_eval(
            repository=repo,
            rca_runner=_fake_runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
        )

    assert exc.value.code == "EVAL_THRESHOLD_NOT_MET"
    assert repo.eval_runs[0]["summary"]["thresholds_met"] is False


def test_eval_mutating_ground_truth_changes_score() -> None:
    artifacts = _artifacts("run-1", selected=_candidate(element="paid_ads"))
    original = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop", element="paid_ads"),
        artifacts=artifacts,
    )
    mutated = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop", element="organic"),
        artifacts=artifacts,
    )

    assert original["top1_ok"] == 1
    assert mutated["top1_ok"] == 0


def test_eval_runs_rca_for_each_case(tmp_path: Path) -> None:
    repo = _EvalRepository()

    run_eval(repository=repo, rca_runner=_fake_runner, cases_path=_cases_file(tmp_path), output_dir=tmp_path, eval_id="eval-1")

    assert repo.runner_calls == ["gmv_paid_ads_drop", "gmv_no_anomaly"]


def test_eval_scores_from_persisted_artifacts_not_graph_return_state(tmp_path: Path) -> None:
    repo = _EvalRepository()

    output = run_eval(
        repository=repo,
        rca_runner=_unsafe_runner,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
    )

    first_case = output["cases"][0]
    assert first_case["top1_ok"] == 1
    assert first_case["detail"]["selected_candidate"]["element"] == "paid_ads"


def test_eval_writes_eval_run_and_eval_case_result(tmp_path: Path) -> None:
    repo = _EvalRepository()

    output = run_eval(repository=repo, rca_runner=_fake_runner, cases_path=_cases_file(tmp_path), output_dir=tmp_path, eval_id="eval-1")

    assert repo.eval_runs[0]["summary"] == output["summary"]
    assert [row["case_id"] for row in repo.case_results] == ["gmv_paid_ads_drop", "gmv_no_anomaly"]


def test_eval_scores_intent_anomaly_top1_top3_evidence_sql_reflection() -> None:
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=_artifacts("run-1", selected=_candidate()),
    )

    assert score["intent_ok"] == 1
    assert score["anomaly_ok"] == 1
    assert score["top1_ok"] == 1
    assert score["top3_ok"] == 1
    assert score["evidence_coverage"] == 1.0
    assert score["sql_safe"] == 1
    assert score["reflection_repair_ok"] == 1


def test_eval_expected_anomaly_requires_e1_anomaly_evidence() -> None:
    artifacts = _artifacts("run-1", selected=_candidate())
    no_e1 = PersistedArtifacts(
        agent_run=artifacts.agent_run,
        evidences=artifacts.evidences[1:],
        trace_steps=artifacts.trace_steps,
        sql_audit=artifacts.sql_audit,
        tasks=artifacts.tasks,
        report=artifacts.report,
    )
    score = score_case(case_id="gmv_paid_ads_drop", ground_truth=_gt("gmv_paid_ads_drop"), artifacts=no_e1)

    assert score["anomaly_ok"] == 0


def test_eval_report_traceable_ok_requires_persisted_numeric_claims() -> None:
    artifacts = _artifacts("run-1", selected=_candidate())
    score = score_case(case_id="gmv_paid_ads_drop", ground_truth=_gt("gmv_paid_ads_drop"), artifacts=artifacts)
    broken = PersistedArtifacts(
        agent_run=artifacts.agent_run,
        evidences=[
            *artifacts.evidences[:-1],
            {**artifacts.evidences[-1], "result_summary": {"selected_candidate": _candidate(contribution_pct=0.1)}},
        ],
        trace_steps=artifacts.trace_steps,
        sql_audit=artifacts.sql_audit,
        tasks=artifacts.tasks,
        report=artifacts.report,
    )
    broken_score = score_case(case_id="gmv_paid_ads_drop", ground_truth=_gt("gmv_paid_ads_drop"), artifacts=broken)

    assert score["report_traceable_ok"] == 1
    assert broken_score["report_traceable_ok"] == 0


def test_eval_memory_pollution_ok_rejects_memory_evidence_id() -> None:
    polluted = _candidate(evidence_ids=["run-1:E1", "memory:case:1", "run-1:E3", "run-1:E4"])
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=_artifacts("run-1", selected=polluted),
    )

    assert score["memory_pollution_ok"] == 0


def test_dangerous_sql_blocked_is_real_boolean_from_guard() -> None:
    assert dangerous_sql_blocked() is True


def test_dangerous_sql_blocked_not_constant_when_guard_monkeypatched() -> None:
    class _Plan:
        guard_status = "passed"

    assert dangerous_sql_blocked(guard=lambda sql: _Plan()) is False


def test_no_anomaly_correct_requires_no_task_no_attribute_rank_no_candidate() -> None:
    clean = score_case(
        case_id="gmv_no_anomaly",
        ground_truth=_gt("gmv_no_anomaly", expected_anomaly=False, root_cause_type=None, dimension=None, element=None),
        artifacts=_no_anomaly_artifacts("run-1"),
    )
    polluted = score_case(
        case_id="gmv_no_anomaly",
        ground_truth=_gt("gmv_no_anomaly", expected_anomaly=False, root_cause_type=None, dimension=None, element=None),
        artifacts=_no_anomaly_artifacts("run-1", trace_node="attribute_rank"),
    )

    assert clean["no_anomaly_task_ok"] == 1
    assert clean["anomaly_ok"] == 1
    assert polluted["no_anomaly_task_ok"] == 0


def test_eval_json_and_markdown_outputs_exist(tmp_path: Path) -> None:
    run_eval(repository=_EvalRepository(), rca_runner=_fake_runner, cases_path=_cases_file(tmp_path), output_dir=tmp_path, eval_id="eval-1")

    assert (tmp_path / "eval-1.json").exists()
    assert (tmp_path / "eval-1.md").exists()


def test_runtime_code_outside_seed_eval_tests_does_not_read_anomaly_ground_truth() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "metric_rca").rglob("*.py"):
        relative = str(path.relative_to(root))
        if relative.startswith("metric_rca/evals/") or relative == "metric_rca/data/seed_data.py":
            continue
        if "anomaly_ground_truth" in path.read_text():
            offenders.append(relative)

    assert offenders == []


def test_make_eval_no_longer_not_implemented() -> None:
    source = Path("metric_rca/evals/runner.py").read_text()

    assert "NOT IMPLEMENTED" not in source


def _cases_file(tmp_path: Path) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"case_id":"gmv_paid_ads_drop","question":"Why did paid ads GMV drop?"}',
                '{"case_id":"gmv_no_anomaly","question":"Why did GMV drop?"}',
            ]
        )
    )
    return path


def _fake_runner(question: str, **kwargs: Any) -> dict[str, Any]:
    repo: _EvalRepository = kwargs["repository"]
    case_id = kwargs["run_id"].split("eval-1-", maxsplit=1)[1]
    repo.runner_calls.append(case_id)
    repo.persist_case(kwargs["run_id"], case_id)
    return {"run_id": kwargs["run_id"], "status": repo.agent_runs[kwargs["run_id"]]["status"]}


def _unsafe_runner(question: str, **kwargs: Any) -> dict[str, Any]:
    _fake_runner(question, **kwargs)
    return {"run_id": kwargs["run_id"], "status": "succeeded", "report": {"top_candidate": {"element": "unsafe"}}}


def _gt(
    case_id: str,
    *,
    expected_anomaly: bool = True,
    root_cause_type: str | None = "campaign_traffic_drop",
    dimension: str | None = "channel",
    element: str | None = "paid_ads",
) -> GroundTruth:
    return GroundTruth(
        case_id=case_id,
        business_date=date(2026, 6, 5),
        metric_id="gmv",
        expected_anomaly=expected_anomaly,
        root_cause_type=root_cause_type,
        dimension=dimension,
        element=element,
    )


def _candidate(
    *,
    element: str = "paid_ads",
    contribution_pct: float = 0.9,
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": element,
        "contribution_pct": contribution_pct,
        "signal_severity": 0.8,
        "evidence_support": 1.0,
        "eng_confidence": 0.85,
        "verdict": "confirmed",
        "evidence_ids": evidence_ids or ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"],
    }


def _artifacts(run_id: str, *, selected: dict[str, Any]) -> PersistedArtifacts:
    evidence_ids = [str(value) for value in selected.get("evidence_ids", [])]
    if evidence_ids and all(value.startswith("run-1:") for value in evidence_ids):
        evidence_ids = [f"{run_id}:{value.split(':', maxsplit=1)[1]}" for value in evidence_ids]
    run_selected = {**selected, "evidence_ids": evidence_ids}
    evidences = [
        _evidence(f"{run_id}:E1", {"is_anomaly": True}),
        _evidence(f"{run_id}:E2", {"candidates": [run_selected]}),
        _evidence(f"{run_id}:E3", {"signal_type": "campaign"}),
        _evidence(f"{run_id}:E4", {"selected_candidate": run_selected, "candidates": [run_selected]}),
    ]
    report = {
        "status": "succeeded",
        "top_candidate": {
            "root_cause_type": run_selected["root_cause_type"],
            "dimension": run_selected["dimension"],
            "element": run_selected["element"],
            "verdict": run_selected["verdict"],
        },
        "numeric_claims": [{"name": "contribution_pct", "value": run_selected["contribution_pct"], "evidence_id": f"{run_id}:E4"}],
    }
    return PersistedArtifacts(
        agent_run={"run_id": run_id, "status": "succeeded", "metric_id": "gmv"},
        evidences=evidences,
        trace_steps=[{"seq": 1, "node": "parse_question"}, {"seq": 2, "node": "reflection_verify"}],
        sql_audit=[{"guard_status": "passed"}],
        tasks=[{"task_id": f"{run_id}:task"}],
        report=report,
    )


def _no_anomaly_artifacts(run_id: str, *, trace_node: str = "parse_question") -> PersistedArtifacts:
    return PersistedArtifacts(
        agent_run={"run_id": run_id, "status": "no_anomaly", "metric_id": "gmv"},
        evidences=[_evidence(f"{run_id}:E1", {"is_anomaly": False})],
        trace_steps=[{"seq": 1, "node": trace_node}],
        sql_audit=[{"guard_status": "passed"}],
        tasks=[],
        report={"status": "no_anomaly", "evidence_ids": [f"{run_id}:E1"]},
    )


def _evidence(evidence_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "run_id": evidence_id.split(":", maxsplit=1)[0],
        "guard_status": "passed",
        "result_summary": summary,
    }


class _EvalRepository:
    def __init__(
        self,
        *,
        missing_gt: set[str] | None = None,
        gt_element: str = "paid_ads",
        persisted_element: str = "paid_ads",
    ) -> None:
        self.missing_gt = missing_gt or set()
        self.gt_element = gt_element
        self.persisted_element = persisted_element
        self.gt_requests: list[list[str]] = []
        self.runner_calls: list[str] = []
        self.agent_runs: dict[str, dict[str, Any]] = {}
        self.evidences: dict[str, list[dict[str, Any]]] = {}
        self.trace_steps: dict[str, list[dict[str, Any]]] = {}
        self.sql_audit: dict[str, list[dict[str, Any]]] = {}
        self.tasks: dict[str, list[dict[str, Any]]] = {}
        self.eval_runs: list[dict[str, Any]] = []
        self.case_results: list[dict[str, Any]] = []

    def get_ground_truth_cases(self, case_ids: list[str]) -> dict[str, dict[str, Any]]:
        self.gt_requests.append(case_ids)
        rows = {}
        for case_id in case_ids:
            if case_id in self.missing_gt:
                continue
            gt = _gt(
                case_id,
                expected_anomaly=case_id != "gmv_no_anomaly",
                root_cause_type=None if case_id == "gmv_no_anomaly" else "campaign_traffic_drop",
                dimension=None if case_id == "gmv_no_anomaly" else "channel",
                element=None if case_id == "gmv_no_anomaly" else self.gt_element,
            )
            rows[case_id] = {
                "case_id": gt.case_id,
                "business_date": gt.business_date,
                "metric_id": gt.metric_id,
                "expected_anomaly": int(gt.expected_anomaly),
                "root_cause_type": gt.root_cause_type,
                "dimension": gt.dimension,
                "element": gt.element,
            }
        return rows

    def persist_case(self, run_id: str, case_id: str) -> None:
        if case_id == "gmv_no_anomaly":
            artifacts = _no_anomaly_artifacts(run_id)
        else:
            artifacts = _artifacts(run_id, selected=_candidate(element=self.persisted_element))
        self.agent_runs[run_id] = artifacts.agent_run or {}
        self.evidences[run_id] = artifacts.evidences
        self.trace_steps[run_id] = artifacts.trace_steps
        self.sql_audit[run_id] = artifacts.sql_audit
        self.tasks[run_id] = artifacts.tasks

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        return self.agent_runs.get(run_id)

    def get_evidences(self, run_id: str) -> list[dict[str, Any]]:
        return self.evidences.get(run_id, [])

    def get_operation_tasks(self, run_id: str) -> list[dict[str, Any]]:
        return self.tasks.get(run_id, [])

    def get_trace_steps(self, run_id: str) -> list[dict[str, Any]]:
        return self.trace_steps.get(run_id, [])

    def get_sql_audit_rows(self, run_id: str) -> list[dict[str, Any]]:
        return self.sql_audit.get(run_id, [])

    def create_eval_run(self, row: dict[str, Any]) -> None:
        self.eval_runs.append(row)

    def create_eval_case_result(self, row: dict[str, Any]) -> None:
        self.case_results.append(row)

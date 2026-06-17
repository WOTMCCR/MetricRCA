from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from metric_rca.config.settings import Settings
from metric_rca.evals.models import EvalRuntimeError, GroundTruth, PersistedArtifacts, RootCauseTruth
from metric_rca.evals.runner import (
    MEMORY_TREATMENT_CASES_PATH,
    _attempt_run_id,
    _cases_path_for_suite,
    _eval_summary,
    _read_artifacts,
    _run_id,
    load_cases,
    run_eval,
)
from metric_rca.evals.scorer import (
    dangerous_sql_blocked,
    score_case,
    summarize_memory_retrieval,
    summarize_scores,
)

PUBLIC_CASES_PATH = Path("metric_rca/evals/regression_public_cases.jsonl")
PRIVATE_GROUND_TRUTH_PATH = Path("metric_rca/evals/regression_private_ground_truth.jsonl")
MEMORY_TREATMENT_PRIVATE_GROUND_TRUTH_PATH = Path("metric_rca/evals/memory_treatment_private_ground_truth.jsonl")
PUBLIC_CASE_FIELDS = {"case_id", "question", "tags"}
PRIVATE_GROUND_TRUTH_FIELDS = {
    "case_id",
    "expected_metric_id",
    "expected_anomaly",
    "expected_root_cause_type",
    "expected_dimension",
    "expected_element",
    "expected_business_date",
}
OPTIONAL_PRIVATE_GROUND_TRUTH_FIELDS = {"root_causes"}
ANSWER_BEARING_FIELDS = PRIVATE_GROUND_TRUTH_FIELDS - {"case_id"}
REGRESSION_CASE_COUNT = 44


def test_eval_loads_cases_and_ground_truth(tmp_path: Path) -> None:
    cases_path = _cases_file(tmp_path)
    cases = load_cases(cases_path)
    repo = _EvalRepository()

    run_eval(repository=repo, rca_runner=_fake_runner, cases_path=cases_path, output_dir=tmp_path, eval_id="eval-1")

    assert [case.case_id for case in cases] == ["gmv_paid_ads_drop", "gmv_no_anomaly"]
    assert repo.gt_requests == [["gmv_paid_ads_drop", "gmv_no_anomaly"]]
    assert repo.gt_scopes == [("regression", "regression")]


def test_legacy_cases_path_symbol_is_removed() -> None:
    with pytest.raises(ImportError):
        exec("from metric_rca.evals.runner import LEGACY_CASES_PATH", {})


def test_eval_writes_grpo_dataset_with_predictions_and_trace_artifacts(tmp_path: Path) -> None:
    cases_path = _cases_file(tmp_path)
    predictions_dir = tmp_path / "eval-1"
    predictions_dir.mkdir(parents=True)
    (predictions_dir / "predictions.jsonl").write_text(
        json.dumps(
            {
                "case_id": "gmv_paid_ads_drop",
                "aspect": "outcome",
                "prediction": {"top1_ok": True},
                "reasoning": "campaign fixture should select paid ads",
                "risks": [],
            }
        )
        + "\n"
    )
    repo = _EvalRepository()

    run_eval(repository=repo, rca_runner=_fake_runner, cases_path=cases_path, output_dir=tmp_path, eval_id="eval-1")

    dataset_path = tmp_path / "eval-1" / "grpo_dataset" / "trajectories.jsonl"
    manifest_path = tmp_path / "eval-1" / "grpo_dataset" / "manifest.json"
    records = [json.loads(line) for line in dataset_path.read_text().splitlines() if line.strip()]
    manifest = json.loads(manifest_path.read_text())
    paid_ads_records = [record for record in records if record["case"]["case_id"] == "gmv_paid_ads_drop"]
    assert manifest["record_count"] == 4
    assert sorted({record["phase"] for record in records}) == ["baseline", "memory"]
    assert paid_ads_records[0]["predictions"][0]["aspect"] == "outcome"
    assert paid_ads_records[0]["trajectory"]["trace_steps"]
    assert paid_ads_records[0]["judge"]["reward"] == 1.0


def test_eval_require_predictions_fails_before_running_cases(tmp_path: Path) -> None:
    repo = _EvalRepository()

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=_fake_runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-missing-predictions",
            require_predictions=True,
        )

    assert exc_info.value.code == "GRPO_PREDICTIONS_MISSING"
    assert repo.gt_requests == []
    assert repo.runner_calls == []
    assert repo.eval_run_updates == []


def test_eval_loader_defaults_to_public_regression_cases_without_expected_fields() -> None:
    cases = load_cases()
    public_rows = _read_jsonl(PUBLIC_CASES_PATH)

    assert len(cases) == REGRESSION_CASE_COUNT
    assert [case.case_id for case in cases] == [row["case_id"] for row in public_rows]
    assert all(set(case.__dict__) == PUBLIC_CASE_FIELDS for case in cases)
    assert all(ANSWER_BEARING_FIELDS.isdisjoint(case.__dict__) for case in cases)


def test_eval_suite_default_case_paths_keep_memory_treatment_isolated() -> None:
    regression_cases = load_cases(_cases_path_for_suite("regression"))
    treatment_cases = load_cases(_cases_path_for_suite("memory-treatment"))
    treatment_private = _read_jsonl(MEMORY_TREATMENT_PRIVATE_GROUND_TRUTH_PATH)

    assert _cases_path_for_suite("acceptance").resolve() == PUBLIC_CASES_PATH.resolve()
    assert _cases_path_for_suite("memory-treatment").resolve() == MEMORY_TREATMENT_CASES_PATH.resolve()
    assert [case.case_id for case in treatment_cases] == ["M01_gmv_memory_product_prior"]
    assert [case.case_id for case in regression_cases] == [row["case_id"] for row in _read_jsonl(PUBLIC_CASES_PATH)]
    assert all("memory_treatment" in case.tags for case in treatment_cases)
    assert all(set(row) == PRIVATE_GROUND_TRUTH_FIELDS for row in treatment_private)
    assert [row["case_id"] for row in treatment_private] == [case.case_id for case in treatment_cases]
    assert all("question" not in row and "tags" not in row for row in treatment_private)


def test_run_eval_uses_settings_eval_suite_for_default_case_selection(tmp_path: Path) -> None:
    repo = _EvalRepository(gt_element="2", persisted_element="2")

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=_fake_runner,
            output_dir=tmp_path,
            eval_id="eval-memory",
            settings=Settings(
                db_dsn="mysql+pymysql://app:app@localhost/db",
                readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
                llm_provider="openai",
                llm_model="gpt-test",
                llm_api_key="key",
                eval_suite="memory-treatment",
            ),
        )

    assert exc_info.value.code == "EVAL_THRESHOLD_NOT_MET"
    assert repo.gt_requests == [["M01_gmv_memory_product_prior"]]
    assert repo.gt_scopes == [("memory-treatment", "regression")]


def test_run_eval_uses_acceptance_ground_truth_profile_scope(tmp_path: Path) -> None:
    repo = _EvalRepository()

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=_fake_runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-acceptance",
            settings=Settings(
                db_dsn="mysql+pymysql://app:app@localhost/db",
                readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
                llm_provider="openai",
                llm_model="gpt-test",
                llm_api_key="key",
                eval_suite="acceptance",
            ),
        )

    assert exc_info.value.code == "EVAL_THRESHOLD_NOT_MET"
    assert repo.gt_scopes == [("regression", "acceptance")]


def test_public_regression_cases_have_no_answer_bearing_fields_and_allow_metric_terms() -> None:
    rows = _read_jsonl(PUBLIC_CASES_PATH)

    assert len(rows) == REGRESSION_CASE_COUNT
    assert all(set(row) == PUBLIC_CASE_FIELDS for row in rows)
    assert all(ANSWER_BEARING_FIELDS.isdisjoint(row) for row in rows)
    questions = " ".join(str(row["question"]).lower() for row in rows)
    for allowed_metric_term in [
        "gmv",
        "traffic",
        "conversion rate",
        "refund rate",
        "stockout rate",
        "complaint rate",
    ]:
        assert allowed_metric_term in questions


def test_private_regression_ground_truth_matches_public_case_ids() -> None:
    public_rows = _read_jsonl(PUBLIC_CASES_PATH)
    private_rows = _read_jsonl(PRIVATE_GROUND_TRUTH_PATH)

    assert len(private_rows) == REGRESSION_CASE_COUNT
    assert all(PRIVATE_GROUND_TRUTH_FIELDS <= set(row) <= PRIVATE_GROUND_TRUTH_FIELDS | OPTIONAL_PRIVATE_GROUND_TRUTH_FIELDS for row in private_rows)
    assert [row["case_id"] for row in private_rows] == [row["case_id"] for row in public_rows]
    assert all("question" not in row and "tags" not in row for row in private_rows)


def test_phase_c_complex_cases_cover_required_families_with_weighted_root_causes() -> None:
    public_rows = _read_jsonl(PUBLIC_CASES_PATH)
    private_rows = _read_jsonl(PRIVATE_GROUND_TRUTH_PATH)
    private_by_id = {row["case_id"]: row for row in private_rows}
    new_case_ids = [row["case_id"] for row in public_rows if "phase_c" in row["tags"]]

    assert len(new_case_ids) == 16
    families = {tag for row in public_rows if row["case_id"] in new_case_ids for tag in row["tags"]}
    assert {"multi_cause", "interaction", "lagged", "weak_signal"} <= families

    multi_cause_rows = [private_by_id[case_id] for case_id in new_case_ids if case_id.startswith("MC")]
    assert len(multi_cause_rows) >= 4
    for row in multi_cause_rows:
        root_causes = row.get("root_causes")
        assert isinstance(root_causes, list)
        assert len(root_causes) >= 2
        assert round(sum(float(cause["weight"]) for cause in root_causes), 6) == 1.0
        assert all({"root_cause_type", "dimension", "element", "weight"} <= set(cause) for cause in root_causes)


def test_seed_ground_truth_preserves_explicit_multi_cause_weights() -> None:
    from metric_rca.data.seed_data import _ground_truth_row_with_metadata

    row = _ground_truth_row_with_metadata(
        {
            "case_id": "MC00_phase_c_multi_cause",
            "business_date": date(2026, 6, 1),
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": [
                {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.6},
                {"root_cause_type": "stockout", "dimension": "category", "element": "electronics", "weight": 0.4},
            ],
        },
        seed=20260606,
        seed_profile="regression",
    )

    root_causes = json.loads(row["root_causes"])
    assert root_causes == [
        {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads", "weight": 0.6},
        {"root_cause_type": "stockout", "dimension": "category", "element": "electronics", "weight": 0.4},
    ]


def test_eval_loader_rejects_answer_bearing_public_case_rows(tmp_path: Path) -> None:
    path = tmp_path / "public_cases.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "leaky_case",
                "question": "Why did GMV move?",
                "tags": ["regression"],
                "expected_element": "paid_ads",
            }
        )
    )

    with pytest.raises(EvalRuntimeError) as exc_info:
        load_cases(path)

    assert exc_info.value.code == "EVAL_CASE_PRIVATE_FIELD_LEAKED"


def test_eval_cases_are_natural_questions_without_answer_leakage() -> None:
    cases = load_cases(PUBLIC_CASES_PATH)
    by_id = {case.case_id: case.question.lower() for case in cases}

    assert len(cases) == REGRESSION_CASE_COUNT
    assert all("metric_id=" not in question for question in by_id.values())
    discovery_forbidden = {
        "stockout",
        "refund",
        "uv",
        "aov",
        "logistics",
        "high-price",
        "high price",
        "paid_ads",
        "paid ads",
        "social",
        "electronics",
        "organic",
        "product 2",
    }
    # C25 is discovery, but "refund rate" is the target metric name, not a leaked
    # root-cause mechanism or discovered dimension element.
    for case_id in [
        "C06_gmv_multi_channel_drop",
        "C07_gmv_category_channel_cross",
        "C08_gmv_aov_drop",
        "C09_gmv_uv_organic_drop",
        "C21_cvr_discovery",
        "C23_uv_organic_drop",
        "C26_ambiguous_intent",
        "C27_composite_cause",
        "C28_multi_day_drift",
    ]:
        assert [token for token in discovery_forbidden if token in by_id[case_id]] == []


def test_eval_run_ids_leave_room_for_long_evidence_aliases() -> None:
    run_id = _attempt_run_id(_run_id("eval-4787ddf2", "C06_gmv_multi_channel_drop"), 4)

    assert len(run_id) <= 42
    assert len(f"{run_id}:E3_cat_electronics") <= 64


def test_eval_run_ids_for_long_case_ids_are_collision_resistant() -> None:
    shared_prefix = "case_" + ("x" * 80)
    first = _run_id("eval-4787ddf2", f"{shared_prefix}_a")
    second = _run_id("eval-4787ddf2", f"{shared_prefix}_b")

    assert first != second
    assert len(first) <= 42
    assert len(second) <= 42
    assert len(_attempt_run_id(first, 2)) <= 42


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
    assert repo.eval_run_updates[-1]["summary"]["thresholds_met"] is False


def test_eval_accepts_gpt5_nano_and_records_provider_model(tmp_path: Path) -> None:
    settings = Settings(
        db_dsn="sqlite://",
        readonly_db_dsn="sqlite://",
        llm_provider="openai",
        llm_model="gpt-5-nano",
        llm_api_key="key",
    )

    output = run_eval(
        repository=_EvalRepository(),
        rca_runner=_fake_runner,
        settings=settings,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
    )

    assert output["summary"]["llm_provider"] == "openai"
    assert output["summary"]["llm_model"] == "gpt-5-nano"
    assert output["summary"]["thresholds_met"] is True


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

    assert repo.runner_calls == [
        ("gmv_paid_ads_drop", True),
        ("gmv_no_anomaly", True),
        ("gmv_paid_ads_drop", False),
        ("gmv_no_anomaly", False),
    ]


def test_eval_memory_prepass_reads_memory_without_writing_finalize_records(tmp_path: Path) -> None:
    repo = _EvalRepository()
    observed_settings: list[tuple[str, bool, bool]] = []

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        run_id = kwargs["run_id"]
        case_id = _case_id_from_run_id(run_id)
        settings = kwargs["settings"]
        observed_settings.append(
            (case_id, bool(settings.memory_enabled), bool(settings.memory_write_on_finalize))
        )
        repo.persist_case(run_id, case_id)
        return {"run_id": run_id, "status": repo.agent_runs[run_id]["status"], "error_code": None}

    run_eval(
        repository=repo,
        rca_runner=runner,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
    )

    assert observed_settings == [
        ("gmv_paid_ads_drop", True, False),
        ("gmv_no_anomaly", True, False),
        ("gmv_paid_ads_drop", False, True),
        ("gmv_no_anomaly", False, True),
    ]


def test_eval_outputs_real_paired_memory_retrieval_summary(tmp_path: Path) -> None:
    repo = _EvalRepository()

    output = run_eval(
        repository=repo,
        rca_runner=_fake_runner,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
    )

    assert output["summary"]["memory_hit_improvement"] >= 0
    assert output["summary"]["memory_pollution_ok"] is True
    assert [row["detail"]["memory_enabled"] for row in output["memory_cases"]] == [True, True]
    assert {row["case_id"] for row in output["memory_cases"]} == {"gmv_paid_ads_drop", "gmv_no_anomaly"}


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

    assert repo.eval_run_updates[-1]["summary"] == output["summary"]
    assert repo.eval_run_updates[-1]["summary"]["complete"] is True
    assert any(row["summary"]["complete"] is False for row in repo.eval_run_updates[:-1])
    assert "llm_model" in repo.eval_run_updates[-1]["summary"]
    assert [row["case_id"] for row in repo.case_results] == ["gmv_paid_ads_drop", "gmv_no_anomaly"]


def test_eval_parallel_cases_use_worker_repositories_and_preserve_output_order(tmp_path: Path) -> None:
    main_repo = _EvalRepository()
    worker_repos: list[_EvalRepository] = []
    lock = threading.Lock()
    active = 0
    max_active = 0

    def repository_factory() -> _EvalRepository:
        repo = _EvalRepository()
        worker_repos.append(repo)
        return repo

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal active, max_active
        repo: _EvalRepository = kwargs["repository"]
        run_id = kwargs["run_id"]
        case_id = _case_id_from_run_id(run_id)
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        try:
            repo.persist_case(run_id, case_id)
            return {"run_id": run_id, "status": repo.agent_runs[run_id]["status"], "error_code": None}
        finally:
            with lock:
                active -= 1

    output = run_eval(
        repository=main_repo,
        repository_factory=repository_factory,
        rca_runner=runner,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
        settings=Settings(
            db_dsn="mysql+pymysql://app:app@localhost/db",
            readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
            llm_provider="openai",
            llm_model="gpt-test",
            llm_api_key="key",
            eval_concurrency=2,
        ),
    )

    assert max_active == 2
    assert len(worker_repos) == 4
    assert worker_repos[0] is not worker_repos[1]
    assert all(repo.closed for repo in worker_repos)
    assert [row["case_id"] for row in output["cases"]] == ["gmv_paid_ads_drop", "gmv_no_anomaly"]
    assert [row["case_id"] for row in main_repo.case_results] == ["gmv_paid_ads_drop", "gmv_no_anomaly"]
    assert main_repo.eval_run_updates[-1]["summary"]["complete"] is True


def test_eval_memory_prepass_uses_eval_concurrency_and_write_isolation(tmp_path: Path) -> None:
    main_repo = _EvalRepository()
    active_memory = 0
    max_active_memory = 0
    lock = threading.Lock()
    memory_settings_seen: list[tuple[bool, bool]] = []

    def repository_factory() -> _EvalRepository:
        return _EvalRepository()

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal active_memory, max_active_memory
        repo: _EvalRepository = kwargs["repository"]
        run_id = kwargs["run_id"]
        case_id = _case_id_from_run_id(run_id)
        settings = kwargs["settings"]
        if settings.memory_enabled:
            with lock:
                active_memory += 1
                max_active_memory = max(max_active_memory, active_memory)
                memory_settings_seen.append((settings.memory_enabled, settings.memory_write_on_finalize))
            time.sleep(0.05)
            try:
                repo.persist_case(run_id, case_id)
                return {"run_id": run_id, "status": repo.agent_runs[run_id]["status"], "error_code": None}
            finally:
                with lock:
                    active_memory -= 1
        repo.persist_case(run_id, case_id)
        return {"run_id": run_id, "status": repo.agent_runs[run_id]["status"], "error_code": None}

    output = run_eval(
        repository=main_repo,
        repository_factory=repository_factory,
        rca_runner=runner,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
        settings=Settings(
            db_dsn="mysql+pymysql://app:app@localhost/db",
            readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
            llm_provider="openai",
            llm_model="gpt-test",
            llm_api_key="key",
            eval_concurrency=2,
        ),
    )

    assert max_active_memory == 2
    assert sorted(memory_settings_seen) == [(True, False), (True, False)]
    assert [row["case_id"] for row in output["memory_cases"]] == ["gmv_paid_ads_drop", "gmv_no_anomaly"]


def test_eval_writes_progress_summary_when_memory_prepass_fails(tmp_path: Path) -> None:
    repo = _EvalRepository()

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        run_id = kwargs["run_id"]
        case_id = _case_id_from_run_id(run_id)
        if kwargs["settings"].memory_enabled and case_id == "gmv_no_anomaly":
            return {"run_id": run_id, "status": "failed", "error_code": "REFLECTION_REPAIR_FAILED"}
        repo.persist_case(run_id, case_id)
        return {"run_id": run_id, "status": repo.agent_runs[run_id]["status"], "error_code": None}

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
            settings=Settings(
                db_dsn="mysql+pymysql://app:app@localhost/db",
                readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
                llm_provider="openai",
                llm_model="gpt-test",
                llm_api_key="key",
                eval_llm_max_attempts=1,
            ),
        )

    assert exc_info.value.code == "REFLECTION_REPAIR_FAILED"
    assert repo.eval_run_updates
    assert repo.eval_run_updates[-1]["summary"]["complete"] is False
    assert repo.eval_run_updates[-1]["summary"]["completed_memory_case_total"] == 1
    assert repo.eval_run_updates[-1]["summary"]["completed_case_total"] == 0
    assert not (tmp_path / "eval-1.json").exists()


def test_eval_parallel_failure_returns_before_blocked_worker_finishes_and_does_not_submit_remaining_cases(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        "\n".join(
            [
                '{"case_id":"case_1","question":"Why did case 1 move?"}',
                '{"case_id":"case_2","question":"Why did case 2 move?"}',
                '{"case_id":"case_3","question":"Why did case 3 move?"}',
            ]
        )
    )
    main_repo = _EvalRepository()
    started: list[str] = []
    started_lock = threading.Lock()
    release_running = threading.Event()

    def repository_factory() -> _EvalRepository:
        return _EvalRepository()

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        repo: _EvalRepository = kwargs["repository"]
        run_id = kwargs["run_id"]
        case_id = _case_id_from_run_id(run_id)
        if kwargs["settings"].memory_enabled:
            repo.persist_case(run_id, case_id)
            return {"run_id": run_id, "status": repo.agent_runs[run_id]["status"], "error_code": None}
        if not kwargs["settings"].memory_enabled:
            with started_lock:
                started.append(case_id)
        if case_id == "case_1" and not kwargs["settings"].memory_enabled:
            raise RuntimeError("case_1 failed")
        release_running.wait(timeout=1.0)
        repo.persist_case(run_id, case_id)
        return {"run_id": run_id, "status": repo.agent_runs[run_id]["status"], "error_code": None}

    start = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="case_1 failed"):
            run_eval(
                repository=main_repo,
                repository_factory=repository_factory,
                rca_runner=runner,
                cases_path=cases_path,
                output_dir=tmp_path,
                eval_id="eval-1",
                settings=Settings(
                    db_dsn="mysql+pymysql://app:app@localhost/db",
                    readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
                    llm_provider="openai",
                    llm_model="gpt-test",
                    llm_api_key="key",
                    eval_concurrency=2,
                ),
            )
    finally:
        release_running.set()

    elapsed = time.monotonic() - start
    assert elapsed < 0.5
    assert "case_1" in started
    assert "case_3" not in started
    assert main_repo.case_results == []
    assert main_repo.eval_runs == []


def test_eval_parallel_requires_worker_repository_factory_for_injected_repository(tmp_path: Path) -> None:
    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=_EvalRepository(),
            rca_runner=_fake_runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
            settings=Settings(
                db_dsn="mysql+pymysql://app:app@localhost/db",
                readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
                llm_provider="openai",
                llm_model="gpt-test",
                llm_api_key="key",
                eval_concurrency=2,
            ),
        )

    assert exc_info.value.code == "EVAL_CONCURRENCY_REPOSITORY_UNSAFE"


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


def test_eval_evidence_coverage_requires_rank_evidence() -> None:
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=_artifacts(
            "run-1",
            selected=_candidate(evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"]),
        ),
    )

    assert score["evidence_coverage"] == 0.8


def test_eval_scores_adtributor_used_and_multi_agent_path() -> None:
    score = score_case(
        case_id="C06_gmv_multi_channel_drop",
        ground_truth=_gt("C06_gmv_multi_channel_drop"),
        artifacts=_artifacts("run-1", selected=_candidate(dimension_elements=[("channel", "paid_ads"), ("channel", "social")])),
    )

    assert score["adtributor_used"] == 1
    assert score["multi_agent_path"] == "single_agent"
    assert score["top1_ok"] == 1


def test_eval_multi_cause_scoring_uses_root_causes_json_not_case_id_rules() -> None:
    ground_truth = _gt(
        "custom_multi_cause_case",
        root_causes=(
            RootCauseTruth("campaign_traffic_drop", "channel", "paid_ads", 0.62),
            RootCauseTruth("stockout", "category", "electronics", 0.25),
        ),
    )
    artifacts = _artifacts(
        "run-1",
        selected=_candidate(),
        candidates=[
            _candidate(),
            _candidate(root_cause_type="stockout", dimension="category", element="electronics", contribution_pct=0.25),
        ],
    )

    score = score_case(case_id="not_special_cased", ground_truth=ground_truth, artifacts=artifacts)

    assert score["dominant_top1_ok"] == 1
    assert score["root_cause_set_recall"] == 1.0
    assert score["root_cause_set_precision"] == 1.0
    assert score["weighted_explanation_coverage"] == 1.0
    assert score["top3_contains_all_major_causes"] == 1


def test_eval_scoring_matches_truth_against_candidate_dimension_elements() -> None:
    ground_truth = _gt(
        "custom_merchandise_case",
        root_causes=(RootCauseTruth("aov_drop", "category", "fashion", 1.0),),
    )
    candidate = _candidate(
        root_cause_type="aov_drop",
        dimension="product",
        element="142",
        dimension_elements=[("product", "142"), ("category", "fashion")],
    )

    score = score_case(
        case_id="custom_merchandise_case",
        ground_truth=ground_truth,
        artifacts=_artifacts("run-1", selected=candidate),
    )

    assert score["dominant_top1_ok"] == 1
    assert score["root_cause_set_recall"] == 1.0
    assert score["top3_contains_all_major_causes"] == 1


def test_eval_multi_cause_scoring_penalizes_missing_major_cause_without_case_id_lookup() -> None:
    ground_truth = _gt(
        "another_custom_multi_cause_case",
        root_causes=(
            RootCauseTruth("campaign_traffic_drop", "channel", "paid_ads", 0.62),
            RootCauseTruth("stockout", "category", "electronics", 0.25),
        ),
    )

    score = score_case(
        case_id="C07_gmv_category_channel_cross",
        ground_truth=ground_truth,
        artifacts=_artifacts("run-1", selected=_candidate()),
    )

    assert score["top1_ok"] == 1
    assert score["root_cause_set_recall"] == 0.5
    assert score["weighted_explanation_coverage"] == round(0.62 / 0.87, 6)
    assert score["top3_contains_all_major_causes"] == 0


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


def test_eval_expected_anomaly_requires_current_run_guard_passed_e1() -> None:
    artifacts = _artifacts("run-1", selected=_candidate())
    rejected_e1 = PersistedArtifacts(
        agent_run=artifacts.agent_run,
        evidences=[
            {**artifacts.evidences[0], "guard_status": "rejected"},
            *artifacts.evidences[1:],
        ],
        trace_steps=artifacts.trace_steps,
        sql_audit=artifacts.sql_audit,
        tasks=artifacts.tasks,
        report=artifacts.report,
    )
    foreign_e1 = PersistedArtifacts(
        agent_run=artifacts.agent_run,
        evidences=[
            {**artifacts.evidences[0], "evidence_id": "other-run:E1", "run_id": "other-run"},
            *artifacts.evidences[1:],
        ],
        trace_steps=artifacts.trace_steps,
        sql_audit=artifacts.sql_audit,
        tasks=artifacts.tasks,
        report=artifacts.report,
    )

    rejected_score = score_case(case_id="gmv_paid_ads_drop", ground_truth=_gt("gmv_paid_ads_drop"), artifacts=rejected_e1)
    foreign_score = score_case(case_id="gmv_paid_ads_drop", ground_truth=_gt("gmv_paid_ads_drop"), artifacts=foreign_e1)

    assert rejected_score["anomaly_ok"] == 0
    assert foreign_score["anomaly_ok"] == 0


def test_eval_report_traceable_ok_requires_persisted_numeric_claims() -> None:
    artifacts = _artifacts("run-1", selected=_candidate())
    score = score_case(case_id="gmv_paid_ads_drop", ground_truth=_gt("gmv_paid_ads_drop"), artifacts=artifacts)
    broken = PersistedArtifacts(
        agent_run=artifacts.agent_run,
        evidences=[
            {
                **row,
                "result_summary": {"selected_candidate": _candidate(contribution_pct=0.1)},
            }
            if row["evidence_id"] == "run-1:E4"
            else row
            for row in artifacts.evidences
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


def test_eval_memory_pollution_checks_candidate_list_and_report_claims() -> None:
    artifacts = _artifacts("run-1", selected=_candidate())
    polluted_candidate = _candidate(
        evidence_ids=["run-1:E1", "memory:semantic:gmv", "run-1:E3", "run-1:E4"]
    )
    e4 = artifacts.evidences[-1]
    polluted_e4 = {
        **e4,
        "result_summary": {
            **e4["result_summary"],
            "candidates": [e4["result_summary"]["selected_candidate"], polluted_candidate],
            "contribution_set": {
                **e4["result_summary"]["contribution_set"],
                "candidates": [e4["result_summary"]["selected_candidate"], polluted_candidate],
            },
        },
    }
    polluted_report = {
        **artifacts.report,
        "numeric_claims": [{"name": "memory_claim", "value": 1, "evidence_id": "memory:semantic:gmv"}],
    }

    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=PersistedArtifacts(
            agent_run=artifacts.agent_run,
            evidences=[*artifacts.evidences[:-1], polluted_e4],
            trace_steps=artifacts.trace_steps,
            sql_audit=artifacts.sql_audit,
            tasks=artifacts.tasks,
            report=polluted_report,
        ),
    )

    assert score["memory_pollution_ok"] == 0


def test_eval_memory_pollution_rejects_cross_run_evidence_ids() -> None:
    polluted = _candidate(evidence_ids=["other-run:E1", "run-1:E2", "run-1:E3", "run-1:E4"])
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=_artifacts("run-1", selected=polluted),
    )

    assert score["memory_pollution_ok"] == 0


def test_eval_memory_pollution_rejects_fabricated_current_run_evidence_ids() -> None:
    polluted = _candidate(evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3_fake", "run-1:E4"])
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=_artifacts("run-1", selected=polluted),
    )

    assert score["memory_pollution_ok"] == 0


def test_eval_memory_pollution_rejects_empty_evidence_ids() -> None:
    polluted = {**_candidate(), "evidence_ids": []}
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=_artifacts("run-1", selected=polluted),
    )

    assert score["memory_pollution_ok"] == 0


def test_eval_memory_pollution_rejects_non_guard_passed_evidence_ids() -> None:
    artifacts = _artifacts("run-1", selected=_candidate())
    rejected_e3 = {
        **artifacts.evidences[2],
        "guard_status": "rejected",
    }
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=PersistedArtifacts(
            agent_run=artifacts.agent_run,
            evidences=[artifacts.evidences[0], artifacts.evidences[1], rejected_e3, artifacts.evidences[3]],
            trace_steps=artifacts.trace_steps,
            sql_audit=artifacts.sql_audit,
            tasks=artifacts.tasks,
            report=artifacts.report,
        ),
    )

    assert score["memory_pollution_ok"] == 0


def test_eval_memory_pollution_rejects_untrusted_memory_record_artifacts() -> None:
    artifacts = _artifacts("run-1", selected=_candidate())
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=PersistedArtifacts(
            agent_run=artifacts.agent_run,
            evidences=artifacts.evidences,
            trace_steps=artifacts.trace_steps,
            sql_audit=artifacts.sql_audit,
            tasks=artifacts.tasks,
            report=artifacts.report,
            memory_records=[
                _memory_record(
                    memory_id="mem-untrusted",
                    layer="episodic",
                    mem_key="gmv|run",
                    payload={"run_id": "previous-run", "metric_id": "gmv"},
                    source="untrusted",
                )
            ],
        ),
    )

    assert score["memory_pollution_ok"] == 0


def test_eval_memory_pollution_rejects_cross_scope_memory_read_hits() -> None:
    artifacts = _artifacts("run-1", selected=_candidate())
    scoped_e1 = {
        **artifacts.evidences[0],
        "result_summary": {"is_anomaly": True, "filters": {"category": "electronics"}},
    }
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=PersistedArtifacts(
            agent_run=artifacts.agent_run,
            evidences=[scoped_e1, *artifacts.evidences[1:]],
            trace_steps=[
                *artifacts.trace_steps,
                {
                    "seq": 3,
                    "node": "memory_read",
                    "action": "read_layers",
                    "output_summary": {
                        "hits": [
                            {
                                "memory_id": "mem-fashion",
                                "layer": "episodic",
                                "mem_key": "gmv|run",
                                "filters": {"category": "fashion"},
                                "confidence": 0.8,
                                "source": "reflection_verified",
                            }
                        ]
                    },
                },
            ],
            sql_audit=artifacts.sql_audit,
            tasks=artifacts.tasks,
            report=artifacts.report,
            memory_records=[
                _memory_record(
                    memory_id="mem-fashion",
                    layer="episodic",
                    mem_key="gmv|run",
                    payload={
                        "run_id": "previous-run",
                        "metric_id": "gmv",
                        "filters": {"category": "fashion"},
                    },
                )
            ],
        ),
    )

    assert score["memory_pollution_ok"] == 0


def test_eval_memory_pollution_accepts_trusted_same_scope_memory_artifacts() -> None:
    artifacts = _artifacts("run-1", selected=_candidate())
    scoped_e1 = {
        **artifacts.evidences[0],
        "result_summary": {"is_anomaly": True, "filters": {"category": "electronics"}},
    }
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=PersistedArtifacts(
            agent_run=artifacts.agent_run,
            evidences=[scoped_e1, *artifacts.evidences[1:]],
            trace_steps=[
                *artifacts.trace_steps,
                {
                    "seq": 3,
                    "node": "memory_read",
                    "action": "read_layers",
                    "output_summary": {
                        "hits": [
                            {
                                "memory_id": "semantic-gmv",
                                "layer": "semantic",
                                "mem_key": "gmv|semantic",
                                "filters": {},
                                "confidence": 0.95,
                                "source": "system_verified",
                            },
                            {
                                "memory_id": "mem-electronics",
                                "layer": "episodic",
                                "mem_key": "gmv|run",
                                "filters": {"category": "electronics"},
                                "confidence": 0.8,
                                "source": "reflection_verified",
                            },
                        ]
                    },
                },
            ],
            sql_audit=artifacts.sql_audit,
            tasks=artifacts.tasks,
            report=artifacts.report,
            memory_records=[
                _memory_record(
                    memory_id="semantic-gmv",
                    layer="semantic",
                    mem_key="gmv|semantic",
                    payload={"metric_id": "gmv"},
                    source="system_verified",
                    confidence=0.95,
                ),
                _memory_record(
                    memory_id="mem-electronics",
                    layer="episodic",
                    mem_key="gmv|run",
                    payload={
                        "run_id": "previous-run",
                        "metric_id": "gmv",
                        "filters": {"category": "electronics"},
                    },
                ),
            ],
        ),
    )

    assert score["memory_pollution_ok"] == 1


def test_eval_read_artifacts_includes_memory_records_for_pollution_scoring() -> None:
    repo = _EvalRepository()
    repo.persist_case("run-1", "gmv_paid_ads_drop")
    repo.memory_records["run-1"] = [
        _memory_record(
            memory_id="mem-wrong-metric",
            layer="semantic",
            mem_key="refund_rate|semantic",
            payload={"metric_id": "refund_rate"},
            source="system_verified",
        )
    ]

    artifacts = _read_artifacts(repo, "run-1")
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=artifacts,
    )

    assert artifacts.memory_records == repo.memory_records["run-1"]
    assert score["memory_pollution_ok"] == 0


def test_eval_read_artifacts_fails_without_memory_artifact_reader() -> None:
    repo = _NoMemoryArtifactRepository()
    repo.persist_case("run-1", "gmv_paid_ads_drop")

    with pytest.raises(EvalRuntimeError) as exc:
        _read_artifacts(repo, "run-1")

    assert exc.value.code == "EVAL_MEMORY_ARTIFACT_UNSUPPORTED"


def test_eval_case_detail_records_token_and_latency_from_trace() -> None:
    artifacts = _artifacts("run-1", selected=_candidate())
    score = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=_gt("gmv_paid_ads_drop"),
        artifacts=PersistedArtifacts(
            agent_run=artifacts.agent_run,
            evidences=artifacts.evidences,
            trace_steps=[
                {"seq": 1, "node": "parse_question", "latency_ms": 5},
                {
                    "seq": 2,
                    "node": "llm_call",
                    "latency_ms": 95,
                    "token_usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
                },
            ],
            sql_audit=artifacts.sql_audit,
            tasks=artifacts.tasks,
            report=artifacts.report,
        ),
    )

    assert score["detail"]["token_count"] == 10
    assert score["detail"]["latency_ms"] == 100


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
    p6_polluted = score_case(
        case_id="gmv_no_anomaly",
        ground_truth=_gt("gmv_no_anomaly", expected_anomaly=False, root_cause_type=None, dimension=None, element=None),
        artifacts=_no_anomaly_artifacts("run-1", trace_action="rank_root_causes"),
    )

    assert clean["no_anomaly_task_ok"] == 1
    assert clean["anomaly_ok"] == 1
    assert polluted["no_anomaly_task_ok"] == 0
    assert p6_polluted["no_anomaly_task_ok"] == 0


def test_no_anomaly_score_requires_structured_report_e1_and_guard_passed_evidence() -> None:
    ground_truth = _gt(
        "gmv_no_anomaly",
        expected_anomaly=False,
        root_cause_type=None,
        dimension=None,
        element=None,
    )
    missing_report_evidence = score_case(
        case_id="gmv_no_anomaly",
        ground_truth=ground_truth,
        artifacts=PersistedArtifacts(
            agent_run={"run_id": "run-1", "status": "no_anomaly", "metric_id": "gmv"},
            evidences=[_evidence("run-1:E1", {"is_anomaly": False})],
            trace_steps=[{"seq": 1, "node": "parse_question"}],
            sql_audit=[{"guard_status": "passed"}],
            tasks=[],
            report={"status": "no_anomaly"},
        ),
    )
    rejected_e1 = score_case(
        case_id="gmv_no_anomaly",
        ground_truth=ground_truth,
        artifacts=PersistedArtifacts(
            agent_run={"run_id": "run-2", "status": "no_anomaly", "metric_id": "gmv"},
            evidences=[{**_evidence("run-2:E1", {"is_anomaly": False}), "guard_status": "rejected"}],
            trace_steps=[{"seq": 1, "node": "parse_question"}],
            sql_audit=[{"guard_status": "passed"}],
            tasks=[],
            report={"status": "no_anomaly", "evidence_ids": ["run-2:E1"]},
        ),
    )

    assert missing_report_evidence["report_traceable_ok"] == 0
    assert missing_report_evidence["memory_pollution_ok"] == 0
    assert missing_report_evidence["anomaly_ok"] == 0
    assert missing_report_evidence["evidence_coverage"] == 0.0
    assert rejected_e1["evidence_coverage"] == 0.0
    assert rejected_e1["anomaly_ok"] == 0

    summary = summarize_scores([missing_report_evidence], dangerous_sql_blocked=True)

    assert summary["no_anomaly_correct"] is False


def test_no_anomaly_score_rejects_root_cause_content_in_report() -> None:
    score = score_case(
        case_id="gmv_no_anomaly",
        ground_truth=_gt(
            "gmv_no_anomaly",
            expected_anomaly=False,
            root_cause_type=None,
            dimension=None,
            element=None,
        ),
        artifacts=PersistedArtifacts(
            agent_run={"run_id": "run-1", "status": "no_anomaly", "metric_id": "gmv"},
            evidences=[_evidence("run-1:E1", {"is_anomaly": False})],
            trace_steps=[{"seq": 1, "node": "parse_question"}],
            sql_audit=[{"guard_status": "passed"}],
            tasks=[],
            report={
                "status": "no_anomaly",
                "evidence_ids": ["run-1:E1"],
                "top_candidate": {"dimension": "channel", "element": "paid_ads"},
                "numeric_claims": [{"name": "contribution_pct", "value": 0.1, "evidence_id": "run-1:E1"}],
            },
        ),
    )
    summary = summarize_scores([score], dangerous_sql_blocked=True)

    assert score["anomaly_ok"] == 0
    assert score["evidence_coverage"] == 0.0
    assert score["report_traceable_ok"] == 0
    assert score["memory_pollution_ok"] == 0
    assert summary["no_anomaly_correct"] is False


def test_no_anomaly_correct_requires_all_no_anomaly_traps_clean() -> None:
    scores = [
        score_case(
            case_id="gmv_no_anomaly",
            ground_truth=_gt("gmv_no_anomaly", expected_anomaly=False, root_cause_type=None, dimension=None, element=None),
            artifacts=_no_anomaly_artifacts("run-1"),
        ),
        score_case(
            case_id="C19_gmv_seasonal_false_positive",
            ground_truth=_gt("C19_gmv_seasonal_false_positive", expected_anomaly=False, root_cause_type=None, dimension=None, element=None),
            artifacts=_no_anomaly_artifacts("run-2"),
        ),
        score_case(
            case_id="C20_cvr_no_anomaly_noise",
            ground_truth=_gt("C20_cvr_no_anomaly_noise", expected_anomaly=False, root_cause_type=None, dimension=None, element=None),
            artifacts=_no_anomaly_artifacts("run-3"),
        ),
        score_case(
            case_id="C22_gmv_borderline",
            ground_truth=_gt("C22_gmv_borderline", expected_anomaly=False, root_cause_type=None, dimension=None, element=None),
            artifacts=_no_anomaly_artifacts("run-4", trace_action="rank_root_causes"),
        ),
    ]

    summary = summarize_scores(scores, dangerous_sql_blocked=True)

    assert scores[-1]["no_anomaly_task_ok"] == 0
    assert summary["no_anomaly_correct"] is False


def test_eval_summary_accepts_p7_thresholds() -> None:
    rows = []
    for index in range(20):
        rows.append(
            {
                "case_id": f"C{index + 1:02d}",
                "intent_ok": 1,
                "anomaly_ok": 1,
                "top1_ok": 1 if index < 16 else 0,
                "top3_ok": 1 if index < 18 else 0,
                "dominant_top1_ok": 1 if index < 16 else 0,
                "root_cause_set_recall": 1.0,
                "root_cause_set_precision": 1.0,
                "weighted_explanation_coverage": 1.0,
                "top3_contains_all_major_causes": 1 if index < 18 else 0,
                "evidence_coverage": 1.0,
                "sql_safe": 1,
                "reflection_repair_ok": 1,
                "report_traceable_ok": 1,
                "memory_pollution_ok": 1,
                "no_anomaly_task_ok": 1,
            }
        )
    rows[4]["case_id"] = "gmv_no_anomaly"
    rows[18]["case_id"] = "C19_gmv_seasonal_false_positive"
    rows[19]["case_id"] = "C20_cvr_no_anomaly_noise"

    summary = summarize_scores(rows, dangerous_sql_blocked=True)

    assert summary["case_total"] == 20
    assert summary["intent_accuracy"] == 1.0
    assert summary["top1_rate"] == 0.8
    assert summary["top3_rate"] == 0.9
    assert summary["no_anomaly_correct"] is True


def test_eval_summary_records_average_tokens_latency_provider_model() -> None:
    rows = []
    for index in range(2):
        rows.append(
            {
                "case_id": f"case_{index}",
                "intent_ok": 1,
                "anomaly_ok": 1,
                "top1_ok": 1,
                "top3_ok": 1,
                "dominant_top1_ok": 1,
                "root_cause_set_recall": 1.0,
                "root_cause_set_precision": 1.0,
                "weighted_explanation_coverage": 1.0,
                "top3_contains_all_major_causes": 1,
                "evidence_coverage": 1.0,
                "sql_safe": 1,
                "reflection_repair_ok": 1,
                "report_traceable_ok": 1,
                "memory_pollution_ok": 1,
                "no_anomaly_task_ok": 1,
                "detail": {
                    "token_count": 10 + index * 2,
                    "latency_ms": 100 + index * 50,
                    "sql_count": 3 + index,
                },
            }
        )

    summary = summarize_scores(rows, dangerous_sql_blocked=True)

    assert summary["avg_tokens_per_case"] == 11.0
    assert summary["avg_latency_ms_per_case"] == 125.0
    assert summary["p95_latency_ms"] == 150.0
    assert summary["p95_sql_count"] == 4.0


def test_memory_retrieval_eval_reports_improvement_and_zero_pollution() -> None:
    enabled = [
        {"case_id": "case_1", "top1_ok": 1, "memory_pollution_ok": 1},
        {"case_id": "case_2", "top1_ok": 1, "memory_pollution_ok": 1},
    ]
    disabled = [
        {"case_id": "case_1", "top1_ok": 0, "memory_pollution_ok": 1},
        {"case_id": "case_2", "top1_ok": 1, "memory_pollution_ok": 1},
    ]

    summary = summarize_memory_retrieval(enabled, disabled)

    assert summary["memory_enabled_top1_rate"] == 1.0
    assert summary["memory_disabled_top1_rate"] == 0.5
    assert summary["memory_hit_improvement"] == 0.5
    assert summary["memory_pollution_ok"] is True


def test_memory_treatment_suite_requires_enabled_lift_and_memory_read_trace() -> None:
    disabled = [
        {
            "case_id": "memory_case",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 0,
            "top3_ok": 0,
            "dominant_top1_ok": 0,
            "root_cause_set_recall": 0.0,
            "root_cause_set_precision": 0.0,
            "weighted_explanation_coverage": 0.0,
            "top3_contains_all_major_causes": 0,
            "evidence_coverage": 1.0,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "report_traceable_ok": 1,
            "memory_pollution_ok": 1,
            "no_anomaly_task_ok": 1,
            "detail": {"scenario_family": "memory_treatment"},
        }
    ]
    enabled = [
        {
            **disabled[0],
            "top1_ok": 1,
            "top3_ok": 1,
            "dominant_top1_ok": 1,
            "root_cause_set_recall": 1.0,
            "root_cause_set_precision": 1.0,
            "weighted_explanation_coverage": 1.0,
            "top3_contains_all_major_causes": 1,
            "detail": {"scenario_family": "memory_treatment", "tool_sequence": ["read_priors"]},
        }
    ]

    summary = _eval_summary(
        case_scores=disabled,
        memory_case_scores=enabled,
        settings=Settings(
            db_dsn="mysql+pymysql://app:app@localhost/db",
            readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
            llm_provider="openai",
            llm_model="gpt-test",
            llm_api_key="key",
        ),
        eval_suite="memory-treatment",
        configured_case_total=1,
        complete=True,
    )

    assert summary["memory_treatment_gate"] is True
    assert summary["memory_disabled_top1_rate"] == 0.0
    assert summary["memory_enabled_top1_rate"] == 1.0
    assert summary["thresholds_met"] is True


def test_memory_treatment_suite_rejects_enabled_lift_without_memory_read_trace() -> None:
    disabled = [
        {
            "case_id": "memory_case",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 0,
            "top3_ok": 0,
            "dominant_top1_ok": 0,
            "root_cause_set_recall": 0.0,
            "root_cause_set_precision": 0.0,
            "weighted_explanation_coverage": 0.0,
            "top3_contains_all_major_causes": 0,
            "evidence_coverage": 1.0,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "report_traceable_ok": 1,
            "memory_pollution_ok": 1,
            "no_anomaly_task_ok": 1,
            "detail": {"scenario_family": "memory_treatment"},
        }
    ]
    enabled = [
        {
            **disabled[0],
            "top1_ok": 1,
            "top3_ok": 1,
            "dominant_top1_ok": 1,
            "root_cause_set_recall": 1.0,
            "root_cause_set_precision": 1.0,
            "weighted_explanation_coverage": 1.0,
            "top3_contains_all_major_causes": 1,
            "detail": {"scenario_family": "memory_treatment", "tool_sequence": ["fetch_related_signal"]},
        }
    ]

    summary = _eval_summary(
        case_scores=disabled,
        memory_case_scores=enabled,
        settings=Settings(
            db_dsn="mysql+pymysql://app:app@localhost/db",
            readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
            llm_provider="openai",
            llm_model="gpt-test",
            llm_api_key="key",
        ),
        eval_suite="memory-treatment",
        configured_case_total=1,
        complete=True,
    )

    assert summary["memory_treatment_gate"] is False
    assert summary["thresholds_met"] is False


def test_memory_treatment_suite_rejects_disabled_side_bad_evidence_or_pollution() -> None:
    disabled = [
        {
            "case_id": "memory_case",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 0,
            "top3_ok": 0,
            "dominant_top1_ok": 0,
            "root_cause_set_recall": 0.0,
            "root_cause_set_precision": 0.0,
            "weighted_explanation_coverage": 0.0,
            "top3_contains_all_major_causes": 0,
            "evidence_coverage": 0.0,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "report_traceable_ok": 1,
            "memory_pollution_ok": 0,
            "no_anomaly_task_ok": 1,
            "detail": {"scenario_family": "memory_treatment"},
        }
    ]
    enabled = [
        {
            **disabled[0],
            "top1_ok": 1,
            "top3_ok": 1,
            "dominant_top1_ok": 1,
            "root_cause_set_recall": 1.0,
            "root_cause_set_precision": 1.0,
            "weighted_explanation_coverage": 1.0,
            "top3_contains_all_major_causes": 1,
            "evidence_coverage": 1.0,
            "memory_pollution_ok": 1,
            "detail": {"scenario_family": "memory_treatment", "tool_sequence": ["read_priors"]},
        }
    ]

    summary = _eval_summary(
        case_scores=disabled,
        memory_case_scores=enabled,
        settings=Settings(
            db_dsn="mysql+pymysql://app:app@localhost/db",
            readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
            llm_provider="openai",
            llm_model="gpt-test",
            llm_api_key="key",
        ),
        eval_suite="memory-treatment",
        configured_case_total=1,
        complete=True,
    )

    assert summary["memory_treatment_gate"] is False
    assert summary["thresholds_met"] is False


def test_memory_treatment_suite_rejects_partial_enabled_success() -> None:
    disabled = [
        {
            "case_id": "memory_case_1",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 0,
            "top3_ok": 0,
            "dominant_top1_ok": 0,
            "root_cause_set_recall": 0.0,
            "root_cause_set_precision": 0.0,
            "weighted_explanation_coverage": 0.0,
            "top3_contains_all_major_causes": 0,
            "evidence_coverage": 1.0,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "report_traceable_ok": 1,
            "memory_pollution_ok": 1,
            "no_anomaly_task_ok": 1,
            "detail": {"scenario_family": "memory_treatment"},
        },
        {
            "case_id": "memory_case_2",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 0,
            "top3_ok": 0,
            "dominant_top1_ok": 0,
            "root_cause_set_recall": 0.0,
            "root_cause_set_precision": 0.0,
            "weighted_explanation_coverage": 0.0,
            "top3_contains_all_major_causes": 0,
            "evidence_coverage": 1.0,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "report_traceable_ok": 1,
            "memory_pollution_ok": 1,
            "no_anomaly_task_ok": 1,
            "detail": {"scenario_family": "memory_treatment"},
        },
    ]
    enabled = [
        {
            **disabled[0],
            "top1_ok": 1,
            "top3_ok": 1,
            "dominant_top1_ok": 1,
            "root_cause_set_recall": 1.0,
            "root_cause_set_precision": 1.0,
            "weighted_explanation_coverage": 1.0,
            "top3_contains_all_major_causes": 1,
            "detail": {"scenario_family": "memory_treatment", "tool_sequence": ["read_priors"]},
        },
        {
            **disabled[1],
            "detail": {"scenario_family": "memory_treatment", "tool_sequence": ["read_priors"]},
        },
    ]

    summary = _eval_summary(
        case_scores=disabled,
        memory_case_scores=enabled,
        settings=Settings(
            db_dsn="mysql+pymysql://app:app@localhost/db",
            readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
            llm_provider="openai",
            llm_model="gpt-test",
            llm_api_key="key",
        ),
        eval_suite="memory-treatment",
        configured_case_total=2,
        complete=True,
    )

    assert summary["memory_enabled_top1_rate"] == 0.5
    assert summary["memory_disabled_top1_rate"] == 0.0
    assert summary["memory_treatment_gate"] is False
    assert summary["thresholds_met"] is False


def test_eval_on_case_complete_callback_fires_per_case(tmp_path: Path) -> None:
    completed: list[tuple[str, str]] = []

    def callback(score: dict[str, Any]) -> None:
        completed.append((str(score.get("phase") or "baseline"), score["case_id"]))

    run_eval(
        repository=_EvalRepository(),
        rca_runner=_fake_runner,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
        on_case_complete=callback,
    )

    assert completed == [
        ("memory", "gmv_paid_ads_drop"),
        ("memory", "gmv_no_anomaly"),
        ("baseline", "gmv_paid_ads_drop"),
        ("baseline", "gmv_no_anomaly"),
    ]


def test_eval_on_case_complete_writes_per_case_json_files(tmp_path: Path) -> None:
    run_eval(
        repository=_EvalRepository(),
        rca_runner=_fake_runner,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
        on_case_complete=lambda score: None,
    )

    cases_dir = tmp_path / "eval-1" / "cases"
    memory_cases_dir = tmp_path / "eval-1" / "memory_cases"
    assert (memory_cases_dir / "gmv_paid_ads_drop.json").exists()
    assert (memory_cases_dir / "gmv_no_anomaly.json").exists()
    assert (cases_dir / "gmv_paid_ads_drop.json").exists()
    assert (cases_dir / "gmv_no_anomaly.json").exists()
    payload = json.loads((cases_dir / "gmv_paid_ads_drop.json").read_text())
    assert payload["case_id"] == "gmv_paid_ads_drop"
    assert "trace_step_count" in payload["detail"]


def test_eval_on_case_complete_none_is_backward_compatible(tmp_path: Path) -> None:
    output = run_eval(
        repository=_EvalRepository(),
        rca_runner=_fake_runner,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
    )

    assert len(output["cases"]) == 2
    cases_dir = tmp_path / "eval-1" / "cases"
    assert not cases_dir.exists()


def test_eval_case_detail_includes_trace_step_count(tmp_path: Path) -> None:
    output = run_eval(
        repository=_EvalRepository(),
        rca_runner=_fake_runner,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
    )

    for case_score in output["cases"]:
        assert "trace_step_count" in case_score["detail"]
        assert isinstance(case_score["detail"]["trace_step_count"], int)


def test_eval_json_and_markdown_outputs_exist(tmp_path: Path) -> None:
    run_eval(repository=_EvalRepository(), rca_runner=_fake_runner, cases_path=_cases_file(tmp_path), output_dir=tmp_path, eval_id="eval-1")

    assert (tmp_path / "eval-1.json").exists()
    assert (tmp_path / "eval-1.md").exists()


def test_eval_retries_transient_llm_errors_with_same_case(tmp_path: Path) -> None:
    repo = _EvalRepository()
    calls: list[str] = []
    failed_disabled_once = False

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal failed_disabled_once
        run_id = kwargs["run_id"]
        calls.append(run_id)
        if not kwargs["settings"].memory_enabled and not failed_disabled_once:
            failed_disabled_once = True
            return {"run_id": run_id, "status": "failed", "error_code": "rate_limit_exceeded"}
        case_id = _case_id_from_run_id(run_id)
        repo.persist_case(run_id, case_id)
        return {"run_id": run_id, "status": repo.agent_runs[run_id]["status"], "error_code": None}

    output = run_eval(
        repository=repo,
        rca_runner=runner,
        cases_path=_cases_file(tmp_path),
        output_dir=tmp_path,
        eval_id="eval-1",
        settings=Settings(
            db_dsn="mysql+pymysql://app:app@localhost/db",
            readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
            llm_provider="openai",
            llm_model="gpt-test",
            llm_api_key="key",
            eval_llm_retry_seconds=0,
        ),
    )

    assert calls[2:4] == ["eval-1-gmv_paid_ads_drop", "eval-1-gmv_paid_ads_drop-r2"]
    assert output["cases"][0]["detail"]["eval_attempts"] == 2
    assert output["summary"]["thresholds_met"] is True


def test_eval_fails_fast_on_system_table_write_failure_after_repository_retry_budget(tmp_path: Path) -> None:
    repo = _EvalRepository()
    calls: list[str] = []

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        run_id = kwargs["run_id"]
        calls.append(run_id)
        if not kwargs["settings"].memory_enabled:
            return {"run_id": run_id, "status": "failed", "error_code": "SYSTEM_TABLE_WRITE_FAILED"}
        case_id = _case_id_from_run_id(run_id)
        repo.persist_case(run_id, case_id)
        return {"run_id": run_id, "status": repo.agent_runs[run_id]["status"], "error_code": None}

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
            settings=Settings(
                db_dsn="mysql+pymysql://app:app@localhost/db",
                readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
                llm_provider="openai",
                llm_model="gpt-test",
                llm_api_key="key",
                eval_llm_retry_seconds=0,
            ),
        )

    assert exc_info.value.code == "SYSTEM_TABLE_WRITE_FAILED"
    assert calls[2] == "eval-1-gmv_paid_ads_drop"
    assert "eval-1-gmv_paid_ads_drop-r2" not in calls
    assert repo.get_agent_run("eval-1-gmv_paid_ads_drop") is None
    assert repo.case_results == []
    assert repo.eval_runs == []
    assert not (tmp_path / "eval-1.json").exists()


def test_eval_fails_fast_when_case_result_upsert_fails(tmp_path: Path) -> None:
    repo = _EvalRepository()

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        run_id = kwargs["run_id"]
        case_id = _case_id_from_run_id(run_id)
        repo.persist_case(run_id, case_id)
        return {"run_id": run_id, "status": repo.agent_runs[run_id]["status"], "error_code": None}

    def fail_upsert(row: dict[str, Any]) -> None:
        raise RuntimeError("SYSTEM_TABLE_WRITE_FAILED: duplicate key")

    repo.upsert_eval_case_result = fail_upsert  # type: ignore[method-assign]

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
            settings=Settings(
                db_dsn="mysql+pymysql://app:app@localhost/db",
                readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
                llm_provider="openai",
                llm_model="gpt-test",
                llm_api_key="key",
                eval_llm_retry_seconds=0,
            ),
        )

    assert exc_info.value.code == "SYSTEM_TABLE_WRITE_FAILED"
    assert repo.case_results == []
    assert not (tmp_path / "eval-1.json").exists()


def test_eval_fails_fast_on_failed_status_even_without_error_code(tmp_path: Path) -> None:
    repo = _EvalRepository()

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        return {"run_id": kwargs["run_id"], "status": "failed", "error_code": None}

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
            settings=Settings(
                db_dsn="mysql+pymysql://app:app@localhost/db",
                readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
                llm_provider="openai",
                llm_model="gpt-test",
                llm_api_key="key",
                eval_llm_retry_seconds=0,
            ),
        )

    assert exc_info.value.code == "EVAL_RCA_RUN_FAILED"
    assert repo.case_results == []
    assert repo.eval_runs == []


def test_eval_rejects_runner_result_without_success_status(tmp_path: Path) -> None:
    repo = _EvalRepository()

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        return {"run_id": kwargs["run_id"], "error_code": None}

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
            settings=Settings(
                db_dsn="mysql+pymysql://app:app@localhost/db",
                readonly_db_dsn="mysql+pymysql://reader:reader@localhost/db",
                llm_provider="openai",
                llm_model="gpt-test",
                llm_api_key="key",
                eval_llm_retry_seconds=0,
            ),
        )

    assert exc_info.value.code == "EVAL_RCA_RUN_STATUS_INVALID"
    assert repo.case_results == []
    assert repo.eval_runs == []


def test_eval_rejects_missing_persisted_run_before_scoring(tmp_path: Path) -> None:
    repo = _EvalRepository()

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        return {"run_id": kwargs["run_id"], "status": "succeeded", "error_code": None}

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
        )

    assert exc_info.value.code == "EVAL_RCA_RUN_MISSING"
    assert repo.case_results == []
    assert repo.eval_runs == []


def test_eval_rejects_nonterminal_persisted_run_before_scoring(tmp_path: Path) -> None:
    repo = _EvalRepository()

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        run_id = kwargs["run_id"]
        repo.agent_runs[run_id] = {"run_id": run_id, "status": "running", "metric_id": "gmv"}
        return {"run_id": run_id, "status": "succeeded", "error_code": None}

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
        )

    assert exc_info.value.code == "EVAL_RCA_RUN_STATUS_INVALID"
    assert repo.case_results == []
    assert repo.eval_runs == []


def test_eval_preserves_persisted_failed_run_code_before_scoring(tmp_path: Path) -> None:
    repo = _EvalRepository()

    def runner(question: str, **kwargs: Any) -> dict[str, Any]:
        run_id = kwargs["run_id"]
        repo.agent_runs[run_id] = {
            "run_id": run_id,
            "status": "failed",
            "error_code": "SYSTEM_TABLE_WRITE_FAILED",
            "metric_id": "gmv",
        }
        return {"run_id": run_id, "status": "succeeded", "error_code": None}

    with pytest.raises(EvalRuntimeError) as exc_info:
        run_eval(
            repository=repo,
            rca_runner=runner,
            cases_path=_cases_file(tmp_path),
            output_dir=tmp_path,
            eval_id="eval-1",
        )

    assert exc_info.value.code == "SYSTEM_TABLE_WRITE_FAILED"
    assert repo.case_results == []
    assert repo.eval_runs == []


def test_runtime_code_outside_seed_eval_tests_does_not_read_anomaly_ground_truth() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "metric_rca").rglob("*.py"):
        relative = str(path.relative_to(root))
        if (
            relative.startswith("metric_rca/evals/")
            or relative == "metric_rca/data/seed_data.py"
            or relative == "metric_rca/repositories/metric_repository.py"
        ):
            continue
        if "anomaly_ground_truth" in path.read_text():
            offenders.append(relative)

    assert offenders == []


def test_make_eval_no_longer_not_implemented() -> None:
    source = Path("metric_rca/evals/runner.py").read_text()

    assert "NOT IMPLEMENTED" not in source


def test_eval_runner_uses_public_repository_ground_truth_reader() -> None:
    source = Path("metric_rca/evals/runner.py").read_text()

    assert "metric_rca.evals.repository" not in source
    assert "read_ground_truth_cases" not in source


def test_deprecated_eval_repository_helper_fails_fast_without_private_audit_access() -> None:
    source = Path("metric_rca/evals/repository.py").read_text()

    assert "_audit_engine" not in source
    assert "SQLAlchemyError" not in source
    assert "EVAL_GROUND_TRUTH_MISSING" in source


def test_only_eval_runner_uses_ground_truth_repository_reader() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "metric_rca").rglob("*.py"):
        relative = str(path.relative_to(root))
        if relative in {"metric_rca/evals/runner.py", "metric_rca/repositories/metric_repository.py"}:
            continue
        if ".get_ground_truth_cases(" in path.read_text():
            offenders.append(relative)

    assert offenders == []


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert all(isinstance(row, dict) for row in rows)
    return rows


def _case_id_from_run_id(run_id: str) -> str:
    value = run_id
    if value.endswith("-r2") or value.endswith("-r3"):
        value = value.rsplit("-r", maxsplit=1)[0]
    for prefix in ("eval-1-mem-", "eval-1-"):
        if value.startswith(prefix):
            return value.removeprefix(prefix)
    return value


def _fake_runner(question: str, **kwargs: Any) -> dict[str, Any]:
    repo: _EvalRepository = kwargs["repository"]
    case_id = _case_id_from_run_id(kwargs["run_id"])
    repo.runner_calls.append((case_id, kwargs["settings"].memory_enabled))
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
    root_causes: tuple[RootCauseTruth, ...] = (),
) -> GroundTruth:
    return GroundTruth(
        case_id=case_id,
        business_date=date(2026, 6, 5),
        metric_id="gmv",
        expected_anomaly=expected_anomaly,
        root_cause_type=root_cause_type,
        dimension=dimension,
        element=element,
        root_causes=root_causes,
    )


def _candidate(
    *,
    root_cause_type: str = "campaign_traffic_drop",
    dimension: str = "channel",
    element: str = "paid_ads",
    contribution_pct: float = 0.9,
    evidence_ids: list[str] | None = None,
    dimension_elements: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    candidate = {
        "root_cause_type": root_cause_type,
        "dimension": dimension,
        "element": element,
        "contribution_pct": contribution_pct,
        "signal_severity": 0.8,
        "evidence_support": 1.0,
        "eng_confidence": 0.85,
        "verdict": "confirmed",
        "evidence_ids": evidence_ids or ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4", "run-1:E_rank"],
    }
    if dimension_elements is not None:
        candidate["dimension_elements"] = dimension_elements
        candidate["explanatory_power"] = 0.91
        candidate["surprise_js"] = 0.12
    return candidate


def _artifacts(run_id: str, *, selected: dict[str, Any], candidates: list[dict[str, Any]] | None = None) -> PersistedArtifacts:
    evidence_ids = [str(value) for value in selected.get("evidence_ids", [])]
    if evidence_ids and all(value.startswith("run-1:") for value in evidence_ids):
        evidence_ids = [f"{run_id}:{value.split(':', maxsplit=1)[1]}" for value in evidence_ids]
    run_selected = {**selected, "evidence_ids": evidence_ids}
    run_candidates = [
        {**candidate, "evidence_ids": _run_evidence_ids(candidate, run_id)}
        for candidate in (candidates or [selected])
    ]
    e4_summary = _e4_summary(run_selected, candidates=run_candidates)
    if run_selected.get("explanatory_power") is not None:
        e4_summary["ranker"] = "adtributor_internal"
    evidences = [
        _evidence(f"{run_id}:E1", {"is_anomaly": True}),
        _evidence(f"{run_id}:E2", {"candidates": [run_selected]}),
        _evidence(f"{run_id}:E3", {"signal_type": "campaign"}),
        _evidence(f"{run_id}:E4", e4_summary),
        _evidence(f"{run_id}:E_rank", e4_summary),
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


def _e4_summary(candidate: dict[str, Any], *, candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    candidate_list = candidates or [candidate]
    return {
        "contribution_set": {
            "selected_candidate": candidate,
            "candidates": candidate_list,
            "evidence_ids": list(candidate.get("evidence_ids", [])),
            "factor_graph": {},
            "selection_evidence_id": None,
        },
        "selected_candidate": candidate,
        "candidates": candidate_list,
    }


def _run_evidence_ids(candidate: dict[str, Any], run_id: str) -> list[str]:
    evidence_ids = [str(value) for value in candidate.get("evidence_ids", [])]
    if evidence_ids and all(value.startswith("run-1:") for value in evidence_ids):
        return [f"{run_id}:{value.split(':', maxsplit=1)[1]}" for value in evidence_ids]
    return evidence_ids


def _no_anomaly_artifacts(
    run_id: str,
    *,
    trace_node: str = "parse_question",
    trace_action: str | None = None,
) -> PersistedArtifacts:
    return PersistedArtifacts(
        agent_run={"run_id": run_id, "status": "no_anomaly", "metric_id": "gmv"},
        evidences=[_evidence(f"{run_id}:E1", {"is_anomaly": False})],
        trace_steps=[{"seq": 1, "node": trace_node, "action": trace_action}],
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


def _memory_record(
    *,
    memory_id: str,
    layer: str,
    mem_key: str,
    payload: dict[str, Any],
    confidence: float = 0.8,
    source: str = "reflection_verified",
) -> dict[str, Any]:
    return {
        "memory_id": memory_id,
        "layer": layer,
        "mem_key": mem_key,
        "payload": payload,
        "confidence": confidence,
        "source": source,
        "version": 1,
        "ttl_days": 30,
        "created_at": "2026-06-05T00:00:00",
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
        self.runner_calls: list[tuple[str, bool]] = []
        self.agent_runs: dict[str, dict[str, Any]] = {}
        self.evidences: dict[str, list[dict[str, Any]]] = {}
        self.trace_steps: dict[str, list[dict[str, Any]]] = {}
        self.sql_audit: dict[str, list[dict[str, Any]]] = {}
        self.tasks: dict[str, list[dict[str, Any]]] = {}
        self.memory_records: dict[str, list[dict[str, Any]]] = {}
        self.eval_runs: list[dict[str, Any]] = []
        self.eval_run_updates: list[dict[str, Any]] = []
        self.case_results: list[dict[str, Any]] = []
        self.gt_scopes: list[tuple[str | None, str | None]] = []
        self.closed = False

    def get_ground_truth_cases(
        self,
        case_ids: list[str],
        *,
        split: str | None = None,
        profile: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        self.gt_requests.append(case_ids)
        self.gt_scopes.append((split, profile))
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

    def get_memory_records_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return self.memory_records.get(run_id, [])

    def create_eval_run(self, row: dict[str, Any]) -> None:
        self.eval_runs.append(row)

    def upsert_eval_run_summary(self, row: dict[str, Any]) -> None:
        self.eval_run_updates.append(row)

    def create_eval_case_result(self, row: dict[str, Any]) -> None:
        self.case_results.append(row)

    def upsert_eval_case_result(self, row: dict[str, Any]) -> None:
        for index, existing in enumerate(self.case_results):
            if existing["eval_id"] == row["eval_id"] and existing["case_id"] == row["case_id"]:
                self.case_results[index] = row
                return
        self.case_results.append(row)

    def close(self) -> None:
        self.closed = True


class _NoMemoryArtifactRepository(_EvalRepository):
    get_memory_records_for_run = None

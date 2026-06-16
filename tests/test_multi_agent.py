from __future__ import annotations

from datetime import date, datetime
import logging
from types import SimpleNamespace
from typing import Any

from metric_rca.agent.deep_tools import EXPOSED_TOOL_NAMES
from metric_rca.agent.factory import create_metric_rca_agent
from metric_rca.agent.runner import AgentDependencies, RunOrchestrator
from metric_rca.agent.subagents import RunOutcome, route_metric_family
from metric_rca.evals.models import GroundTruth, PersistedArtifacts
from metric_rca.evals.scorer import score_case, summarize_scores
from metric_rca.observability.trace import TraceWriter
from metric_rca.services.metric_contracts import ParsedIntent


def test_single_agent_and_multi_agent_produce_same_score_fields() -> None:
    gt = _gt()
    single = score_case(case_id="gmv_paid_ads_drop", ground_truth=gt, artifacts=_artifacts("run-single"))
    multi = score_case(
        case_id="gmv_paid_ads_drop",
        ground_truth=gt,
        artifacts=_artifacts(
            "run-multi",
            trace_steps=[{"run_id": "run-multi", "seq": 1, "node": "triage", "action": "route_gmv_family"}],
        ),
    )

    assert set(single) == set(multi)
    assert single["multi_agent_path"] == "single_agent"
    assert multi["multi_agent_path"] == "multi_agent:gmv_family"


def test_single_agent_mode_does_not_create_expert_agents() -> None:
    repo = _Repo()
    factory = _Factory(repo)

    bundle = create_metric_rca_agent(
        dependencies=_deps(repo, metric_id="gmv", multi_agent_enabled=False),
        run_id="run-single-factory",
        agent_factory=factory,
    )

    assert bundle.expert_agents == {}
    assert factory.created_names == ["single_agent"]
    assert bundle.agent_for_family(None) is bundle.agent


def test_multi_agent_triage_routes_gmv_family() -> None:
    repo = _Repo()
    factory = _Factory(repo)

    result = RunOrchestrator(
        dependencies=_deps(repo, metric_id="gmv", multi_agent_enabled=True),
        agent_factory=factory,
    ).run("Why did yesterday GMV drop?", run_id="run-gmv")

    assert result["status"] == "succeeded"
    assert factory.invoked == ["gmv_family"]
    assert _triage_actions(repo, "run-gmv") == ["route_gmv_family"]


def test_multi_agent_triage_routes_rate_family() -> None:
    repo = _Repo()
    factory = _Factory(repo, terminal="no_anomaly")

    result = RunOrchestrator(
        dependencies=_deps(repo, metric_id="refund_rate", multi_agent_enabled=True),
        agent_factory=factory,
    ).run("Why did yesterday refund rate increase?", run_id="run-rate")

    assert result["status"] == "no_anomaly"
    assert factory.invoked == ["rate_family"]
    assert _triage_actions(repo, "run-rate") == ["route_rate_family"]


def test_multi_agent_budget_is_shared_across_experts() -> None:
    repo = _Repo()
    factory = _Factory(repo)

    result = RunOrchestrator(
        dependencies=_deps(repo, metric_id="gmv", multi_agent_enabled=True),
        agent_factory=factory,
    ).run("Why did yesterday GMV drop?", run_id="run-budget")

    assert result["status"] == "succeeded"
    assert len(factory.middleware_ids) == 2
    assert len(set(factory.middleware_ids)) == 1
    assert len(factory.guard_context_ids) == 2
    assert len(set(factory.guard_context_ids)) == 1


def test_multi_agent_no_anomaly_contract_preserved() -> None:
    repo = _Repo()
    factory = _Factory(repo, terminal="no_anomaly_with_downstream_trace")

    result = RunOrchestrator(
        dependencies=_deps(repo, metric_id="gmv", multi_agent_enabled=True),
        agent_factory=factory,
    ).run("Why did yesterday GMV drop?", run_id="run-no-anom")

    assert result["status"] == "failed"
    assert result["error_code"] == "NO_ANOMALY_CONTRACT_VIOLATED"
    assert factory.invoked == ["gmv_family"]


def test_multi_agent_reflection_repair_works() -> None:
    repo = _Repo()
    factory = _Factory(repo, terminal="repair_then_success")

    result = RunOrchestrator(
        dependencies=_deps(repo, metric_id="gmv", multi_agent_enabled=True),
        agent_factory=factory,
    ).run("Why did yesterday GMV drop?", run_id="run-repair")

    assert result["status"] == "succeeded"
    assert factory.invoked == ["gmv_family", "gmv_family"]


def test_multi_agent_malformed_run_outcome_warns_and_uses_persisted_artifacts(caplog) -> None:
    repo = _Repo()
    factory = _Factory(repo, terminal="malformed_outcome")
    caplog.set_level(logging.WARNING, logger="metric_rca.agent.runner")

    result = RunOrchestrator(
        dependencies=_deps(repo, metric_id="gmv", multi_agent_enabled=True),
        agent_factory=factory,
    ).run("Why did yesterday GMV drop?", run_id="run-bad-outcome")

    assert result["status"] == "succeeded"
    assert factory.invoked == ["gmv_family"]
    assert "malformed RunOutcome ignored" in caplog.text


def test_eval_summary_counts_multi_agent_path_distribution() -> None:
    scores = [
        {"case_id": "case_1", "multi_agent_path": "single_agent", "intent_ok": 1, "top1_ok": 1, "top3_ok": 1, "anomaly_ok": 1, "evidence_coverage": 1, "sql_safe": 1, "reflection_repair_ok": 1, "memory_pollution_ok": 1, "report_traceable_ok": 1, "no_anomaly_task_ok": 1, "detail": {"token_count": 10, "latency_ms": 100}},
        {"case_id": "case_2", "multi_agent_path": "multi_agent:gmv_family", "intent_ok": 1, "top1_ok": 1, "top3_ok": 1, "anomaly_ok": 1, "evidence_coverage": 1, "sql_safe": 1, "reflection_repair_ok": 1, "memory_pollution_ok": 1, "report_traceable_ok": 1, "no_anomaly_task_ok": 1, "detail": {"token_count": 12, "latency_ms": 120}},
        {"case_id": "case_3", "multi_agent_path": "multi_agent:gmv_family", "intent_ok": 1, "top1_ok": 1, "top3_ok": 1, "anomaly_ok": 1, "evidence_coverage": 1, "sql_safe": 1, "reflection_repair_ok": 1, "memory_pollution_ok": 1, "report_traceable_ok": 1, "no_anomaly_task_ok": 1, "detail": {"token_count": 14, "latency_ms": 140}},
    ]

    summary = summarize_scores(scores, dangerous_sql_blocked=True)

    assert summary["multi_agent_path_distribution"] == {
        "single_agent": 1,
        "multi_agent:gmv_family": 2,
    }


def test_route_metric_family_rejects_unknown_metric() -> None:
    try:
        route_metric_family("campaign_roi")
    except RuntimeError as exc:
        assert str(exc).startswith("METRIC_NOT_FOUND:")
    else:
        raise AssertionError("expected METRIC_NOT_FOUND")


class _Factory:
    def __init__(self, repo: "_Repo", terminal: str = "success") -> None:
        self.repo = repo
        self.terminal = terminal
        self.created_names: list[str] = []
        self.invoked: list[str] = []
        self.middleware_ids: list[int] = []
        self.guard_context_ids: list[int] = []

    def __call__(self, **kwargs: Any) -> "_Agent":
        middleware = kwargs["middleware"][0]
        self.middleware_ids.append(id(middleware))
        self.guard_context_ids.append(id(middleware.context))
        name = str(kwargs.get("name") or "")
        self.created_names.append(name)
        family = "rate_family" if "rate_family" in name else "gmv_family"
        return _Agent(self.repo, family=family, terminal=self.terminal, invocations=self.invoked)


class _Agent:
    nodes = {
        "tools": SimpleNamespace(
            bound=SimpleNamespace(_tools_by_name={name: object() for name in EXPOSED_TOOL_NAMES})
        )
    }

    def __init__(self, repo: "_Repo", *, family: str, terminal: str, invocations: list[str]) -> None:
        self.repo = repo
        self.family = family
        self.terminal = terminal
        self.invocations = invocations

    def invoke(self, payload, **kwargs):
        run_id = kwargs["config"]["configurable"]["thread_id"]
        self.repo.invocations.append(self.family)
        self.invocations.append(self.family)
        if self.terminal == "success":
            self.repo.add_valid_evidences(run_id)
            status = "succeeded"
        elif self.terminal == "no_anomaly":
            self.repo.add_no_anomaly_evidence(run_id)
            status = "no_anomaly"
        elif self.terminal == "no_anomaly_with_downstream_trace":
            self.repo.add_no_anomaly_evidence(run_id)
            self.repo.create_trace_step(
                {
                    "run_id": run_id,
                    "seq": 20,
                    "node": "execute_tool",
                    "action": "drilldown_dimension",
                    "input_summary": {},
                    "output_summary": {},
                    "error_code": None,
                    "latency_ms": 0,
                }
            )
            status = "no_anomaly"
        elif self.terminal == "repair_then_success" and self.repo.invocations.count(self.family) == 1:
            self.repo.add_valid_evidences(run_id, missing_e3=True)
            status = "succeeded"
        elif self.terminal == "malformed_outcome":
            self.repo.add_valid_evidences(run_id)
            return {"structured_response": {"status": "succeeded"}}
        else:
            self.repo.add_valid_evidences(run_id)
            status = "succeeded"
        return {
            "structured_response": RunOutcome(
                status=status,
                metric_id=self.repo.runs[run_id]["metric_id"],
                dimension="channel",
                element="paid_ads",
                root_cause_type="campaign_traffic_drop" if status == "succeeded" else None,
                verdict="confirmed" if status == "succeeded" else None,
                evidence_ids=list(self.repo.evidences),
                reflection_notes=[],
            )
        }


class _MetricService:
    def __init__(self, metric_id: str) -> None:
        self.metric_id = metric_id

    def parse_question(self, question: str, *, business_today) -> ParsedIntent:
        family = "refund_rate_increase" if self.metric_id == "refund_rate" else "gmv_drop"
        return ParsedIntent(
            metric_id=self.metric_id,
            target_date=date(2026, 6, 5),
            question_family=family,
            analysis_strategy="standard",
        )


def _deps(repo: "_Repo", *, metric_id: str, multi_agent_enabled: bool) -> AgentDependencies:
    settings = SimpleNamespace(
        llm_provider="openai",
        llm_model="gpt-test",
        llm_api_key="key",
        business_today=date(2026, 6, 6),
        target_date=date(2026, 6, 5),
        max_steps=8,
        max_query=12,
        max_drilldown_depth=3,
        max_repair=1,
        memory_enabled=False,
        memory_required=False,
        memory_write_on_finalize=True,
        multi_agent_enabled=multi_agent_enabled,
    )
    return AgentDependencies(
        settings=settings,
        repository=repo,
        metric_service=_MetricService(metric_id),
        renderer=SimpleNamespace(),
        trace_writer=TraceWriter(repo),
        memory_repo=None,
    )


def _triage_actions(repo: "_Repo", run_id: str) -> list[str]:
    return [
        str(row.get("action"))
        for row in repo.get_trace_steps(run_id)
        if row.get("node") == "triage"
    ]


class _Repo:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.evidences: dict[str, dict[str, Any]] = {}
        self.trace_steps: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []
        self.invocations: list[str] = []

    def create_agent_run(self, row: dict[str, Any]) -> None:
        self.runs[row["run_id"]] = dict(row)

    def update_agent_run_context(self, *, run_id: str, metric_id: str, target_date) -> None:
        self.runs[run_id]["metric_id"] = metric_id
        self.runs[run_id]["target_date"] = target_date

    def finish_agent_run(self, *, run_id: str, status: str, error_code: str | None, finished_at, total_tokens=None, total_latency_ms=None, token_breakdown=None) -> None:
        self.runs[run_id]["status"] = status
        self.runs[run_id]["error_code"] = error_code
        self.runs[run_id]["finished_at"] = finished_at
        self.runs[run_id]["total_tokens"] = total_tokens
        self.runs[run_id]["total_latency_ms"] = total_latency_ms
        self.runs[run_id]["token_breakdown"] = token_breakdown

    def create_trace_step(self, row: dict[str, Any]) -> None:
        self.trace_steps.append(dict(row))

    def get_trace_steps(self, run_id: str) -> list[dict[str, Any]]:
        return [row for row in self.trace_steps if row.get("run_id") == run_id]

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, Any] | None:
        return self.evidences.get(evidence_id)

    def get_evidences(self, run_id: str) -> list[dict[str, Any]]:
        return [row for row in self.evidences.values() if row["run_id"] == run_id]

    def get_operation_tasks(self, run_id: str) -> list[dict[str, Any]]:
        return list(self.tasks)

    def create_operation_task(self, row: dict[str, Any]) -> None:
        self.tasks.append(dict(row))

    def add_no_anomaly_evidence(self, run_id: str) -> None:
        if run_id in self.runs:
            self.update_agent_run_context(run_id=run_id, metric_id=self.runs[run_id]["metric_id"], target_date=date(2026, 6, 5))
        self.add_evidence(run_id, "E1", {"metric_id": self.runs[run_id]["metric_id"], "target_date": "2026-06-05", "is_anomaly": False})

    def add_valid_evidences(self, run_id: str, *, missing_e3: bool = False) -> None:
        if run_id in self.runs:
            self.update_agent_run_context(run_id=run_id, metric_id=self.runs[run_id]["metric_id"], target_date=date(2026, 6, 5))
        candidate = _candidate(run_id, missing_e3=missing_e3)
        self.add_evidence(run_id, "E1", {"metric_id": self.runs[run_id]["metric_id"], "target_date": "2026-06-05", "is_anomaly": True})
        self.add_evidence(run_id, "E2", {"metric_id": self.runs[run_id]["metric_id"], "target_date": "2026-06-05"})
        if not missing_e3:
            self.add_evidence(run_id, "E3", {"metric_id": self.runs[run_id]["metric_id"], "target_date": "2026-06-05", "signal_type": "campaign", "signal_metric_id": "gmv", "dimension": "channel", "element": "paid_ads"})
        self.add_evidence(run_id, "E4", {"metric_id": self.runs[run_id]["metric_id"], "target_date": "2026-06-05", "selected_candidate": candidate, "candidates": [candidate]})
        if not missing_e3:
            self.add_evidence(run_id, "E_rank", {"metric_id": self.runs[run_id]["metric_id"], "target_date": "2026-06-05", "selected_candidate": candidate, "candidates": [candidate]})

    def add_evidence(self, run_id: str, alias: str, result_summary: dict[str, Any]) -> None:
        evidence_id = f"{run_id}:{alias}"
        self.evidences[evidence_id] = {
            "evidence_id": evidence_id,
            "run_id": run_id,
            "query_spec": {
                "metric_id": self.runs[run_id]["metric_id"],
                "time_range": {"start_date": "2026-06-05", "end_date": "2026-06-05", "tz": "Asia/Tokyo"},
                "group_by": [],
                "filters": {},
                "limit": 1000,
                "purpose": "current",
                "signal_type": "metric",
            },
            "sql_text": "SELECT 1 FROM fact_order WHERE business_date = :target_date LIMIT 1",
            "sql_hash": "0" * 64,
            "guard_status": "passed",
            "result_summary": result_summary,
            "data_source": "fact_order",
            "created_at": datetime(2026, 6, 6),
        }


def _gt() -> GroundTruth:
    return GroundTruth(
        case_id="gmv_paid_ads_drop",
        business_date=date(2026, 6, 5),
        metric_id="gmv",
        expected_anomaly=True,
        root_cause_type="campaign_traffic_drop",
        dimension="channel",
        element="paid_ads",
    )


def _artifacts(run_id: str, *, trace_steps: list[dict[str, Any]] | None = None) -> PersistedArtifacts:
    candidate = _candidate(run_id)
    evidences = [
        _evidence(run_id, "E1", {"is_anomaly": True}),
        _evidence(run_id, "E2", {}),
        _evidence(run_id, "E3", {}),
        _evidence(run_id, "E4", {"selected_candidate": candidate, "candidates": [candidate]}),
        _evidence(run_id, "E_rank", {"selected_candidate": candidate, "candidates": [candidate]}),
    ]
    return PersistedArtifacts(
        agent_run={"run_id": run_id, "status": "succeeded", "metric_id": "gmv", "target_date": date(2026, 6, 5)},
        evidences=evidences,
        trace_steps=trace_steps or [],
        sql_audit=[{"guard_status": "passed"}],
        tasks=[{"task_id": f"{run_id}:task"}],
        report={
            "status": "succeeded",
            "top_candidate": candidate,
            "numeric_claims": [{"name": "contribution_pct", "value": 0.9, "evidence_id": f"{run_id}:E4"}],
        },
    )


def _candidate(run_id: str, *, missing_e3: bool = False) -> dict[str, Any]:
    evidence_ids = [f"{run_id}:E1", f"{run_id}:E2", f"{run_id}:E4", f"{run_id}:E_rank"] if missing_e3 else [
        f"{run_id}:E1",
        f"{run_id}:E2",
        f"{run_id}:E3",
        f"{run_id}:E4",
        f"{run_id}:E_rank",
    ]
    return {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": "paid_ads",
        "contribution_pct": 0.9,
        "signal_severity": 0.9,
        "evidence_support": 1.0,
        "reflection_factor": 1.0,
        "eng_confidence": 0.9,
        "verdict": "confirmed",
        "evidence_ids": evidence_ids,
    }


def _evidence(run_id: str, alias: str, summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": f"{run_id}:{alias}",
        "run_id": run_id,
        "guard_status": "passed",
        "result_summary": summary,
    }

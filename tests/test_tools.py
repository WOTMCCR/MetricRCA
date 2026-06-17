from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from metric_rca.agent.deep_tools import build_metric_rca_tools
from metric_rca.agent.tools.calculate_contribution import calculate_contribution
from metric_rca.agent.tools.detect_anomaly import detect_anomaly
from metric_rca.agent.tools.drilldown_dimension import drilldown_dimension
from metric_rca.agent.tools.fetch_related_signal import fetch_related_signal
from metric_rca.agent.tools.schemas import (
    CalculateContributionArgs,
    DetectAnomalyArgs,
    DrilldownDimensionArgs,
    FetchRelatedSignalArgs,
)
from metric_rca.config.settings import Settings
from metric_rca.domain.models import MetricDefinition, SQLPlan


class StaticMetricService:
    def __init__(self) -> None:
        self.definitions = {
            metric_id: MetricDefinition(
                metric_id=metric_id,
                display_name=metric_id,
                formula="test",
                metric_family=(
                    "rate_family"
                    if metric_id in {"pay_cvr", "refund_rate", "stockout_rate", "complaint_rate"}
                    else "gmv_family"
                ),
                higher_is_better=metric_id not in {"refund_rate", "stockout_rate", "complaint_rate"},
                allowed_dimensions=["channel", "category", "device", "product", "warehouse"],
                source_table=source_table,
            )
            for metric_id, source_table in {
                "gmv": "fact_order",
                "net_gmv": "fact_order",
                "pay_cvr": "fact_traffic",
                "refund_rate": "fact_order",
                "stockout_rate": "fact_inventory",
                "complaint_rate": "fact_customer_ticket",
                "uv": "fact_traffic",
                "aov": "fact_order",
            }.items()
        }

    def get_metric_definition(self, metric_id: str) -> MetricDefinition:
        return self.definitions[metric_id]


class SpyRepository:
    def __init__(self) -> None:
        self.executed: list[SQLPlan] = []
        self.evidence_rows: list[dict[str, Any]] = []
        self.runs = {
            "run-1": {
                "run_id": "run-1",
                "status": "running",
                "metric_id": "gmv",
                "target_date": date(2026, 6, 5),
            }
        }
        self.persisted_evidence: dict[str, dict[str, Any]] = {
            "run-1:E1": {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            "run-1:E2": {"evidence_id": "run-1:E2", "run_id": "run-1", "guard_status": "passed"},
            "run-1:E3": {"evidence_id": "run-1:E3", "run_id": "run-1", "guard_status": "passed"},
        }

    def seed_e2_family(self, alias: str) -> None:
        self.persisted_evidence[f"run-1:{alias}"] = {
            "evidence_id": f"run-1:{alias}",
            "run_id": "run-1",
            "guard_status": "passed",
        }

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, Any] | None:
        row = self.persisted_evidence.get(evidence_id)
        if row and row["run_id"] == run_id:
            return row
        return None

    def get_evidences(self, run_id: str) -> list[dict[str, Any]]:
        return [row for row in self.persisted_evidence.values() if row.get("run_id") == run_id]

    def execute_plan(self, plan: SQLPlan, *, run_id: str):
        assert plan.guard_status == "passed"
        self.executed.append(plan)
        sql = plan.sql
        params = plan.params
        if "fact_campaign" in sql and "business_date IN" in sql:
            rows = [
                {"business_date": date(2026, 5, 29), "metric_value": 1000.0},
                {"business_date": date(2026, 5, 22), "metric_value": 1000.0},
                {"business_date": date(2026, 5, 15), "metric_value": 1000.0},
                {"business_date": date(2026, 5, 8), "metric_value": 1000.0},
            ]
        elif "fact_campaign" in sql:
            rows = [{"metric_value": 250.0}]
        elif "GROUP BY fact_traffic.channel" in sql and "business_date IN" in sql:
            rows = [
                {"business_date": date(2026, 5, 29), "channel": "paid_ads", "metric_value": 0.10},
                {"business_date": date(2026, 5, 22), "channel": "paid_ads", "metric_value": 0.10},
                {"business_date": date(2026, 5, 15), "channel": "paid_ads", "metric_value": 0.10},
                {"business_date": date(2026, 5, 8), "channel": "paid_ads", "metric_value": 0.10},
                {"business_date": date(2026, 5, 29), "channel": "organic", "metric_value": 0.10},
                {"business_date": date(2026, 5, 22), "channel": "organic", "metric_value": 0.10},
                {"business_date": date(2026, 5, 15), "channel": "organic", "metric_value": 0.10},
                {"business_date": date(2026, 5, 8), "channel": "organic", "metric_value": 0.10},
            ]
        elif "GROUP BY fact_traffic.channel" in sql:
            rows = [
                {"channel": "paid_ads", "metric_value": 0.02},
                {"channel": "organic", "metric_value": 0.09},
            ]
        elif "SUM(fact_traffic.pay_user_cnt)" in sql and "business_date IN" in sql:
            rows = [
                {"business_date": date(2026, 5, 29), "metric_value": 0.10},
                {"business_date": date(2026, 5, 22), "metric_value": 0.10},
                {"business_date": date(2026, 5, 15), "metric_value": 0.10},
                {"business_date": date(2026, 5, 8), "metric_value": 0.10},
            ]
        elif "SUM(fact_traffic.pay_user_cnt)" in sql:
            rows = [{"metric_value": 0.10}]
        elif "SUM(fact_traffic.uv)" in sql and "business_date IN" in sql:
            rows = [
                {"business_date": date(2026, 5, 29), "metric_value": 200.0},
                {"business_date": date(2026, 5, 22), "metric_value": 200.0},
                {"business_date": date(2026, 5, 15), "metric_value": 200.0},
                {"business_date": date(2026, 5, 8), "metric_value": 200.0},
            ]
        elif "SUM(fact_traffic.uv)" in sql:
            rows = [{"metric_value": 100.0}]
        elif "business_date IN" in sql and " AS business_date" in sql and " AS channel" not in sql:
            rows = [
                {"business_date": date(2026, 5, 29), "metric_value": 100.0},
                {"business_date": date(2026, 5, 22), "metric_value": 101.0},
                {"business_date": date(2026, 5, 15), "metric_value": 99.0},
                {"business_date": date(2026, 5, 8), "metric_value": 100.0},
            ]
        elif "SUM(fact_order.refund_amount)" in sql and "GROUP BY fact_order.channel" in sql and "business_date IN" in sql:
            rows = [
                {"business_date": date(2026, 5, 29), "channel": "paid_ads", "metric_value": 0.10},
                {"business_date": date(2026, 5, 22), "channel": "paid_ads", "metric_value": 0.10},
                {"business_date": date(2026, 5, 15), "channel": "paid_ads", "metric_value": 0.10},
                {"business_date": date(2026, 5, 8), "channel": "paid_ads", "metric_value": 0.10},
                {"business_date": date(2026, 5, 29), "channel": "organic", "metric_value": 0.10},
                {"business_date": date(2026, 5, 22), "channel": "organic", "metric_value": 0.10},
                {"business_date": date(2026, 5, 15), "channel": "organic", "metric_value": 0.10},
                {"business_date": date(2026, 5, 8), "channel": "organic", "metric_value": 0.10},
            ]
        elif "SUM(fact_order.refund_amount)" in sql and "GROUP BY fact_order.channel" in sql:
            rows = [
                {"channel": "paid_ads", "metric_value": 0.35},
                {"channel": "organic", "metric_value": 0.10},
            ]
        elif "GROUP BY fact_order.channel" in sql and "business_date IN" in sql:
            rows = [
                {"business_date": date(2026, 5, 29), "channel": "paid_ads", "metric_value": 100.0},
                {"business_date": date(2026, 5, 22), "channel": "paid_ads", "metric_value": 100.0},
                {"business_date": date(2026, 5, 15), "channel": "paid_ads", "metric_value": 100.0},
                {"business_date": date(2026, 5, 8), "channel": "paid_ads", "metric_value": 100.0},
                {"business_date": date(2026, 5, 29), "channel": "organic", "metric_value": 100.0},
                {"business_date": date(2026, 5, 22), "channel": "organic", "metric_value": 100.0},
                {"business_date": date(2026, 5, 15), "channel": "organic", "metric_value": 100.0},
                {"business_date": date(2026, 5, 8), "channel": "organic", "metric_value": 100.0},
            ]
        elif "GROUP BY fact_order.channel" in sql:
            rows = [
                {"channel": "paid_ads", "metric_value": 60.0},
                {"channel": "organic", "metric_value": 95.0},
            ]
        elif params.get("filter_channel") == "paid_ads":
            rows = [{"metric_value": 60.0}]
        else:
            rows = [{"metric_value": 60.0}]
        return type("QueryResult", (), {"rows": rows, "row_count": len(rows), "latency_ms": 1})()

    def create_evidence(self, row: dict[str, Any]) -> None:
        self.evidence_rows.append(row)
        self.persisted_evidence[row["evidence_id"]] = dict(row)

    def update_evidence_result_summary(self, *, run_id: str, evidence_id: str, result_summary: dict[str, Any]) -> None:
        row = self.persisted_evidence[evidence_id]
        assert row["run_id"] == run_id
        row["result_summary"] = result_summary


class RejectingRenderer:
    def render(self, spec):
        return SQLPlan(sql="SELECT * FROM fact_order", sql_hash="bad")


def _with_contribution_set(summary: dict[str, Any]) -> dict[str, Any]:
    selected = summary.get("selected_candidate")
    if not isinstance(selected, dict):
        return summary
    candidates = summary.get("candidates")
    if not isinstance(candidates, list):
        candidates = [selected]
    evidence_ids: list[str] = []
    for candidate in [selected, *candidates]:
        for evidence_id in candidate.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
    return {
        **summary,
        "contribution_set": {
            "selected_candidate": selected,
            "candidates": candidates,
            "evidence_ids": evidence_ids,
            "factor_graph": {},
            "selection_evidence_id": None,
        },
        "candidates": candidates,
    }


class FailingExecutionRepository(SpyRepository):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def execute_plan(self, plan: SQLPlan, *, run_id: str):
        self.executed.append(plan)
        raise self.error


class EmptyContributionRepository(SpyRepository):
    def __init__(self, *, empty_side: str) -> None:
        super().__init__()
        self.empty_side = empty_side

    def execute_plan(self, plan: SQLPlan, *, run_id: str):
        if "GROUP BY fact_order.channel" in plan.sql:
            is_baseline = "business_date IN" in plan.sql
            if (self.empty_side == "current" and not is_baseline) or (
                self.empty_side == "baseline" and is_baseline
            ):
                self.executed.append(plan)
                return type("QueryResult", (), {"rows": [], "row_count": 0, "latency_ms": 1})()
        return super().execute_plan(plan, run_id=run_id)


class FailingEvidenceRepository(SpyRepository):
    def create_evidence(self, row: dict[str, Any]) -> None:
        raise RuntimeError("SYSTEM_TABLE_WRITE_FAILED")


class NetGmvRefundDriverRepository(SpyRepository):
    def execute_plan(self, plan: SQLPlan, *, run_id: str):
        self.executed.append(plan)
        sql = plan.sql
        if "GROUP BY fact_order.channel" in sql and "business_date IN" in sql:
            if "fact_order.order_amount - fact_order.refund_amount" in sql:
                rows = [
                    {"business_date": date(2026, 5, 29), "channel": "paid_ads", "metric_value": 90.0},
                    {"business_date": date(2026, 5, 22), "channel": "paid_ads", "metric_value": 90.0},
                    {"business_date": date(2026, 5, 15), "channel": "paid_ads", "metric_value": 90.0},
                    {"business_date": date(2026, 5, 8), "channel": "paid_ads", "metric_value": 90.0},
                    {"business_date": date(2026, 5, 29), "channel": "organic", "metric_value": 90.0},
                    {"business_date": date(2026, 5, 22), "channel": "organic", "metric_value": 90.0},
                    {"business_date": date(2026, 5, 15), "channel": "organic", "metric_value": 90.0},
                    {"business_date": date(2026, 5, 8), "channel": "organic", "metric_value": 90.0},
                ]
            else:
                rows = [
                    {"business_date": date(2026, 5, 29), "channel": "paid_ads", "metric_value": 100.0},
                    {"business_date": date(2026, 5, 22), "channel": "paid_ads", "metric_value": 100.0},
                    {"business_date": date(2026, 5, 15), "channel": "paid_ads", "metric_value": 100.0},
                    {"business_date": date(2026, 5, 8), "channel": "paid_ads", "metric_value": 100.0},
                    {"business_date": date(2026, 5, 29), "channel": "organic", "metric_value": 100.0},
                    {"business_date": date(2026, 5, 22), "channel": "organic", "metric_value": 100.0},
                    {"business_date": date(2026, 5, 15), "channel": "organic", "metric_value": 100.0},
                    {"business_date": date(2026, 5, 8), "channel": "organic", "metric_value": 100.0},
                ]
        elif "GROUP BY fact_order.channel" in sql:
            if "fact_order.order_amount - fact_order.refund_amount" in sql:
                rows = [
                    {"channel": "paid_ads", "metric_value": 60.0},
                    {"channel": "organic", "metric_value": 90.0},
                ]
            else:
                rows = [
                    {"channel": "paid_ads", "metric_value": 100.0},
                    {"channel": "organic", "metric_value": 100.0},
                ]
        elif "fact_order.order_amount - fact_order.refund_amount" in sql and "business_date IN" in sql:
            rows = [
                {"business_date": date(2026, 5, 29), "metric_value": 90.0},
                {"business_date": date(2026, 5, 22), "metric_value": 90.0},
                {"business_date": date(2026, 5, 15), "metric_value": 90.0},
                {"business_date": date(2026, 5, 8), "metric_value": 90.0},
            ]
        elif "fact_order.order_amount - fact_order.refund_amount" in sql:
            rows = [{"metric_value": 60.0}]
        elif "business_date IN" in sql:
            rows = [
                {"business_date": date(2026, 5, 29), "metric_value": 100.0},
                {"business_date": date(2026, 5, 22), "metric_value": 100.0},
                {"business_date": date(2026, 5, 15), "metric_value": 100.0},
                {"business_date": date(2026, 5, 8), "metric_value": 100.0},
            ]
        else:
            rows = [{"metric_value": 100.0}]
        return type("QueryResult", (), {"rows": rows, "row_count": len(rows), "latency_ms": 1})()


def test_detect_anomaly_paid_ads_flagged_and_persists_run_scoped_evidence() -> None:
    repo = SpyRepository()
    result = detect_anomaly(
        DetectAnomalyArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            filters={"channel": "paid_ads"},
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    assert result.observation.evidence_ids == ["run-1:E1"]
    assert result.evidence_alias == "E1"
    assert result.evidences[0].evidence_id == "run-1:E1"
    assert repo.evidence_rows[0]["evidence_id"] == "run-1:E1"
    assert len(repo.executed) == 2
    assert result.sql_count == 2


def test_tool_rejects_args_that_do_not_match_current_run_context() -> None:
    repo = SpyRepository()
    result = detect_anomaly(
        DetectAnomalyArgs(run_id="run-1", metric_id="pay_cvr", target_date=date(2026, 6, 5)),
        repository=repo,
        metric_service=StaticMetricService(),
    )
    assert result.observation.ok is False
    assert result.observation.error_code == "RUN_CONTEXT_MISMATCH"

    result = detect_anomaly(
        DetectAnomalyArgs(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 4)),
        repository=repo,
        metric_service=StaticMetricService(),
    )
    assert result.observation.ok is False
    assert result.observation.error_code == "RUN_CONTEXT_MISMATCH"


def test_detect_anomaly_no_anomaly_returns_no_anomaly_observation() -> None:
    repo = SpyRepository()

    def execute_plan(plan: SQLPlan, *, run_id: str):
        repo.executed.append(plan)
        rows = [
            {"business_date": date(2026, 5, 29), "metric_value": 100.0},
            {"business_date": date(2026, 5, 22), "metric_value": 100.0},
            {"business_date": date(2026, 5, 15), "metric_value": 100.0},
            {"business_date": date(2026, 5, 8), "metric_value": 100.0},
        ] if "business_date IN" in plan.sql else [{"metric_value": 99.0}]
        return type("QueryResult", (), {"rows": rows, "row_count": len(rows), "latency_ms": 1})()

    repo.execute_plan = execute_plan
    result = detect_anomaly(
        DetectAnomalyArgs(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5)),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    assert result.observation.error_code == "NO_ANOMALY_DETECTED"
    assert result.evidences[0].result_summary["is_anomaly"] is False


def test_detect_anomaly_repeated_same_e1_returns_persisted_result_without_requery() -> None:
    repo = SpyRepository()
    persisted_summary = {
        "metric_id": "gmv",
        "filters": {"channel": "paid_ads"},
        "is_anomaly": True,
        "delta_pct": -0.4,
    }
    repo.persisted_evidence["run-1:E1"] = {
        "evidence_id": "run-1:E1",
        "run_id": "run-1",
        "guard_status": "passed",
        "result_summary": persisted_summary,
    }

    result = detect_anomaly(
        DetectAnomalyArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            filters={"channel": "paid_ads"},
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    assert result.observation.payload == persisted_summary
    assert result.observation.evidence_ids == ["run-1:E1"]
    assert result.evidence_alias == "E1"
    assert result.sql_count == 0
    assert repo.executed == []
    assert repo.evidence_rows == []


def test_detect_anomaly_existing_e1_for_different_scope_fails_fast_without_requery() -> None:
    repo = SpyRepository()
    repo.persisted_evidence["run-1:E1"] = {
        "evidence_id": "run-1:E1",
        "run_id": "run-1",
        "guard_status": "passed",
        "result_summary": {
            "metric_id": "gmv",
            "filters": {"channel": "paid_ads"},
            "is_anomaly": True,
        },
    }

    result = detect_anomaly(
        DetectAnomalyArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            filters={"channel": "organic"},
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "E1_ALREADY_EXISTS"
    assert result.evidences == []
    assert repo.executed == []
    assert repo.evidence_rows == []


def test_detect_anomaly_sample_n_lt_3_returns_insufficient_baseline_data() -> None:
    repo = SpyRepository()

    def execute_plan(plan: SQLPlan, *, run_id: str):
        repo.executed.append(plan)
        rows = [
            {"business_date": date(2026, 5, 29), "metric_value": 100.0},
            {"business_date": date(2026, 5, 22), "metric_value": 100.0},
        ] if "business_date IN" in plan.sql else [{"metric_value": 60.0}]
        return type("QueryResult", (), {"rows": rows, "row_count": len(rows), "latency_ms": 1})()

    repo.execute_plan = execute_plan
    result = detect_anomaly(
        DetectAnomalyArgs(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5)),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "INSUFFICIENT_BASELINE_DATA"
    assert repo.evidence_rows == []


def test_drilldown_tool_uses_renderer_guard_repository_and_persists_evidence() -> None:
    repo = SpyRepository()
    repo.persisted_evidence.pop("run-1:E2")
    result = drilldown_dimension(
        DrilldownDimensionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            evidence_ids=["run-1:E1"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    assert result.observation.evidence_ids == ["run-1:E2_channel"]
    assert result.evidence_alias == "E2_channel"
    assert repo.evidence_rows[0]["guard_status"] == "passed"
    assert len(repo.executed) == 2
    assert result.sql_count == 2


def test_drilldown_tool_rejects_missing_or_unpersisted_current_run_evidence() -> None:
    repo = SpyRepository()
    result = drilldown_dimension(
        DrilldownDimensionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            evidence_ids=[],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )
    assert result.observation.ok is False
    assert result.observation.error_code == "EVIDENCE_MISSING"

    result = drilldown_dimension(
        DrilldownDimensionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            evidence_ids=["run-1:fake"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )
    assert result.observation.ok is False
    assert result.observation.error_code == "EVIDENCE_MISSING"


def test_drilldown_repeated_same_evidence_slot_returns_persisted_result_without_requery() -> None:
    repo = SpyRepository()
    persisted_summary = {
        "metric_id": "gmv",
        "dimension": "channel",
        "input_evidence_ids": ["run-1:E1"],
        "coverage": 1.0,
        "candidates": [
            {
                "root_cause_type": "campaign_traffic_drop",
                "dimension": "channel",
                "element": "paid_ads",
                "contribution_pct": 1.0,
                "signal_severity": 1.0,
                "evidence_support": 1.0,
                "reflection_factor": 1.0,
                "eng_confidence": 1.0,
                "verdict": "confirmed",
                "evidence_ids": ["run-1:E1"],
            }
        ],
    }
    repo.persisted_evidence["run-1:E2"] = {
        "evidence_id": "run-1:E2",
        "run_id": "run-1",
        "guard_status": "passed",
        "result_summary": persisted_summary,
    }

    result = drilldown_dimension(
        DrilldownDimensionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            evidence_ids=["run-1:E1"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    assert result.observation.evidence_ids == ["run-1:E2"]
    assert result.observation.payload == persisted_summary
    assert result.candidates[0].element == "paid_ads"
    assert repo.executed == []
    assert repo.evidence_rows == []


def test_tool_bad_dimension_returns_dimension_not_allowed() -> None:
    result = drilldown_dimension(
        DrilldownDimensionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="warehouse",
            evidence_ids=["run-1:E1"],
        ),
        repository=SpyRepository(),
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "DIMENSION_NOT_ALLOWED"


def test_tool_args_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DetectAnomalyArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            unexpected=True,
        )


def test_tool_guard_rejection_returns_typed_error_and_does_not_call_execute_plan() -> None:
    repo = SpyRepository()
    result = detect_anomaly(
        DetectAnomalyArgs(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5)),
        repository=repo,
        metric_service=StaticMetricService(),
        renderer=RejectingRenderer(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "SQL_GUARD_REJECTED"
    assert repo.executed == []
    assert repo.evidence_rows == []


@pytest.mark.parametrize(
    ("raised", "expected_code"),
    [
        (RuntimeError("SQL_EXECUTION_FAILED"), "SQL_EXECUTION_FAILED"),
        (RuntimeError("SYSTEM_TABLE_WRITE_FAILED"), "SYSTEM_TABLE_WRITE_FAILED"),
        (ValueError("SQL_PLAN_INVALID: forged plan"), "SQL_PLAN_INVALID"),
        (ValueError("SQL_GUARD_REJECTED: repository revalidation failed"), "SQL_GUARD_REJECTED"),
    ],
)
def test_detect_anomaly_execution_failure_returns_typed_error_without_evidence(
    raised: Exception,
    expected_code: str,
) -> None:
    repo = FailingExecutionRepository(raised)

    result = detect_anomaly(
        DetectAnomalyArgs(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5)),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == expected_code
    assert result.evidences == []
    assert repo.evidence_rows == []


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        (
            "drilldown",
            DrilldownDimensionArgs(
                run_id="run-1",
                metric_id="gmv",
                target_date=date(2026, 6, 5),
                dimension="channel",
                evidence_ids=["run-1:E1"],
            ),
        ),
        (
            "signal",
            FetchRelatedSignalArgs(
                run_id="run-1",
                metric_id="gmv",
                target_date=date(2026, 6, 5),
                signal_type="campaign",
                dimension="channel",
                element="paid_ads",
                evidence_ids=["run-1:E1", "run-1:E2_channel"],
            ),
        ),
        (
            "contribution",
            CalculateContributionArgs(
                run_id="run-1",
                metric_id="gmv",
                target_date=date(2026, 6, 5),
                dimension="channel",
                element="paid_ads",
                evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
            ),
        ),
    ],
)
def test_tools_execution_failure_returns_typed_observation_without_evidence(
    tool_name: str,
    args,
) -> None:
    repo = FailingExecutionRepository(RuntimeError("SQL_EXECUTION_FAILED"))
    if tool_name == "signal":
        repo.seed_e2_family("E2_channel")
    tool = {
        "drilldown": drilldown_dimension,
        "signal": fetch_related_signal,
        "contribution": calculate_contribution,
    }[tool_name]

    result = tool(args, repository=repo, metric_service=StaticMetricService())

    assert result.observation.ok is False
    assert result.observation.error_code == "SQL_EXECUTION_FAILED"
    assert result.evidences == []
    assert repo.evidence_rows == []


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        (
            "detect",
            DetectAnomalyArgs(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5)),
        ),
        (
            "drilldown",
            DrilldownDimensionArgs(
                run_id="run-1",
                metric_id="gmv",
                target_date=date(2026, 6, 5),
                dimension="channel",
                evidence_ids=["run-1:E1"],
            ),
        ),
        (
            "signal",
            FetchRelatedSignalArgs(
                run_id="run-1",
                metric_id="gmv",
                target_date=date(2026, 6, 5),
                signal_type="campaign",
                dimension="channel",
                element="paid_ads",
                evidence_ids=["run-1:E1", "run-1:E2_channel"],
            ),
        ),
        (
            "contribution",
            CalculateContributionArgs(
                run_id="run-1",
                metric_id="gmv",
                target_date=date(2026, 6, 5),
                dimension="channel",
                element="paid_ads",
                evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
            ),
        ),
    ],
)
def test_tools_evidence_persistence_failure_returns_typed_observation(
    tool_name: str,
    args,
) -> None:
    repo = FailingEvidenceRepository()
    if tool_name == "signal":
        repo.seed_e2_family("E2_channel")
    tool = {
        "detect": detect_anomaly,
        "drilldown": drilldown_dimension,
        "signal": fetch_related_signal,
        "contribution": calculate_contribution,
    }[tool_name]

    result = tool(args, repository=repo, metric_service=StaticMetricService())

    assert result.observation.ok is False
    assert result.observation.error_code == "SYSTEM_TABLE_WRITE_FAILED"
    assert result.evidences == []


def test_fetch_related_signal_covers_campaign_inventory_conversion_refund_quality() -> None:
    repo = SpyRepository()
    scenarios = [
        ("campaign", "gmv", "channel", "paid_ads", "E2_channel", "E3_ch_paid_ads"),
        ("campaign", "uv", "channel", "organic", "E2_channel", "E3_ch_organic"),
        ("inventory", "gmv", "category", "electronics", "E2_category", "E3_cat_electronics"),
        ("conversion", "pay_cvr", "device", "mobile", "E2_device", "E3_dev_mobile"),
        ("refund_quality", "refund_rate", "product", "1", "E2_product", "E3_prod_1"),
    ]

    for signal_type, metric_id, dimension, element, e2_alias, expected_alias in scenarios:
        repo.runs["run-1"]["metric_id"] = metric_id
        repo.persisted_evidence[f"run-1:{e2_alias}"] = {
            "evidence_id": f"run-1:{e2_alias}",
            "run_id": "run-1",
            "guard_status": "passed",
        }
        result = fetch_related_signal(
            FetchRelatedSignalArgs(
                run_id="run-1",
                metric_id=metric_id,
                target_date=date(2026, 6, 5),
                signal_type=signal_type,
                dimension=dimension,
                element=element,
                evidence_ids=["run-1:E1", f"run-1:{e2_alias}"],
            ),
            repository=repo,
            metric_service=StaticMetricService(),
        )
        assert result.observation.ok is True
        assert result.evidence_alias == expected_alias
        assert result.evidences[0].evidence_id == f"run-1:{expected_alias}"
        assert len(result.evidences[0].evidence_id) <= 64


def test_fetch_related_signal_rejects_signal_type_that_conflicts_with_metric_dimension_policy() -> None:
    repo = SpyRepository()
    repo.runs["run-1"]["metric_id"] = "refund_rate"
    repo.seed_e2_family("E2_product")

    result = fetch_related_signal(
        FetchRelatedSignalArgs(
            run_id="run-1",
            metric_id="refund_rate",
            target_date=date(2026, 6, 5),
            signal_type="inventory",
            dimension="product",
            element="1",
            evidence_ids=["run-1:E1", "run-1:E2_product"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "QUERY_SPEC_INVALID"
    assert "signal_type must be refund_quality" in result.observation.message
    assert repo.executed == []
    assert repo.evidence_rows == []


def test_fetch_related_signal_rejects_filters_that_conflict_with_selected_element() -> None:
    repo = SpyRepository()
    repo.seed_e2_family("E2_category")

    result = fetch_related_signal(
        FetchRelatedSignalArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            signal_type="inventory",
            dimension="category",
            element="electronics",
            filters={"category": "fashion"},
            evidence_ids=["run-1:E1", "run-1:E2_category"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "QUERY_SPEC_INVALID"
    assert result.evidences == []
    assert repo.executed == []
    assert repo.evidence_rows == []


def test_fetch_related_signal_accepts_e2_family_alias_and_hints_missing_e1() -> None:
    repo = SpyRepository()
    repo.persisted_evidence.pop("run-1:E2")
    repo.persisted_evidence["run-1:E2_channel"] = {
        "evidence_id": "run-1:E2_channel",
        "run_id": "run-1",
        "guard_status": "passed",
    }
    args = FetchRelatedSignalArgs(
        run_id="run-1",
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        signal_type="campaign",
        dimension="channel",
        element="paid_ads",
        evidence_ids=["run-1:E2_channel"],
    )

    missing = fetch_related_signal(args, repository=repo, metric_service=StaticMetricService())

    assert missing.observation.ok is False
    assert missing.observation.error_code == "EVIDENCE_MISSING"
    assert "run-1:E1" in (missing.observation.message or "")
    assert "run-1:E2_channel" in (missing.observation.message or "")
    assert repo.executed == []

    result = fetch_related_signal(
        args.model_copy(update={"evidence_ids": ["run-1:E1", "run-1:E2_channel"]}),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    assert result.evidence_alias == "E3_ch_paid_ads"
    assert result.evidences[0].result_summary["input_evidence_ids"] == ["run-1:E1", "run-1:E2_channel"]


def test_fetch_related_signal_requires_e2_family_matching_requested_dimension() -> None:
    repo = SpyRepository()
    repo.persisted_evidence.pop("run-1:E2")
    repo.persisted_evidence["run-1:E2_category"] = {
        "evidence_id": "run-1:E2_category",
        "run_id": "run-1",
        "guard_status": "passed",
    }
    repo.persisted_evidence["run-1:E2_product"] = {
        "evidence_id": "run-1:E2_product",
        "run_id": "run-1",
        "guard_status": "passed",
    }

    wrong = fetch_related_signal(
        FetchRelatedSignalArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            signal_type="inventory",
            dimension="product",
            element="2",
            evidence_ids=["run-1:E1", "run-1:E2_category"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )
    right = fetch_related_signal(
        FetchRelatedSignalArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            signal_type="inventory",
            dimension="product",
            element="2",
            evidence_ids=["run-1:E1", "run-1:E2_product"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert wrong.observation.ok is False
    assert wrong.observation.error_code == "EVIDENCE_MISSING"
    assert "E2_product" in (wrong.observation.message or "")
    assert right.observation.ok is True
    assert right.evidence_alias == "E3_prod_2"


def test_calculate_contribution_hints_existing_evidence_family_aliases() -> None:
    repo = SpyRepository()
    repo.persisted_evidence.pop("run-1:E2")
    repo.persisted_evidence.pop("run-1:E3")
    repo.persisted_evidence["run-1:E2_channel"] = {
        "evidence_id": "run-1:E2_channel",
        "run_id": "run-1",
        "guard_status": "passed",
    }
    repo.persisted_evidence["run-1:E3_ch_paid_ads"] = {
        "evidence_id": "run-1:E3_ch_paid_ads",
        "run_id": "run-1",
        "guard_status": "passed",
    }

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2_channel"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "EVIDENCE_MISSING"
    assert "run-1:E1" in (result.observation.message or "")
    assert "run-1:E2_channel" in (result.observation.message or "")
    assert "run-1:E3_ch_paid_ads" in (result.observation.message or "")
    assert repo.executed == []


def test_fetch_campaign_signal_uses_fact_campaign_for_current_and_baseline() -> None:
    repo = SpyRepository()
    repo.seed_e2_family("E2_channel")
    result = fetch_related_signal(
        FetchRelatedSignalArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            signal_type="campaign",
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2_channel"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    assert len(repo.executed) == 2
    assert all("fact_campaign" in plan.sql for plan in repo.executed)
    assert "business_date IN" in repo.executed[1].sql
    assert result.evidences[0].data_source == "fact_campaign"
    assert repo.evidence_rows[0]["data_source"] == "fact_campaign"


def test_fetch_related_signal_uses_configured_signal_metric_override() -> None:
    repo = SpyRepository()
    repo.seed_e2_family("E2_channel")
    settings = Settings(
        db_dsn="mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
        readonly_db_dsn="mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
        llm_model="gpt-test",
        llm_api_key="key",
        llm_required=False,
        signal_metric_by_type={
            "campaign": "uv",
            "inventory": "stockout_rate",
            "conversion": "uv",
            "refund_quality": "complaint_rate",
        },
    )

    result = fetch_related_signal(
        FetchRelatedSignalArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            signal_type="campaign",
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2_channel"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
        settings=settings,
    )

    assert result.observation.ok is True
    assert result.evidences[0].result_summary["signal_metric_id"] == "uv"
    assert all("fact_traffic" in plan.sql for plan in repo.executed)


def test_calculate_contribution_emits_e4_from_current_run_evidence() -> None:
    repo = SpyRepository()
    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    assert result.evidence_alias == "E4"
    assert result.observation.evidence_ids == ["run-1:E4"]
    assert result.evidences[0].result_summary["input_evidence_ids"] == [
        "run-1:E1",
        "run-1:E2",
        "run-1:E3",
    ]
    assert result.evidences[0].result_summary["decomposition"]["current"]["pay_cvr"] == 0.10
    assert result.evidences[0].result_summary["decomposition"]["current"]["aov"] == 6.0
    assert result.evidences[0].result_summary["decomposition"]["largest_drop_factor"] == "uv"
    assert result.evidences[0].result_summary["candidates"][0]["evidence_ids"] == [
        "run-1:E1",
        "run-1:E2",
        "run-1:E3",
        "run-1:E4",
    ]
    assert result.evidences[0].result_summary["selected_candidate"]["element"] == "paid_ads"
    contribution_set = result.evidences[0].result_summary["contribution_set"]
    assert contribution_set["selected_candidate"] == result.evidences[0].result_summary["selected_candidate"]
    assert contribution_set["candidates"] == result.evidences[0].result_summary["candidates"]
    assert contribution_set["evidence_ids"] == ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"]
    assert contribution_set["factor_graph"]["decomposition"]["largest_drop_factor"] == "uv"
    assert any("fact_traffic" in plan.sql for plan in repo.executed)


def test_calculate_contribution_empty_current_data_fails_typed_without_e4() -> None:
    repo = EmptyContributionRepository(empty_side="current")

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "NO_CURRENT_DATA"
    assert result.candidates == []
    assert "run-1:E4" not in repo.persisted_evidence


def test_calculate_contribution_empty_baseline_data_fails_typed_without_e4() -> None:
    repo = EmptyContributionRepository(empty_side="baseline")

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "INSUFFICIENT_BASELINE_DATA"
    assert result.candidates == []
    assert "run-1:E4" not in repo.persisted_evidence


def test_calculate_contribution_accepts_selected_non_top_candidate_with_signal_evidence() -> None:
    repo = SpyRepository()
    repo.persisted_evidence["run-1:E3"] = {
        "evidence_id": "run-1:E3",
        "run_id": "run-1",
        "guard_status": "passed",
        "result_summary": {"dimension": "channel", "element": "organic", "delta_pct": -0.82},
    }

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="organic",
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    assert result.evidences[0].result_summary["selected_candidate"]["element"] == "organic"
    assert result.evidences[0].result_summary["selected_candidate"]["signal_severity"] == 0.82


def test_calculate_contribution_rejects_element_that_is_not_attributed_candidate() -> None:
    repo = SpyRepository()

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="affiliate",
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "ATTRIBUTION_COVERAGE_LOW"
    assert result.evidences == []
    assert repo.evidence_rows == []


def test_calculate_contribution_returns_typed_error_when_e4_already_exists_for_different_selection() -> None:
    repo = SpyRepository()
    repo.persisted_evidence["run-1:E4"] = {
        "evidence_id": "run-1:E4",
        "run_id": "run-1",
        "guard_status": "passed",
        "result_summary": {
            "metric_id": "gmv",
            "dimension": "channel",
            "element": "paid_ads",
            "input_evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3"],
            "candidates": [],
        },
    }

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="category",
            element="electronics",
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "E4_ALREADY_EXISTS"
    assert result.observation.evidence_ids == ["run-1:E4"]
    assert result.evidences == []
    assert repo.evidence_rows == []
    assert repo.executed == []


def test_calculate_contribution_factor_queries_keep_base_filters() -> None:
    repo = SpyRepository()

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            filters={"device": "mobile"},
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    factor_plans = [
        plan
        for plan in repo.executed
        if plan.params.get("filter_channel") == "paid_ads"
    ]
    assert factor_plans
    assert all(plan.params.get("filter_device") == "mobile" for plan in factor_plans)


def test_calculate_contribution_rejects_filters_conflicting_with_selected_element() -> None:
    repo = SpyRepository()

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            filters={"channel": "organic"},
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "QUERY_SPEC_INVALID"
    assert result.evidences == []
    assert repo.evidence_rows == []


@pytest.mark.parametrize("metric_id", ["pay_cvr", "refund_rate"])
def test_calculate_contribution_non_factor_metrics_do_not_emit_gmv_decomposition(metric_id: str) -> None:
    repo = SpyRepository()
    repo.runs["run-1"]["metric_id"] = metric_id

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id=metric_id,
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    summary = result.evidences[0].result_summary
    assert summary["metric_id"] == metric_id
    assert "decomposition" not in summary
    assert "factor_query_sources" not in summary
    assert summary["metric_contribution"]["model"] == "dimension_delta"


def test_calculate_contribution_net_gmv_emits_gmv_refund_decomposition() -> None:
    repo = SpyRepository()
    repo.runs["run-1"]["metric_id"] = "net_gmv"

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="net_gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    summary = result.evidences[0].result_summary
    assert "decomposition" not in summary
    assert "metric_contribution" not in summary
    split = summary["net_gmv_decomposition"]
    assert split["current"] == {"gmv": 60.0, "refund": 0.0, "net_gmv": 60.0}
    assert split["baseline"] == {"gmv": 100.0, "refund": 0.0, "net_gmv": 100.0}
    assert split["relative_drops_or_increases"] == {"gmv_drop": 0.4, "refund_increase": 0.0}
    assert split["largest_driver"] == "gmv_drop"
    assert set(summary["factor_query_sources"]) == {"gmv", "net_gmv"}
    assert len([plan for plan in repo.executed if plan.params.get("filter_channel") == "paid_ads"]) >= 4


def test_calculate_contribution_uses_refund_quality_signal_root_cause_for_net_gmv() -> None:
    repo = SpyRepository()
    repo.runs["run-1"]["metric_id"] = "net_gmv"
    repo.persisted_evidence["run-1:E3"] = {
        "evidence_id": "run-1:E3",
        "run_id": "run-1",
        "guard_status": "passed",
        "result_summary": {
            "dimension": "channel",
            "element": "paid_ads",
            "signal_type": "refund_quality",
            "signal_metric_id": "complaint_rate",
            "delta_pct": 2.5,
        },
    }

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="net_gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    summary = result.evidences[0].result_summary
    assert summary["net_gmv_decomposition"]["largest_driver"] == "gmv_drop"
    assert summary["selected_candidate"]["root_cause_type"] == "complaint_or_quality_issue"
    assert summary["selected_candidate"]["signal_severity"] == 1.0


def test_calculate_contribution_net_gmv_can_identify_refund_increase_driver() -> None:
    repo = NetGmvRefundDriverRepository()
    repo.runs["run-1"]["metric_id"] = "net_gmv"

    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="net_gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
        ),
        repository=repo,
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is True
    split = result.evidences[0].result_summary["net_gmv_decomposition"]
    assert split["current"] == {"gmv": 100.0, "refund": 40.0, "net_gmv": 60.0}
    assert split["baseline"] == {"gmv": 100.0, "refund": 10.0, "net_gmv": 90.0}
    assert split["relative_drops_or_increases"]["gmv_drop"] == 0.0
    assert split["relative_drops_or_increases"]["refund_increase"] == 3.0
    assert split["largest_driver"] == "refund_increase"
    assert result.evidences[0].result_summary["selected_candidate"]["root_cause_type"] == "complaint_or_quality_issue"


def test_calculate_contribution_rejects_unpersisted_current_run_evidence() -> None:
    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:fake"],
        ),
        repository=SpyRepository(),
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "EVIDENCE_MISSING"


def test_calculate_contribution_requires_e1_e2_and_e3_before_e4() -> None:
    result = calculate_contribution(
        CalculateContributionArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1"],
        ),
        repository=SpyRepository(),
        metric_service=StaticMetricService(),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "EVIDENCE_MISSING"


def test_rank_root_causes_invokes_internal_adtributor_and_updates_e4() -> None:
    repo = SpyRepository()
    repo.persisted_evidence["run-1:E2"] = {
        "evidence_id": "run-1:E2",
        "run_id": "run-1",
        "guard_status": "passed",
        "result_summary": {
            "metric_id": "gmv",
            "dimension": "channel",
            "adtributor_elements": [
                {"dimension": "channel", "element": "paid_ads", "actual": 20.0, "forecast": 100.0},
                {"dimension": "channel", "element": "social", "actual": 40.0, "forecast": 100.0},
                {"dimension": "channel", "element": "organic", "actual": 90.0, "forecast": 100.0},
            ],
        },
    }
    repo.persisted_evidence["run-1:E2_category"] = {
        "evidence_id": "run-1:E2_category",
        "run_id": "run-1",
        "guard_status": "passed",
        "result_summary": {
            "metric_id": "gmv",
            "dimension": "category",
            "adtributor_elements": [
                {"dimension": "category", "element": "electronics", "actual": 20.0, "forecast": 100.0},
                {"dimension": "category", "element": "fashion", "actual": 1.0, "forecast": 20.0},
            ],
        },
    }
    repo.persisted_evidence["run-1:E4"] = {
        "evidence_id": "run-1:E4",
        "run_id": "run-1",
        "query_spec": {},
        "sql_text": "SELECT order_amount FROM fact_order WHERE business_date = :target_date LIMIT 1000",
        "sql_hash": "e4" * 32,
        "guard_status": "passed",
        "data_source": "fact_order",
        "result_summary": {
            "selected_candidate": {
                "root_cause_type": "campaign_traffic_drop",
                "dimension": "channel",
                "element": "paid_ads",
                "contribution_pct": 0.8,
                "signal_severity": 0.8,
                "evidence_support": 1.0,
                "reflection_factor": 1.0,
                "eng_confidence": 0.8,
                "verdict": "confirmed",
                "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"],
            }
        },
    }
    repo.persisted_evidence["run-1:E4"]["result_summary"] = _with_contribution_set(
        repo.persisted_evidence["run-1:E4"]["result_summary"]
    )
    deps = SimpleNamespace(
        repository=repo,
        metric_service=StaticMetricService(),
        renderer=None,
        settings=Settings(db_dsn="sqlite://", readonly_db_dsn="sqlite://"),
        trace_writer=None,
    )
    rank_tool = next(tool for tool in build_metric_rca_tools(dependencies=deps, run_id="run-1") if tool.name == "rank_root_causes")

    result = rank_tool.invoke({"metric_id": "gmv", "target_date": date(2026, 6, 5)})

    assert result["observation"]["ok"] is True
    selected = repo.persisted_evidence["run-1:E4"]["result_summary"]["selected_candidate"]
    assert selected["explanatory_power"] is not None
    assert selected["surprise_js"] is not None
    assert ("channel", "paid_ads") in [tuple(item) for item in selected["dimension_elements"]]
    assert ("category", "electronics") in [tuple(item) for item in selected["dimension_elements"]]
    assert repo.persisted_evidence["run-1:E4"]["result_summary"]["ranker"] == "adtributor_internal"
    assert repo.evidence_rows[-1]["evidence_id"] == "run-1:E_rank"
    assert repo.evidence_rows[-1]["result_summary"]["selected_candidate"]["explanatory_power"] is not None
    forbidden_alias = "E" + "_adt"
    assert all(not evidence_id.endswith(f":{forbidden_alias}") for evidence_id in repo.persisted_evidence)


def test_rank_root_causes_uses_contribution_set_when_legacy_selected_candidate_disagrees() -> None:
    repo = SpyRepository()
    paid_ads = {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": "paid_ads",
        "contribution_pct": 0.8,
        "signal_severity": 0.8,
        "evidence_support": 1.0,
        "reflection_factor": 1.0,
        "eng_confidence": 0.8,
        "verdict": "confirmed",
        "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"],
    }
    organic = {**paid_ads, "element": "organic"}
    repo.persisted_evidence["run-1:E4"] = {
        "evidence_id": "run-1:E4",
        "run_id": "run-1",
        "query_spec": {},
        "sql_text": "SELECT order_amount FROM fact_order WHERE business_date = :target_date LIMIT 1000",
        "sql_hash": "e4" * 32,
        "guard_status": "passed",
        "data_source": "fact_order",
        "result_summary": {
            "selected_candidate": paid_ads,
            "candidates": [paid_ads],
            "contribution_set": {
                "selected_candidate": organic,
                "candidates": [organic],
                "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"],
                "factor_graph": {},
                "selection_evidence_id": None,
            },
        },
    }
    deps = SimpleNamespace(
        repository=repo,
        metric_service=StaticMetricService(),
        renderer=None,
        settings=Settings(db_dsn="sqlite://", readonly_db_dsn="sqlite://"),
        trace_writer=None,
    )
    rank_tool = next(tool for tool in build_metric_rca_tools(dependencies=deps, run_id="run-1") if tool.name == "rank_root_causes")

    result = rank_tool.invoke({"metric_id": "gmv", "target_date": date(2026, 6, 5)})

    assert result["observation"]["ok"] is True
    selected = repo.persisted_evidence["run-1:E4"]["result_summary"]["selected_candidate"]
    assert selected["element"] == "organic"
    assert repo.evidence_rows[-1]["result_summary"]["selected_candidate"]["element"] == "organic"


def test_rank_root_causes_preserves_signal_verified_selection_when_adtributor_applies() -> None:
    repo = SpyRepository()
    repo.persisted_evidence["run-1:E2_channel"] = {
        "evidence_id": "run-1:E2_channel",
        "run_id": "run-1",
        "guard_status": "passed",
        "result_summary": {
            "metric_id": "gmv",
            "dimension": "channel",
            "adtributor_elements": [
                {"dimension": "channel", "element": "paid_ads", "actual": 20.0, "forecast": 100.0},
                {"dimension": "channel", "element": "organic", "actual": 90.0, "forecast": 100.0},
            ],
        },
    }
    repo.persisted_evidence["run-1:E3_ch_organic"] = {
        "evidence_id": "run-1:E3_ch_organic",
        "run_id": "run-1",
        "guard_status": "passed",
        "result_summary": {"dimension": "channel", "element": "organic", "delta_pct": -0.77},
    }
    repo.persisted_evidence["run-1:E4"] = {
        "evidence_id": "run-1:E4",
        "run_id": "run-1",
        "query_spec": {},
        "sql_text": "SELECT order_amount FROM fact_order WHERE business_date = :target_date LIMIT 1000",
        "sql_hash": "e4" * 32,
        "guard_status": "passed",
        "data_source": "fact_order",
        "result_summary": {
            "selected_candidate": {
                "root_cause_type": "campaign_traffic_drop",
                "dimension": "channel",
                "element": "organic",
                "contribution_pct": 0.25,
                "signal_severity": 0.77,
                "evidence_support": 1.0,
                "reflection_factor": 1.0,
                "eng_confidence": 0.77,
                "verdict": "likely",
                "evidence_ids": ["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_organic", "run-1:E4"],
            },
            "candidates": [
                {
                    "root_cause_type": "campaign_traffic_drop",
                    "dimension": "channel",
                    "element": "paid_ads",
                    "contribution_pct": 0.8,
                    "signal_severity": 0.8,
                    "evidence_support": 1.0,
                    "reflection_factor": 1.0,
                    "eng_confidence": 0.8,
                    "verdict": "likely",
                    "evidence_ids": ["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_organic", "run-1:E4"],
                },
                {
                    "root_cause_type": "campaign_traffic_drop",
                    "dimension": "channel",
                    "element": "organic",
                    "contribution_pct": 0.25,
                    "signal_severity": 0.77,
                    "evidence_support": 1.0,
                    "reflection_factor": 1.0,
                    "eng_confidence": 0.77,
                    "verdict": "likely",
                    "evidence_ids": ["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_organic", "run-1:E4"],
                },
            ],
        },
    }
    repo.persisted_evidence["run-1:E4"]["result_summary"] = _with_contribution_set(
        repo.persisted_evidence["run-1:E4"]["result_summary"]
    )
    deps = SimpleNamespace(
        repository=repo,
        metric_service=StaticMetricService(),
        renderer=None,
        settings=Settings(db_dsn="sqlite://", readonly_db_dsn="sqlite://"),
        trace_writer=None,
    )
    rank_tool = next(tool for tool in build_metric_rca_tools(dependencies=deps, run_id="run-1") if tool.name == "rank_root_causes")

    result = rank_tool.invoke({"metric_id": "gmv", "target_date": date(2026, 6, 5)})

    assert result["observation"]["ok"] is True
    assert result["observation"]["payload"]["adtributor_status"] == "applied"
    assert result["observation"]["payload"]["selected_candidate"]["element"] == "organic"
    assert repo.persisted_evidence["run-1:E4"]["result_summary"]["selected_candidate"]["element"] == "organic"
    assert repo.evidence_rows[-1]["result_summary"]["selected_candidate"]["element"] == "organic"


def test_rank_root_causes_ignores_rejected_adtributor_evidence() -> None:
    repo = SpyRepository()
    repo.persisted_evidence["run-1:E2_rejected"] = {
        "evidence_id": "run-1:E2_rejected",
        "run_id": "run-1",
        "guard_status": "rejected",
        "result_summary": {
            "metric_id": "gmv",
            "dimension": "category",
            "adtributor_elements": [
                {"dimension": "category", "element": "electronics", "actual": 20.0, "forecast": 100.0},
                {"dimension": "category", "element": "fashion", "actual": 90.0, "forecast": 100.0},
            ],
        },
    }
    repo.persisted_evidence["run-1:E4"] = {
        "evidence_id": "run-1:E4",
        "run_id": "run-1",
        "query_spec": {},
        "sql_text": "SELECT order_amount FROM fact_order WHERE business_date = :target_date LIMIT 1000",
        "sql_hash": "e4" * 32,
        "guard_status": "passed",
        "data_source": "fact_order",
        "result_summary": {
            "selected_candidate": {
                "root_cause_type": "campaign_traffic_drop",
                "dimension": "channel",
                "element": "paid_ads",
                "contribution_pct": 0.8,
                "signal_severity": 0.8,
                "evidence_support": 1.0,
                "reflection_factor": 1.0,
                "eng_confidence": 0.8,
                "verdict": "confirmed",
                "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"],
            }
        },
    }
    repo.persisted_evidence["run-1:E4"]["result_summary"] = _with_contribution_set(
        repo.persisted_evidence["run-1:E4"]["result_summary"]
    )
    deps = SimpleNamespace(
        repository=repo,
        metric_service=StaticMetricService(),
        renderer=None,
        settings=Settings(db_dsn="sqlite://", readonly_db_dsn="sqlite://"),
        trace_writer=None,
    )
    rank_tool = next(tool for tool in build_metric_rca_tools(dependencies=deps, run_id="run-1") if tool.name == "rank_root_causes")

    result = rank_tool.invoke({"metric_id": "gmv", "target_date": date(2026, 6, 5)})

    assert result["observation"]["ok"] is True
    selected = repo.persisted_evidence["run-1:E4"]["result_summary"]["selected_candidate"]
    assert result["observation"]["payload"]["adtributor_status"] == "not_applicable"
    assert selected.get("explanatory_power") is None
    assert ("category", "electronics") not in [tuple(item) for item in selected.get("dimension_elements", [])]


def test_rank_root_causes_records_adtributor_not_applicable_without_silent_fallback() -> None:
    repo = SpyRepository()
    repo.persisted_evidence["run-1:E4"] = {
        "evidence_id": "run-1:E4",
        "run_id": "run-1",
        "query_spec": {},
        "sql_text": "SELECT order_amount FROM fact_order WHERE business_date = :target_date LIMIT 1000",
        "sql_hash": "e4" * 32,
        "guard_status": "passed",
        "data_source": "fact_order",
        "result_summary": {
            "selected_candidate": {
                "root_cause_type": "campaign_traffic_drop",
                "dimension": "channel",
                "element": "paid_ads",
                "contribution_pct": 0.8,
                "signal_severity": 0.8,
                "evidence_support": 1.0,
                "reflection_factor": 1.0,
                "eng_confidence": 0.8,
                "verdict": "confirmed",
                "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"],
            }
        },
    }
    repo.persisted_evidence["run-1:E4"]["result_summary"] = _with_contribution_set(
        repo.persisted_evidence["run-1:E4"]["result_summary"]
    )
    deps = SimpleNamespace(
        repository=repo,
        metric_service=StaticMetricService(),
        renderer=None,
        settings=Settings(db_dsn="sqlite://", readonly_db_dsn="sqlite://"),
        trace_writer=None,
    )
    rank_tool = next(tool for tool in build_metric_rca_tools(dependencies=deps, run_id="run-1") if tool.name == "rank_root_causes")

    result = rank_tool.invoke({"metric_id": "gmv", "target_date": date(2026, 6, 5)})

    assert result["observation"]["ok"] is True
    assert result["observation"]["payload"]["ranker"] == "v1"
    assert result["observation"]["payload"]["adtributor_status"] == "not_applicable"
    assert result["observation"]["payload"]["adtributor_error_code"] == "ADTRIBUTOR_NOT_APPLICABLE"
    e4_summary = repo.persisted_evidence["run-1:E4"]["result_summary"]
    e_rank_summary = repo.evidence_rows[-1]["result_summary"]
    assert e4_summary["adtributor_status"] == "not_applicable"
    assert e4_summary["adtributor_error_code"] == "ADTRIBUTOR_NOT_APPLICABLE"
    assert e_rank_summary["adtributor_status"] == "not_applicable"
    assert e_rank_summary["selected_candidate"]["evidence_ids"] == [
        "run-1:E1",
        "run-1:E2",
        "run-1:E3",
        "run-1:E4",
        "run-1:E_rank",
    ]


def test_rank_root_causes_preserves_e4_selection_when_adtributor_not_applicable() -> None:
    repo = SpyRepository()
    repo.persisted_evidence["run-1:E4"] = {
        "evidence_id": "run-1:E4",
        "run_id": "run-1",
        "query_spec": {},
        "sql_text": "SELECT SUM(pay_user_cnt) FROM fact_traffic WHERE business_date = :target_date LIMIT 1000",
        "sql_hash": "e4" * 32,
        "guard_status": "passed",
        "data_source": "fact_traffic",
        "result_summary": {
            "selected_candidate": {
                "root_cause_type": "conversion_drop",
                "dimension": "channel",
                "element": "social",
                "contribution_pct": 0.28,
                "signal_severity": 0.87,
                "evidence_support": 1.0,
                "reflection_factor": 1.0,
                "eng_confidence": 0.24,
                "verdict": "likely",
                "evidence_ids": ["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_social", "run-1:E4"],
            },
            "candidates": [
                {
                    "root_cause_type": "conversion_drop",
                    "dimension": "channel",
                    "element": "paid_ads",
                    "contribution_pct": 0.28,
                    "signal_severity": 0.28,
                    "evidence_support": 1.0,
                    "reflection_factor": 1.0,
                    "eng_confidence": 0.28,
                    "verdict": "likely",
                    "evidence_ids": ["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_social", "run-1:E4"],
                },
                {
                    "root_cause_type": "conversion_drop",
                    "dimension": "channel",
                    "element": "social",
                    "contribution_pct": 0.28,
                    "signal_severity": 0.87,
                    "evidence_support": 1.0,
                    "reflection_factor": 1.0,
                    "eng_confidence": 0.24,
                    "verdict": "likely",
                    "evidence_ids": ["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_social", "run-1:E4"],
                },
            ],
        },
    }
    repo.persisted_evidence["run-1:E4"]["result_summary"] = _with_contribution_set(
        repo.persisted_evidence["run-1:E4"]["result_summary"]
    )
    deps = SimpleNamespace(
        repository=repo,
        metric_service=StaticMetricService(),
        renderer=None,
        settings=Settings(db_dsn="sqlite://", readonly_db_dsn="sqlite://"),
        trace_writer=None,
    )
    rank_tool = next(tool for tool in build_metric_rca_tools(dependencies=deps, run_id="run-1") if tool.name == "rank_root_causes")

    result = rank_tool.invoke({"metric_id": "pay_cvr", "target_date": date(2026, 6, 5)})

    assert result["observation"]["ok"] is True
    assert result["observation"]["payload"]["adtributor_status"] == "not_applicable"
    assert result["observation"]["payload"]["selected_candidate"]["element"] == "social"
    assert repo.persisted_evidence["run-1:E4"]["result_summary"]["selected_candidate"]["element"] == "social"
    assert repo.evidence_rows[-1]["result_summary"]["selected_candidate"]["element"] == "social"


def test_rank_root_causes_requires_persisted_e4_sql_text() -> None:
    repo = SpyRepository()
    repo.persisted_evidence["run-1:E4"] = {
        "evidence_id": "run-1:E4",
        "run_id": "run-1",
        "sql_hash": "e4" * 32,
        "guard_status": "passed",
        "data_source": "fact_order",
        "result_summary": {
            "selected_candidate": {
                "root_cause_type": "campaign_traffic_drop",
                "dimension": "channel",
                "element": "paid_ads",
                "contribution_pct": 0.8,
                "signal_severity": 0.8,
                "evidence_support": 1.0,
                "reflection_factor": 1.0,
                "eng_confidence": 1.0,
                "verdict": "confirmed",
                "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3", "run-1:E4"],
            }
        },
    }
    repo.persisted_evidence["run-1:E4"]["result_summary"] = _with_contribution_set(
        repo.persisted_evidence["run-1:E4"]["result_summary"]
    )
    deps = SimpleNamespace(
        repository=repo,
        metric_service=StaticMetricService(),
        renderer=None,
        settings=Settings(db_dsn="sqlite://", readonly_db_dsn="sqlite://"),
        trace_writer=None,
    )
    rank_tool = next(tool for tool in build_metric_rca_tools(dependencies=deps, run_id="run-1") if tool.name == "rank_root_causes")

    result = rank_tool.invoke({"metric_id": "gmv", "target_date": date(2026, 6, 5)})

    assert result["observation"]["ok"] is False
    assert result["observation"]["error_code"] == "EVIDENCE_MISSING"
    assert result["evidence_ids"] == []
    assert repo.evidence_rows == []

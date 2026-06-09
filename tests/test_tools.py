from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

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
from metric_rca.domain.models import MetricDefinition, SQLPlan


class StaticMetricService:
    def __init__(self) -> None:
        self.definitions = {
            metric_id: MetricDefinition(
                metric_id=metric_id,
                display_name=metric_id,
                formula="test",
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

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, Any] | None:
        row = self.persisted_evidence.get(evidence_id)
        if row and row["run_id"] == run_id:
            return row
        return None

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
                {"channel": "paid_ads", "metric_value": 20.0},
                {"channel": "organic", "metric_value": 95.0},
            ]
        elif params.get("filter_channel") == "paid_ads":
            rows = [{"metric_value": 60.0}]
        else:
            rows = [{"metric_value": 60.0}]
        return type("QueryResult", (), {"rows": rows, "row_count": len(rows), "latency_ms": 1})()

    def create_evidence(self, row: dict[str, Any]) -> None:
        self.evidence_rows.append(row)
        self.persisted_evidence[row["evidence_id"]] = {
            "evidence_id": row["evidence_id"],
            "run_id": row["run_id"],
            "guard_status": row["guard_status"],
        }


class RejectingRenderer:
    def render(self, spec):
        return SQLPlan(sql="SELECT * FROM fact_order", sql_hash="bad")


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
    assert result.evidence_alias == "E2"
    assert repo.evidence_rows[0]["guard_status"] == "passed"
    assert len(repo.executed) == 2


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


def test_fetch_related_signal_covers_campaign_inventory_conversion_refund_quality() -> None:
    repo = SpyRepository()
    scenarios = [
        ("campaign", "gmv", "channel", "paid_ads"),
        ("inventory", "gmv", "category", "electronics"),
        ("conversion", "pay_cvr", "device", "mobile"),
        ("refund_quality", "refund_rate", "product", "1"),
    ]

    for signal_type, metric_id, dimension, element in scenarios:
        repo.runs["run-1"]["metric_id"] = metric_id
        result = fetch_related_signal(
            FetchRelatedSignalArgs(
                run_id="run-1",
                metric_id=metric_id,
                target_date=date(2026, 6, 5),
                signal_type=signal_type,
                dimension=dimension,
                element=element,
                evidence_ids=["run-1:E1", "run-1:E2"],
            ),
            repository=repo,
            metric_service=StaticMetricService(),
        )
        assert result.observation.ok is True
        assert result.evidence_alias == "E3"
        assert result.evidences[0].evidence_id == "run-1:E3"


def test_fetch_campaign_signal_uses_fact_campaign_for_current_and_baseline() -> None:
    repo = SpyRepository()
    result = fetch_related_signal(
        FetchRelatedSignalArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            signal_type="campaign",
            dimension="channel",
            element="paid_ads",
            evidence_ids=["run-1:E1", "run-1:E2"],
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
    assert any("fact_traffic" in plan.sql for plan in repo.executed)


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

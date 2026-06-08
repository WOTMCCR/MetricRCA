from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from metric_rca.domain import models


def test_phase1_domain_models_exist_and_forbid_extra_fields() -> None:
    now = datetime(2026, 6, 8, 12, 0, 0)
    query_spec = models.QuerySpec(
        metric_id="gmv",
        time_range=models.TimeRange(start_date=date(2026, 6, 5), end_date=date(2026, 6, 5)),
    )
    instances = [
        models.TimeRange(start_date=date(2026, 6, 5), end_date=date(2026, 6, 5)),
        models.MetricDefinition(
            metric_id="gmv",
            display_name="GMV",
            formula="sum(order_amount)",
            source_table="fact_order",
        ),
        models.Dimension(dim_id="channel", column="channel", table="fact_order"),
        models.Baseline(
            baseline_dates=[date(2026, 5, 29)],
            baseline_mean=1.0,
            baseline_std=0.1,
            sample_n=1,
        ),
        query_spec,
        models.SQLPlan(sql="SELECT 1", sql_hash="hash"),
        models.Evidence(
            evidence_id="e1",
            query_spec=query_spec,
            sql="SELECT 1",
            sql_hash="hash",
            guard_status="passed",
            result_summary={"metric_value": 1},
            data_source="fact_order",
            created_at=now,
        ),
        models.Observation(action_name="detect_anomaly", ok=True),
        models.RootCauseCandidate(
            root_cause_type="campaign_traffic_drop",
            contribution_pct=0.8,
            signal_severity=2.5,
            evidence_support=1.0,
            eng_confidence=0.9,
            verdict="confirmed",
            evidence_ids=["e1"],
        ),
        models.AgentAction(action="finish", args={}),
        models.ReflectionIssue(check="evidence_coverage", severity="warning", by="rule", message="ok"),
        models.ReflectionResult(passed=True),
        models.MemoryRecord(
            memory_id="m1",
            layer="case",
            key="gmv|channel",
            payload={"hint": "paid_ads"},
            created_at=now,
        ),
        models.EvalCase(
            case_id="gmv_paid_ads_drop",
            question="why",
            expected_metric="gmv",
            expected_anomaly=True,
            expected_root_cause="campaign_traffic_drop",
        ),
        models.TraceStep(
            step_id="s1",
            run_id="r1",
            seq=1,
            node="parse_question",
            created_at=now,
        ),
        models.AgentRun(
            run_id="r1",
            question="why",
            target_date=date(2026, 6, 5),
            status="running",
            created_at=now,
        ),
    ]

    for instance in instances:
        round_tripped = type(instance).model_validate(instance.model_dump())
        assert round_tripped == instance
        with pytest.raises(ValidationError):
            type(instance).model_validate({**instance.model_dump(), "unexpected": "nope"})

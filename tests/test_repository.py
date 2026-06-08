from __future__ import annotations

from datetime import date
import hashlib
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from metric_rca.config.settings import get_settings
from metric_rca.domain.models import SQLPlan
from metric_rca.guardrails.query_spec import build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql
from metric_rca.repositories.metric_repository import MetricRepository


def test_repository_executes_only_guarded_plan_and_writes_audit() -> None:
    repo = MetricRepository.from_settings(get_settings())
    spec = build_query_spec(
        metric_id="gmv",
        start_date=date(2026, 6, 5),
        end_date=date(2026, 6, 5),
        group_by=["channel"],
        filters={"channel": "paid_ads"},
    )
    plan = guard_sql(SQLRenderer().render(spec))
    result = repo.execute_plan(plan, run_id="test_repo_guarded")

    assert result.row_count >= 1
    assert all(row["channel"] == "paid_ads" for row in result.rows)

    audit = repo.latest_audit("test_repo_guarded")
    assert audit["sql_hash"] == plan.sql_hash
    assert audit["guard_status"] == "passed"
    assert audit["row_count"] == result.row_count

    category_spec = build_query_spec(
        metric_id="gmv",
        start_date=date(2026, 6, 5),
        end_date=date(2026, 6, 5),
        group_by=["category"],
    )
    category_plan = guard_sql(SQLRenderer().render(category_spec))
    category_result = repo.execute_plan(category_plan, run_id="test_repo_category")
    assert category_result.row_count >= 1
    assert {"category", "metric_value"} <= set(category_result.rows[0])
    repo.close()


def test_repository_rejects_raw_or_rejected_sql_plan() -> None:
    repo = MetricRepository.from_settings(get_settings())
    rejected = SQLPlan(
        sql="SELECT order_amount FROM fact_order",
        sql_hash="not-rendered",
        guard_status="rejected",
        guard_errors=["missing business_date"],
    )
    with pytest.raises(ValueError, match="SQL_GUARD_REJECTED"):
        repo.execute_plan(rejected, run_id="test_rejected")

    forged_passed = SQLPlan(
        sql="SELECT order_amount FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1",
        sql_hash="wrong",
        guard_status="passed",
        guard_errors=[],
    )
    with pytest.raises(ValueError, match="SQL_PLAN_INVALID"):
        repo.execute_plan(forged_passed, run_id="test_forged_passed")

    correctly_hashed_raw = SQLPlan(
        sql="SELECT order_amount FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1",
        sql_hash=hashlib.sha256(
            b"SELECT order_amount FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1"
        ).hexdigest(),
        guard_status="passed",
        guard_errors=[],
    )
    with pytest.raises(ValueError, match="SQL_PLAN_INVALID"):
        repo.execute_plan(correctly_hashed_raw, run_id="test_correct_hash_raw")
    repo.close()


def test_filter_value_is_bound_parameter_not_executed_sql() -> None:
    repo = MetricRepository.from_settings(get_settings())
    injection = "paid_ads' OR 1=1 --"
    spec = build_query_spec(
        metric_id="gmv",
        start_date=date(2026, 6, 5),
        end_date=date(2026, 6, 5),
        filters={"channel": injection},
    )
    plan = guard_sql(SQLRenderer().render(spec))
    assert plan.guard_status == "passed", plan.guard_errors
    assert injection not in plan.sql
    assert plan.params["filter_channel"] == injection

    result = repo.execute_plan(plan, run_id="test_bound_param")
    assert result.row_count == 1
    assert result.rows[0]["metric_value"] is None
    repo.close()


def test_readonly_execution_account_rejects_dml() -> None:
    settings = get_settings()
    engine = create_engine(str(settings.readonly_db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            with pytest.raises(DBAPIError):
                conn.execute(text("DELETE FROM fact_order WHERE business_date = '2026-06-05'"))
    finally:
        engine.dispose()


def test_repository_persists_documented_system_tables() -> None:
    repo = MetricRepository.from_settings(get_settings())
    now = datetime(2026, 6, 8, 12, 0, 0)
    suffix = uuid4().hex
    run_id = f"repo_system_run_{suffix}"
    step_id = f"repo_system_step_{suffix}"
    evidence_id = f"repo_system_evidence_{suffix}"
    task_id = f"repo_system_task_{suffix}"
    memory_id = f"repo_system_memory_{suffix}"
    eval_id = f"repo_system_eval_{suffix}"
    repo.create_agent_run(
        {
            "run_id": run_id,
            "question": "why",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "status": "running",
            "error_code": None,
            "created_at": now,
            "finished_at": None,
        }
    )
    repo.create_trace_step(
        {
            "step_id": step_id,
            "run_id": run_id,
            "seq": 1,
            "node": "parse_question",
            "action": None,
            "input_summary": {},
            "output_summary": {},
            "error_code": None,
            "latency_ms": 0,
            "created_at": now,
        }
    )
    repo.create_evidence(
        {
            "evidence_id": evidence_id,
            "run_id": run_id,
            "query_spec": {"metric_id": "gmv"},
            "sql_text": "SELECT 1",
            "sql_hash": "0" * 64,
            "guard_status": "passed",
            "result_summary": {"metric_value": 1},
            "data_source": "fact_order",
            "created_at": now,
        }
    )
    repo.create_operation_task(
        {
            "task_id": task_id,
            "run_id": run_id,
            "title": "Fix paid ads",
            "root_cause_type": "campaign_traffic_drop",
            "payload": {"owner": "ops"},
            "created_at": now,
        }
    )
    repo.create_memory_record(
        {
            "memory_id": memory_id,
            "layer": "case",
            "mem_key": "gmv|channel",
            "payload": {"hint": "paid_ads"},
            "confidence": 0.5,
            "source": "test",
            "version": 1,
            "ttl_days": None,
            "created_at": now,
        }
    )
    repo.create_eval_run(
        {
            "eval_id": eval_id,
            "created_at": now,
            "summary": {"case_total": 0},
        }
    )
    repo.create_eval_case_result(
        {
            "eval_id": eval_id,
            "case_id": "gmv_paid_ads_drop",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 1,
            "top3_ok": 1,
            "evidence_coverage": 1.0,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "detail": {"ok": True},
        }
    )
    counts = repo.system_table_counts(
        {
            "agent_run": ("run_id", run_id),
            "trace_step": ("step_id", step_id),
            "evidence": ("evidence_id", evidence_id),
            "operation_task": ("task_id", task_id),
            "memory_record": ("memory_id", memory_id),
            "eval_run": ("eval_id", eval_id),
            "eval_case_result": ("eval_id", eval_id),
        }
    )
    assert counts == {
        "agent_run": 1,
        "trace_step": 1,
        "evidence": 1,
        "operation_task": 1,
        "memory_record": 1,
        "eval_run": 1,
        "eval_case_result": 1,
    }
    repo.close()

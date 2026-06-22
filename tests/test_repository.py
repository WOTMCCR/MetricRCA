from __future__ import annotations

from datetime import date
import hashlib
import json
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    InternalError,
    IntegrityError,
    OperationalError,
    TimeoutError as SQLAlchemyTimeoutError,
)

from metric_rca.config.settings import get_settings
from metric_rca.domain.models import SQLPlan
from metric_rca.guardrails.query_spec import build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql
from metric_rca.repositories.metric_repository import MetricRepository
from metric_rca.runtime.evidence_identity import compose_evidence_id


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
    evidence_id = compose_evidence_id(run_id, "E_repo_system")
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
            "token_usage": None,
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


def test_repository_upserts_eval_run_summary() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_eval_run_table(engine)
    repo = MetricRepository(readonly_engine=engine, audit_engine=engine, statement_timeout_ms=3000)
    now = datetime(2026, 6, 8, 12, 0, 0)

    repo.upsert_eval_run_summary(
        {
            "eval_id": "eval-progress",
            "created_at": now,
            "summary": {"complete": False, "case_total": 1},
        }
    )
    repo.upsert_eval_run_summary(
        {
            "eval_id": "eval-progress",
            "created_at": now,
            "summary": {"complete": True, "case_total": 2},
        }
    )

    assert repo.get_eval_run("eval-progress")["summary"] == {"complete": True, "case_total": 2}
    repo.close()


def test_repository_upserts_eval_case_result() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_eval_case_result_table(engine)
    repo = MetricRepository(readonly_engine=engine, audit_engine=engine, statement_timeout_ms=3000)

    repo.upsert_eval_case_result(
        {
            "eval_id": "eval-progress",
            "case_id": "gmv_paid_ads_drop",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 0,
            "top3_ok": 1,
            "evidence_coverage": 0.5,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "detail": {"attempt": 1},
        }
    )
    repo.upsert_eval_case_result(
        {
            "eval_id": "eval-progress",
            "case_id": "gmv_paid_ads_drop",
            "intent_ok": 1,
            "anomaly_ok": 1,
            "top1_ok": 1,
            "top3_ok": 1,
            "evidence_coverage": 1.0,
            "sql_safe": 1,
            "reflection_repair_ok": 1,
            "detail": {"attempt": 2},
        }
    )

    rows = repo.get_eval_case_results("eval-progress")
    assert len(rows) == 1
    assert rows[0]["top1_ok"] == 1
    assert rows[0]["evidence_coverage"] == 1.0
    assert rows[0]["detail"] == {"attempt": 2}
    repo.close()


def test_repository_retries_transient_system_table_write_once() -> None:
    engine = _FlakyWriteEngine([_operational_error(1213, "Deadlock found")])
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )

    repo.create_trace_step(_trace_step_row())

    assert engine.attempts == 2


def test_repository_retries_repeated_transient_system_table_writes() -> None:
    engine = _FlakyWriteEngine(
        [
            _operational_error(1213, "Deadlock found"),
            _operational_error(1205, "Lock wait timeout exceeded"),
            _operational_error(1040, "Too many connections"),
            _operational_error(2006, "MySQL server has gone away"),
        ]
    )
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )

    repo.create_trace_step(_trace_step_row())

    assert engine.attempts == 5


def test_repository_confirms_idempotent_insert_after_ambiguous_commit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_trace_step_table(engine)
    row = _trace_step_row()
    seed_repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )
    seed_repo.create_trace_step(row)
    flaky_engine = _FailOnceThenRealEngine(engine, _operational_error(2013, "Lost connection to MySQL server during query"))
    repo = MetricRepository(
        readonly_engine=flaky_engine,
        audit_engine=flaky_engine,
        statement_timeout_ms=3000,
    )

    repo.create_trace_step(row)

    assert flaky_engine.begin_attempts == 1


def test_repository_rejects_ambiguous_commit_duplicate_when_payload_differs() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_trace_step_table(engine)
    original = _trace_step_row()
    seed_repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )
    seed_repo.create_trace_step(original)
    changed = {**original, "output_summary": {"changed": True}}
    flaky_engine = _FailOnceThenRealEngine(engine, _operational_error(2013, "Lost connection to MySQL server during query"))
    repo = MetricRepository(
        readonly_engine=flaky_engine,
        audit_engine=flaky_engine,
        statement_timeout_ms=3000,
    )

    with pytest.raises(RuntimeError, match="SYSTEM_TABLE_WRITE_FAILED"):
        repo.create_trace_step(changed)

    assert flaky_engine.begin_attempts == 2


def test_repository_confirms_agent_run_finish_after_ambiguous_update_commit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_agent_run_table(engine)
    run_id = "run-ambiguous-finish"
    seed_repo = MetricRepository(readonly_engine=engine, audit_engine=engine, statement_timeout_ms=3000)
    seed_repo.create_agent_run(
        {
            "run_id": run_id,
            "question": "why",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "status": "running",
            "error_code": None,
            "created_at": datetime(2026, 6, 8, 12, 0, 0),
            "finished_at": None,
        }
    )
    flaky_engine = _ApplyThenFailEngine(engine, _operational_error(2013, "Lost connection to MySQL server during query"))
    repo = MetricRepository(readonly_engine=flaky_engine, audit_engine=flaky_engine, statement_timeout_ms=3000)

    repo.finish_agent_run(
        run_id=run_id,
        status="succeeded",
        error_code=None,
        finished_at=datetime(2026, 6, 8, 12, 1, 0),
        total_tokens=12,
        total_latency_ms=34,
        token_breakdown=[{"seq": 1, "token_usage": {"total_tokens": 12}}],
    )

    with engine.connect() as conn:
        row = conn.execute(text("SELECT status, error_code, total_tokens FROM agent_run WHERE run_id = :run_id"), {"run_id": run_id}).one()
    assert row.status == "succeeded"
    assert row.error_code is None
    assert row.total_tokens == 12


def test_repository_retries_sql_audit_write_with_stable_audit_key() -> None:
    engine = _FlakyWriteEngine([_operational_error(1213, "Deadlock found")])
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )
    plan = SQLPlan(
        sql="SELECT 1",
        sql_hash=hashlib.sha256(b"SELECT 1").hexdigest(),
        guard_status="passed",
        guard_errors=[],
        params={},
    )

    repo._write_audit(run_id="run-1", plan=plan, row_count=1, latency_ms=1)

    assert engine.attempts == 2


def test_repository_confirms_sql_audit_after_ambiguous_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_sql_audit_table(engine)
    fixed_uuid = type("FixedUUID", (), {"hex": "fixedauditkey"})()
    monkeypatch.setattr("metric_rca.repositories.metric_repository.uuid4", lambda: fixed_uuid)
    plan = SQLPlan(
        sql="SELECT 1",
        sql_hash=hashlib.sha256(b"SELECT 1").hexdigest(),
        guard_status="passed",
        guard_errors=[],
        params={},
    )
    seed_repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )
    seed_repo._write_audit(run_id="run-1", plan=plan, row_count=1, latency_ms=1)
    flaky_engine = _FailOnceThenRealEngine(engine, _operational_error(2013, "Lost connection to MySQL server during query"))
    repo = MetricRepository(
        readonly_engine=flaky_engine,
        audit_engine=flaky_engine,
        statement_timeout_ms=3000,
    )

    repo._write_audit(run_id="run-1", plan=plan, row_count=1, latency_ms=1)

    assert flaky_engine.begin_attempts == 1


def test_repository_retries_sqlalchemy_pool_timeout_system_table_write() -> None:
    engine = _FlakyWriteEngine([SQLAlchemyTimeoutError("QueuePool limit reached")])
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )

    repo.create_trace_step(_trace_step_row())

    assert engine.attempts == 2


def test_repository_retries_invalidated_system_table_connection() -> None:
    engine = _FlakyWriteEngine([_connection_invalidated_error()])
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )

    repo.create_trace_step(_trace_step_row())

    assert engine.attempts == 2


def test_repository_retries_system_write_connection_loss_without_errno() -> None:
    engine = _FlakyWriteEngine([_operational_error(0, "Packet sequence number wrong - got 1 expected 2")])
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )

    repo.create_trace_step(_trace_step_row())

    assert engine.attempts == 2


def test_repository_retries_sqlalchemy_interface_system_write_error_without_errno() -> None:
    engine = _FlakyWriteEngine([InterfaceError("INSERT INTO trace_step", {}, _MysqlError(0, ""))])
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )

    repo.create_trace_step(_trace_step_row())

    assert engine.attempts == 2


def test_repository_retries_operational_system_write_error_with_errno_zero() -> None:
    engine = _FlakyWriteEngine([_operational_error(0, "")])
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )

    repo.create_trace_step(_trace_step_row())

    assert engine.attempts == 2


def test_repository_retries_sqlalchemy_internal_system_write_error_without_errno() -> None:
    engine = _FlakyWriteEngine([InternalError("INSERT INTO trace_step", {}, _MysqlError(0, ""))])
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )

    repo.create_trace_step(_trace_step_row())

    assert engine.attempts == 2


def test_repository_does_not_retry_non_transient_system_table_write() -> None:
    engine = _FlakyWriteEngine([_operational_error(1062, "Duplicate entry")])
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )

    with pytest.raises(RuntimeError, match="SYSTEM_TABLE_WRITE_FAILED"):
        repo.create_trace_step(_trace_step_row())

    assert engine.attempts == 1


def test_repository_read_helpers_return_decoded_persisted_artifacts_ordered() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repo = MetricRepository(
        readonly_engine=engine,
        audit_engine=engine,
        statement_timeout_ms=3000,
    )
    now = datetime(2026, 6, 8, 12, 0, 0)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE agent_run (
                  run_id TEXT PRIMARY KEY, question TEXT, metric_id TEXT, target_date TEXT,
                  status TEXT, error_code TEXT, runtime_version INTEGER NOT NULL DEFAULT 3,
                  total_tokens INTEGER, total_latency_ms INTEGER, token_breakdown TEXT,
                  created_at TEXT, finished_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE trace_step (
                  step_id TEXT PRIMARY KEY, run_id TEXT, seq INTEGER, node TEXT, action TEXT,
                  input_summary TEXT, output_summary TEXT, error_code TEXT, latency_ms INTEGER,
                  token_usage TEXT, created_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE evidence (
                  evidence_pk INTEGER PRIMARY KEY AUTOINCREMENT,
                  evidence_id TEXT NOT NULL UNIQUE,
                  run_id TEXT NOT NULL,
                  alias TEXT NOT NULL,
                  query_spec TEXT,
                  sql_text TEXT,
                  sql_hash TEXT,
                  guard_status TEXT,
                  result_summary TEXT,
                  data_source TEXT,
                  created_at TEXT,
                  UNIQUE(run_id, alias)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE sql_audit (
                  audit_id INTEGER PRIMARY KEY AUTOINCREMENT, audit_key TEXT UNIQUE, run_id TEXT, sql_text TEXT, sql_hash TEXT,
                  guard_status TEXT, guard_errors TEXT, row_count INTEGER, latency_ms INTEGER, created_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE operation_task (
                  task_id TEXT PRIMARY KEY, run_id TEXT, title TEXT, root_cause_type TEXT,
                  payload TEXT, created_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE eval_run (
                  eval_id TEXT PRIMARY KEY, created_at TEXT, summary TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE eval_case_result (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, eval_id TEXT, case_id TEXT,
                  intent_ok INTEGER, anomaly_ok INTEGER, top1_ok INTEGER, top3_ok INTEGER,
                  evidence_coverage REAL, sql_safe INTEGER, reflection_repair_ok INTEGER, detail TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE anomaly_ground_truth (
                  case_id TEXT PRIMARY KEY, scenario_id TEXT, split TEXT, seed INTEGER, profile TEXT,
                  business_date TEXT, metric_id TEXT, expected_anomaly INTEGER,
                  root_cause_type TEXT, dimension TEXT, element TEXT,
                  root_causes TEXT, confounders TEXT, expected_behavior TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO agent_run (
                  run_id, question, metric_id, target_date, status, error_code,
                  runtime_version, total_tokens, total_latency_ms, token_breakdown,
                  created_at, finished_at
                )
                VALUES (
                  'run-1', 'why', 'gmv', '2026-06-05', 'succeeded', NULL,
                  3, 5, 9, :token_breakdown, :now, :now
                )
                """
            ),
            {
                "now": now.isoformat(),
                "token_breakdown": '[{"node":"llm_call","total_tokens":5}]',
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO trace_step
                VALUES
                  ('t2', 'run-1', 2, 'execute_tool', 'detect_anomaly', '{}', '{"b": 2}', NULL, 4, '{"total_tokens": 5}', :now),
                  ('t1', 'run-1', 1, 'parse_question', 'parse_question', '{"a": 1}', '{}', NULL, 1, NULL, :now)
                """
            ),
            {"now": now.isoformat()},
        )
        conn.execute(
            text(
                """
                INSERT INTO evidence (
                  evidence_id, run_id, alias, query_spec, sql_text, sql_hash,
                  guard_status, result_summary, data_source, created_at
                )
                VALUES (
                  'run-1:E4', 'run-1', 'E4', '{"metric_id": "gmv"}', 'SELECT 1', :hash,
                  'passed', '{"selected_candidate": {"dimension": "channel"}}', 'fact_order', :now
                )
                """
            ),
            {"hash": "0" * 64, "now": now.isoformat()},
        )
        conn.execute(
            text(
                """
                INSERT INTO sql_audit (
                  audit_key, run_id, sql_text, sql_hash, guard_status, guard_errors, row_count, latency_ms, created_at
                )
                VALUES ('audit-existing', 'run-1', 'SELECT 1', :hash, 'passed', '[]', 1, 3, :now)
                """
            ),
            {"hash": "0" * 64, "now": now.isoformat()},
        )
        conn.execute(
            text(
                """
                INSERT INTO operation_task
                VALUES ('task-1', 'run-1', 'Fix channel', 'campaign_traffic_drop', '{"owner": "ops"}', :now)
                """
            ),
            {"now": now.isoformat()},
        )
        conn.execute(
            text("INSERT INTO eval_run VALUES ('eval-1', :now, '{\"case_total\": 1}')"),
            {"now": now.isoformat()},
        )
        conn.execute(
            text(
                """
                INSERT INTO eval_case_result (
                  eval_id, case_id, intent_ok, anomaly_ok, top1_ok, top3_ok,
                  evidence_coverage, sql_safe, reflection_repair_ok, detail
                )
                VALUES ('eval-1', 'gmv_paid_ads_drop', 1, 1, 1, 1, 1.0, 1, 1, '{"ok": true}')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO anomaly_ground_truth
                VALUES (
                  'gmv_paid_ads_drop', 'gmv_paid_ads_drop', 'regression', 20260606, 'regression',
                  '2026-06-05', 'gmv', 1, 'campaign_traffic_drop', 'channel', 'paid_ads',
                  :root_causes,
                  :confounders,
                  :expected_behavior
                )
                """
            ),
            {
                "root_causes": '[{"root_cause_type":"campaign_traffic_drop","dimension":"channel","element":"paid_ads","weight":1.0}]',
                "confounders": "[]",
                "expected_behavior": '{"top1_policy":"dominant_effect"}',
            },
        )

    agent_run = repo.get_agent_run("run-1")
    assert agent_run["status"] == "succeeded"
    assert agent_run["total_tokens"] == 5
    assert agent_run["total_latency_ms"] == 9
    assert agent_run["token_breakdown"] == [{"node": "llm_call", "total_tokens": 5}]
    assert [row["seq"] for row in repo.get_trace_steps("run-1")] == [1, 2]
    assert repo.get_trace_steps("run-1")[0]["input_summary"] == {"a": 1}
    assert repo.get_trace_steps("run-1")[1]["token_usage"] == {"total_tokens": 5}
    assert repo.get_evidences("run-1")[0]["result_summary"]["selected_candidate"]["dimension"] == "channel"
    assert repo.get_evidence_by_alias(run_id="run-1", alias="E4")["evidence_id"] == "run-1:E4"
    assert repo.get_sql_audit_rows("run-1")[0]["guard_errors"] == []
    assert repo.get_operation_tasks("run-1")[0]["payload"] == {"owner": "ops"}
    assert repo.get_eval_run("eval-1")["summary"] == {"case_total": 1}
    assert repo.get_eval_case_results("eval-1")[0]["detail"] == {"ok": True}
    repo.upsert_eval_run_summary(
        {
            "eval_id": "eval-progress",
            "created_at": now,
            "summary": {"complete": False, "case_total": 1},
        }
    )
    repo.upsert_eval_run_summary(
        {
            "eval_id": "eval-progress",
            "created_at": now,
            "summary": {"complete": True, "case_total": 2},
        }
    )
    assert repo.get_eval_run("eval-progress")["summary"] == {"complete": True, "case_total": 2}
    assert repo.get_ground_truth_cases(["gmv_paid_ads_drop", "missing"]) == {
        "gmv_paid_ads_drop": {
            "case_id": "gmv_paid_ads_drop",
            "business_date": "2026-06-05",
            "metric_id": "gmv",
            "expected_anomaly": 1,
            "root_cause_type": "campaign_traffic_drop",
            "dimension": "channel",
            "element": "paid_ads",
            "root_causes": '[{"root_cause_type":"campaign_traffic_drop","dimension":"channel","element":"paid_ads","weight":1.0}]',
            "confounders": "[]",
            "expected_behavior": '{"top1_policy":"dominant_effect"}',
            "scenario_id": "gmv_paid_ads_drop",
            "split": "regression",
            "seed": 20260606,
            "profile": "regression",
        }
    }

    repo.close()


def test_repository_memory_records_for_run_exclude_future_same_metric_records() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_agent_run_table(engine)
    _create_trace_step_table(engine)
    _create_memory_record_table(engine)
    repo = MetricRepository(readonly_engine=engine, audit_engine=engine, statement_timeout_ms=3000)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO agent_run (
                  run_id, question, metric_id, target_date, status, error_code,
                  total_tokens, total_latency_ms, token_breakdown, created_at, finished_at
                )
                VALUES (
                  'run-1', 'why', 'gmv', '2026-06-05', 'succeeded', NULL,
                  NULL, NULL, NULL, :created_at, :finished_at
                )
                """
            ),
            {"created_at": "2026-06-15 12:00:00", "finished_at": "2026-06-15 12:10:00"},
        )
        rows = [
            (
                "m-sem",
                "semantic",
                "gmv|semantic",
                {"metric_id": "gmv"},
                "2026-06-15 11:00:00",
            ),
            (
                "m-old",
                "episodic",
                "gmv|run",
                {"metric_id": "gmv", "run_id": "older-run"},
                "2026-06-15 11:30:00",
            ),
            (
                "m-own",
                "reflection",
                "gmv|run",
                {"metric_id": "gmv", "run_id": "run-1", "error_code": "REFLECTION_REPAIR_FAILED"},
                "2026-06-15 12:11:00",
            ),
            (
                "m-future",
                "episodic",
                "gmv|run",
                {"metric_id": "gmv", "run_id": "future-run"},
                "2026-06-15 12:20:00",
            ),
            (
                "m-other-metric",
                "semantic",
                "refund_rate|semantic",
                {"metric_id": "refund_rate"},
                "2026-06-15 11:00:00",
            ),
        ]
        for memory_id, layer, mem_key, payload, created_at in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO memory_record (
                      memory_id, layer, mem_key, payload, confidence, source,
                      version, ttl_days, created_at
                    )
                    VALUES (
                      :memory_id, :layer, :mem_key, :payload, 0.9, 'test',
                      1, 30, :created_at
                    )
                    """
                ),
                {
                    "memory_id": memory_id,
                    "layer": layer,
                    "mem_key": mem_key,
                    "payload": json.dumps(payload),
                    "created_at": created_at,
                },
            )

    records = repo.get_memory_records_for_run("run-1")

    assert [row["memory_id"] for row in records] == ["m-sem", "m-old", "m-own"]
    repo.close()


def test_repository_memory_records_for_run_not_hidden_by_unrelated_history() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_agent_run_table(engine)
    _create_trace_step_table(engine)
    _create_memory_record_table(engine)
    repo = MetricRepository(readonly_engine=engine, audit_engine=engine, statement_timeout_ms=3000)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO agent_run (
                  run_id, question, metric_id, target_date, status, error_code,
                  total_tokens, total_latency_ms, token_breakdown, created_at, finished_at
                )
                VALUES (
                  'run-1', 'why', 'gmv', '2026-06-05', 'succeeded', NULL,
                  NULL, NULL, NULL, '2026-06-15 12:00:00', '2026-06-15 12:10:00'
                )
                """
            )
        )
        for index in range(501):
            conn.execute(
                text(
                    """
                    INSERT INTO memory_record (
                      memory_id, layer, mem_key, payload, confidence, source,
                      version, ttl_days, created_at
                    )
                    VALUES (
                      :memory_id, 'semantic', 'refund_rate|semantic', :payload,
                      0.9, 'test', 1, 30, :created_at
                    )
                    """
                ),
                {
                    "memory_id": f"unrelated-{index}",
                    "payload": json.dumps({"metric_id": "refund_rate", "index": index}),
                    "created_at": f"2026-06-15 10:{index % 60:02d}:00",
                },
            )
        conn.execute(
            text(
                """
                INSERT INTO memory_record (
                  memory_id, layer, mem_key, payload, confidence, source,
                  version, ttl_days, created_at
                )
                VALUES (
                  'm-target', 'reflection', 'gmv|run', :payload,
                  0.8, 'test', 1, 30, '2026-06-15 11:30:00'
                )
                """
            ),
            {"payload": json.dumps({"metric_id": "gmv", "run_id": "older-run"})},
        )

    records = repo.get_memory_records_for_run("run-1")

    assert [row["memory_id"] for row in records] == ["m-target"]
    repo.close()


def test_repository_memory_records_for_run_include_suite_scoped_records_only_when_read() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_agent_run_table(engine)
    _create_trace_step_table(engine)
    _create_memory_record_table(engine)
    repo = MetricRepository(readonly_engine=engine, audit_engine=engine, statement_timeout_ms=3000)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO agent_run (
                  run_id, question, metric_id, target_date, status, error_code,
                  total_tokens, total_latency_ms, token_breakdown, created_at, finished_at
                )
                VALUES (
                  'run-1', 'why', 'gmv', '2026-06-05', 'succeeded', NULL,
                  NULL, NULL, NULL, '2026-06-15 12:00:00', '2026-06-15 12:10:00'
                )
                """
            )
        )
        for memory_id, payload in [
            ("m-treatment-used", {"metric_id": "gmv", "eval_suites": ["memory-treatment"]}),
            ("m-treatment-unused", {"metric_id": "gmv", "eval_suites": ["memory-treatment"]}),
            ("m-common", {"metric_id": "gmv"}),
        ]:
            conn.execute(
                text(
                    """
                    INSERT INTO memory_record (
                      memory_id, layer, mem_key, payload, confidence, source,
                      version, ttl_days, created_at
                    )
                    VALUES (
                      :memory_id, 'case', 'gmv|run', :payload,
                      0.9, 'test', 1, 30, '2026-06-15 11:30:00'
                    )
                    """
                ),
                {"memory_id": memory_id, "payload": json.dumps(payload)},
            )
        conn.execute(
            text(
                """
                INSERT INTO trace_step (
                  step_id, run_id, seq, node, action, input_summary, output_summary,
                  error_code, latency_ms, token_usage, created_at
                )
                VALUES (
                  'step-1', 'run-1', 1, 'memory_read', 'read_priors', '{}', :output_summary,
                  NULL, 0, NULL, '2026-06-15 12:01:00'
                )
                """
            ),
            {
                "output_summary": json.dumps(
                    {"hits": [{"memory_id": "m-treatment-used"}, {"memory_id": "m-common"}]}
                )
            },
        )

    records = repo.get_memory_records_for_run("run-1")

    memory_ids = {row["memory_id"] for row in records}
    assert memory_ids == {"m-treatment-used", "m-common"}
    assert "m-treatment-unused" not in memory_ids
    repo.close()


class _FlakyWriteEngine:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.attempts = 0

    def begin(self) -> _FlakyWriteConnection:
        return _FlakyWriteConnection(self)


class _FailOnceThenRealEngine:
    def __init__(self, engine, failure: Exception) -> None:
        self.engine = engine
        self.failure = failure
        self.begin_attempts = 0

    def begin(self):
        self.begin_attempts += 1
        if self.failure is not None:
            failure = self.failure
            self.failure = None
            return _FailingBeginConnection(failure)
        return self.engine.begin()

    def connect(self):
        return self.engine.connect()


class _ApplyThenFailEngine:
    def __init__(self, engine, failure: Exception) -> None:
        self.engine = engine
        self.failure = failure

    def begin(self):
        return _ApplyThenFailConnection(self.engine, self.failure)

    def connect(self):
        return self.engine.connect()


class _FailingBeginConnection:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def execute(self, statement, params):
        raise self.failure


class _ApplyThenFailConnection:
    def __init__(self, engine, failure: Exception) -> None:
        self.engine = engine
        self.failure = failure
        self._ctx = None
        self._conn = None

    def __enter__(self):
        self._ctx = self.engine.begin()
        self._conn = self._ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        assert self._ctx is not None
        return self._ctx.__exit__(None, None, None)

    def execute(self, statement, params):
        assert self._conn is not None
        self._conn.execute(statement, params)
        raise self.failure


class _FlakyWriteConnection:
    def __init__(self, engine: _FlakyWriteEngine) -> None:
        self.engine = engine

    def __enter__(self) -> _FlakyWriteConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def execute(self, statement, params):
        self.engine.attempts += 1
        if self.engine.failures:
            raise self.engine.failures.pop(0)
        return None


def _create_trace_step_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE trace_step (
                  step_id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  seq INTEGER NOT NULL,
                  node TEXT NOT NULL,
                  action TEXT,
                  input_summary TEXT,
                  output_summary TEXT,
                  error_code TEXT,
                  latency_ms INTEGER NOT NULL DEFAULT 0,
                  token_usage TEXT,
                  created_at DATETIME NOT NULL
                )
                """
            )
        )


def _create_agent_run_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE agent_run (
                  run_id TEXT PRIMARY KEY,
                  question TEXT NOT NULL,
                  metric_id TEXT,
                  target_date DATE NOT NULL,
                  status TEXT NOT NULL,
                  error_code TEXT,
                  runtime_version INTEGER NOT NULL DEFAULT 3,
                  total_tokens INTEGER,
                  total_latency_ms INTEGER,
                  token_breakdown TEXT,
                  created_at DATETIME NOT NULL,
                  finished_at DATETIME
                )
                """
            )
        )


def _create_eval_run_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE eval_run (
                  eval_id TEXT PRIMARY KEY,
                  created_at DATETIME NOT NULL,
                  summary TEXT NOT NULL
                )
                """
            )
        )


def _create_eval_case_result_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE eval_case_result (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  eval_id TEXT NOT NULL,
                  case_id TEXT NOT NULL,
                  intent_ok INTEGER NOT NULL,
                  anomaly_ok INTEGER NOT NULL,
                  top1_ok INTEGER NOT NULL,
                  top3_ok INTEGER NOT NULL,
                  evidence_coverage REAL NOT NULL,
                  sql_safe INTEGER NOT NULL,
                  reflection_repair_ok INTEGER NOT NULL,
                  detail TEXT NOT NULL,
                  UNIQUE(eval_id, case_id)
                )
                """
            )
        )


def _create_sql_audit_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE sql_audit (
                  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  audit_key TEXT UNIQUE,
                  run_id TEXT NOT NULL,
                  sql_text TEXT NOT NULL,
                  sql_hash TEXT NOT NULL,
                  guard_status TEXT NOT NULL,
                  guard_errors TEXT,
                  row_count INTEGER,
                  latency_ms INTEGER,
                  created_at DATETIME NOT NULL
                )
                """
            )
        )


def _create_memory_record_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE memory_record (
                  memory_id TEXT PRIMARY KEY,
                  layer TEXT NOT NULL,
                  mem_key TEXT NOT NULL,
                  payload TEXT NOT NULL,
                  confidence REAL NOT NULL,
                  source TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  ttl_days INTEGER NOT NULL,
                  created_at DATETIME NOT NULL
                )
                """
            )
        )


def _operational_error(errno: int, message: str) -> OperationalError:
    return OperationalError("INSERT INTO trace_step", {}, _MysqlError(errno, message))


def _connection_invalidated_error() -> DBAPIError:
    return DBAPIError(
        "INSERT INTO trace_step",
        {},
        _MysqlError(0, "connection already closed"),
        connection_invalidated=True,
    )


class _MysqlError(Exception):
    def __init__(self, errno: int, message: str) -> None:
        super().__init__(errno, message)
        self.args = (errno, message)


def _trace_step_row() -> dict[str, object]:
    return {
        "step_id": "trace-1",
        "run_id": "run-1",
        "seq": 1,
        "node": "tool_call",
        "action": "detect_anomaly",
        "input_summary": {},
        "output_summary": {},
        "error_code": None,
        "latency_ms": 0,
        "token_usage": None,
        "created_at": datetime(2026, 6, 8, 12, 0, 0),
    }

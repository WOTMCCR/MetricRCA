from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from metric_rca.config.settings import Settings
from metric_rca.domain.models import SQLPlan
from metric_rca.guardrails.renderer import is_renderer_signed
from metric_rca.guardrails.sql_guard import guard_sql, is_guard_signed


@dataclass(frozen=True)
class QueryResult:
    rows: list[dict[str, Any]]
    row_count: int
    latency_ms: int


class MetricRepository:
    def __init__(
        self,
        *,
        readonly_engine: Engine,
        audit_engine: Engine,
        statement_timeout_ms: int,
    ) -> None:
        self._readonly_engine = readonly_engine
        self._audit_engine = audit_engine
        self._statement_timeout_ms = statement_timeout_ms

    @classmethod
    def from_settings(cls, settings: Settings) -> MetricRepository:
        return cls(
            readonly_engine=create_engine(str(settings.readonly_db_dsn), pool_pre_ping=True),
            audit_engine=create_engine(str(settings.db_dsn), pool_pre_ping=True),
            statement_timeout_ms=settings.statement_timeout_ms,
        )

    def execute_plan(self, plan: SQLPlan, *, run_id: str) -> QueryResult:
        if plan.guard_status != "passed":
            raise ValueError("SQL_GUARD_REJECTED: repository executes only passed SQLPlan")
        expected_hash = hashlib.sha256(plan.sql.encode("utf-8")).hexdigest()
        if plan.sql_hash != expected_hash:
            raise ValueError("SQL_PLAN_INVALID: sql_hash does not match sql")
        if not is_renderer_signed(plan):
            raise ValueError("SQL_PLAN_INVALID: missing renderer provenance")
        if not is_guard_signed(plan):
            raise ValueError("SQL_PLAN_INVALID: missing guard provenance")
        guarded = guard_sql(plan)
        if guarded.guard_status != "passed" or guarded.sql_hash != plan.sql_hash:
            raise ValueError("SQL_GUARD_REJECTED: repository revalidation failed")

        started = time.perf_counter()
        try:
            with self._readonly_engine.connect() as conn:
                conn.execute(
                    text("SET SESSION max_execution_time = :timeout_ms"),
                    {"timeout_ms": self._statement_timeout_ms},
                )
                result = conn.execute(text(plan.sql), plan.params)
                rows = [dict(row) for row in result.mappings().all()]
        except SQLAlchemyError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self._write_audit(
                run_id=run_id,
                plan=plan,
                row_count=None,
                latency_ms=latency_ms,
            )
            raise RuntimeError("SQL_EXECUTION_FAILED") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        self._write_audit(
            run_id=run_id,
            plan=plan,
            row_count=len(rows),
            latency_ms=latency_ms,
        )
        return QueryResult(rows=rows, row_count=len(rows), latency_ms=latency_ms)

    def latest_audit(self, run_id: str) -> dict[str, Any]:
        with self._audit_engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        """
                        SELECT *
                        FROM sql_audit
                        WHERE run_id = :run_id
                        ORDER BY audit_id DESC
                        LIMIT 1
                        """
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .one()
            )
        return dict(row)

    def close(self) -> None:
        self._readonly_engine.dispose()
        self._audit_engine.dispose()

    def create_agent_run(self, row: dict[str, Any]) -> None:
        self._insert(
            """
            INSERT INTO agent_run (
              run_id, question, metric_id, target_date, status, error_code, created_at, finished_at
            )
            VALUES (
              :run_id, :question, :metric_id, :target_date, :status, :error_code, :created_at, :finished_at
            )
            """,
            row,
        )

    def create_trace_step(self, row: dict[str, Any]) -> None:
        payload = {
            **row,
            "input_summary": json.dumps(row["input_summary"]),
            "output_summary": json.dumps(row["output_summary"]),
        }
        self._insert(
            """
            INSERT INTO trace_step (
              step_id, run_id, seq, node, action, input_summary, output_summary,
              error_code, latency_ms, created_at
            )
            VALUES (
              :step_id, :run_id, :seq, :node, :action, :input_summary, :output_summary,
              :error_code, :latency_ms, :created_at
            )
            """,
            payload,
        )

    def create_evidence(self, row: dict[str, Any]) -> None:
        payload = {
            **row,
            "query_spec": json.dumps(row["query_spec"]),
            "result_summary": json.dumps(row["result_summary"]),
        }
        self._insert(
            """
            INSERT INTO evidence (
              evidence_id, run_id, query_spec, sql_text, sql_hash, guard_status,
              result_summary, data_source, created_at
            )
            VALUES (
              :evidence_id, :run_id, :query_spec, :sql_text, :sql_hash, :guard_status,
              :result_summary, :data_source, :created_at
            )
            """,
            payload,
        )

    def create_operation_task(self, row: dict[str, Any]) -> None:
        payload = {**row, "payload": json.dumps(row["payload"])}
        self._insert(
            """
            INSERT INTO operation_task (
              task_id, run_id, title, root_cause_type, payload, created_at
            )
            VALUES (
              :task_id, :run_id, :title, :root_cause_type, :payload, :created_at
            )
            """,
            payload,
        )

    def create_memory_record(self, row: dict[str, Any]) -> None:
        payload = {**row, "payload": json.dumps(row["payload"])}
        self._insert(
            """
            INSERT INTO memory_record (
              memory_id, layer, mem_key, payload, confidence, source, version, ttl_days, created_at
            )
            VALUES (
              :memory_id, :layer, :mem_key, :payload, :confidence, :source, :version, :ttl_days, :created_at
            )
            """,
            payload,
        )

    def create_eval_run(self, row: dict[str, Any]) -> None:
        payload = {**row, "summary": json.dumps(row["summary"])}
        self._insert(
            """
            INSERT INTO eval_run (eval_id, created_at, summary)
            VALUES (:eval_id, :created_at, :summary)
            """,
            payload,
        )

    def create_eval_case_result(self, row: dict[str, Any]) -> None:
        payload = {**row, "detail": json.dumps(row["detail"])}
        self._insert(
            """
            INSERT INTO eval_case_result (
              eval_id, case_id, intent_ok, anomaly_ok, top1_ok, top3_ok,
              evidence_coverage, sql_safe, reflection_repair_ok, detail
            )
            VALUES (
              :eval_id, :case_id, :intent_ok, :anomaly_ok, :top1_ok, :top3_ok,
              :evidence_coverage, :sql_safe, :reflection_repair_ok, :detail
            )
            """,
            payload,
        )

    def system_table_counts(self, predicates: dict[str, tuple[str, object]]) -> dict[str, int]:
        allowed_columns = {
            "agent_run": {"run_id"},
            "trace_step": {"step_id"},
            "evidence": {"evidence_id"},
            "operation_task": {"task_id"},
            "memory_record": {"memory_id"},
            "eval_run": {"eval_id"},
            "eval_case_result": {"eval_id", "case_id"},
        }
        counts: dict[str, int] = {}
        with self._audit_engine.connect() as conn:
            for table, (column, value) in predicates.items():
                if table not in allowed_columns:
                    raise ValueError(f"table not allowed: {table}")
                if column not in allowed_columns[table]:
                    raise ValueError(f"column not allowed for {table}: {column}")
                row = conn.execute(
                    text(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = :value"),
                    {"value": value},
                ).one()
                counts[table] = int(row.n)
        return counts

    def _insert(self, sql: str, params: dict[str, Any]) -> None:
        with self._audit_engine.begin() as conn:
            conn.execute(text(sql), params)

    def _write_audit(
        self,
        *,
        run_id: str,
        plan: SQLPlan,
        row_count: int | None,
        latency_ms: int,
    ) -> None:
        with self._audit_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO sql_audit (
                      run_id, sql_text, sql_hash, guard_status, guard_errors,
                      row_count, latency_ms, created_at
                    )
                    VALUES (
                      :run_id, :sql_text, :sql_hash, :guard_status, :guard_errors,
                      :row_count, :latency_ms, :created_at
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "sql_text": plan.sql,
                    "sql_hash": plan.sql_hash,
                    "guard_status": plan.guard_status,
                    "guard_errors": json.dumps(plan.guard_errors),
                    "row_count": row_count,
                    "latency_ms": latency_ms,
                    "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
                },
            )

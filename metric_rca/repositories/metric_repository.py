"""MetricRepository: 取数链路的终点——只执行"通过且可溯源"的 SQLPlan , 并负责所有审计/系统表写入。

两套引擎职责分离：
  - readonly_engine(只读账号): 执行业务查询 , DB 层第二道防线；
  - audit_engine(应用账号) :写 sql_audit 与各系统表。

执行前的"五重门禁"是本仓库的安全核心: guard_status=passed → sql_hash 匹配 → 渲染器签名有效 →
守卫签名有效 → 再 guard 一次复核。任何一关不过即抛 typed error, 绝不执行——这把
"SQLGuard 不可旁路 / 危险 SQL 不可执行"做成了结构上不可绕过。

对应 docs/COMPLIANCE_MATRIX.md 第 10 行; docs/MetricRCA.md §11 执行层、§9 系统表。
"""

from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError, TimeoutError as SQLAlchemyTimeoutError

from metric_rca.config.settings import Settings
from metric_rca.domain.models import SQLPlan
from metric_rca.guardrails.renderer import is_renderer_signed
from metric_rca.guardrails.sql_guard import guard_sql, is_guard_signed

REPOSITORY_POOL_SIZE = 1
REPOSITORY_MAX_OVERFLOW = 1
REPOSITORY_POOL_TIMEOUT_SECONDS = 30
SYSTEM_WRITE_MAX_ATTEMPTS = 10
SYSTEM_WRITE_RETRY_DELAY_SECONDS = 0.2
TRANSIENT_SYSTEM_WRITE_ERRNOS = frozenset({1040, 1205, 1213, 2006, 2013})


@dataclass(frozen=True)
class QueryResult:
    """一次执行的结果：行数据 + 行数 + 时延（毫秒）。"""

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
        # 从配置构造：只读 DSN 跑业务查询，应用 DSN 写系统表；pool_pre_ping 防 MySQL 空闲断连。
        return cls(
            readonly_engine=create_engine(
                str(settings.readonly_db_dsn),
                pool_pre_ping=True,
                pool_size=REPOSITORY_POOL_SIZE,
                max_overflow=REPOSITORY_MAX_OVERFLOW,
                pool_timeout=REPOSITORY_POOL_TIMEOUT_SECONDS,
            ),
            audit_engine=create_engine(
                str(settings.db_dsn),
                pool_pre_ping=True,
                pool_size=REPOSITORY_POOL_SIZE,
                max_overflow=REPOSITORY_MAX_OVERFLOW,
                pool_timeout=REPOSITORY_POOL_TIMEOUT_SECONDS,
            ),
            statement_timeout_ms=settings.statement_timeout_ms,
        )

    def execute_plan(self, plan: SQLPlan, *, run_id: str) -> QueryResult:
        """执行一条 SQLPlan：先过五重门禁，再只读执行，并无论成败都写 sql_audit。"""
        # 门禁 1：必须是守卫已放行的 Plan。
        if plan.guard_status != "passed":
            raise ValueError("SQL_GUARD_REJECTED: repository executes only passed SQLPlan")
        # 门禁 2：sql_hash 必须与 sql 文本一致（防执行前被篡改）。
        expected_hash = hashlib.sha256(plan.sql.encode("utf-8")).hexdigest()
        if plan.sql_hash != expected_hash:
            raise ValueError("SQL_PLAN_INVALID: sql_hash does not match sql")
        # 门禁 3/4：必须带本进程渲染器 + 守卫的有效签名（防手工伪造 Plan 旁路）。
        if not is_renderer_signed(plan):
            raise ValueError("SQL_PLAN_INVALID: missing renderer provenance")
        if not is_guard_signed(plan):
            raise ValueError("SQL_PLAN_INVALID: missing guard provenance")
        # 门禁 5：执行前再 guard 一次复核，确保状态与 hash 仍一致。
        guarded = guard_sql(plan)
        if guarded.guard_status != "passed" or guarded.sql_hash != plan.sql_hash:
            raise ValueError("SQL_GUARD_REJECTED: repository revalidation failed")

        started = time.perf_counter()
        try:
            with self._readonly_engine.connect() as conn:
                # 会话级语句超时，防慢查拖垮连接。
                conn.execute(
                    text("SET SESSION max_execution_time = :timeout_ms"),
                    {"timeout_ms": self._statement_timeout_ms},
                )
                # 参数化执行（plan.params 为绑定参数，绝不字符串拼接）。
                result = conn.execute(text(plan.sql), plan.params)
                rows = [dict(row) for row in result.mappings().all()]
        except SQLAlchemyError as exc:
            # 执行失败：仍写一条审计（row_count=None），再抛 typed error，不静默吞错。
            latency_ms = int((time.perf_counter() - started) * 1000)
            self._write_audit(
                run_id=run_id,
                plan=plan,
                row_count=None,
                latency_ms=latency_ms,
            )
            raise RuntimeError("SQL_EXECUTION_FAILED") from exc

        # 成功：写审计（含真实 row_count / latency）。
        latency_ms = int((time.perf_counter() - started) * 1000)
        self._write_audit(
            run_id=run_id,
            plan=plan,
            row_count=len(rows),
            latency_ms=latency_ms,
        )
        return QueryResult(rows=rows, row_count=len(rows), latency_ms=latency_ms)

    def latest_audit(self, run_id: str) -> dict[str, Any]:
        """取某 run 最新一条 sql_audit（测试 / 调试用）。"""
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

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        """读取当前 run 根记录；工具层用它证明 run_id 是有效当前运行。"""
        try:
            with self._audit_engine.connect() as conn:
                row = (
                    conn.execute(
                        text(
                            """
                            SELECT run_id, question, metric_id, target_date, status, error_code, created_at, finished_at
                            FROM agent_run
                            WHERE run_id = :run_id
                            LIMIT 1
                            """
                        ),
                        {"run_id": run_id},
                    )
                    .mappings()
                    .first()
                )
        except SQLAlchemyError as exc:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc
        return dict(row) if row is not None else None

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, Any] | None:
        """读取当前 run 的 guard-passed 证据；工具层用它禁止伪造 evidence_id。"""
        try:
            with self._audit_engine.connect() as conn:
                row = (
                    conn.execute(
                        text(
                            """
                            SELECT evidence_id, run_id, query_spec, sql_text, sql_hash, guard_status,
                                   result_summary, data_source, created_at
                            FROM evidence
                            WHERE run_id = :run_id AND evidence_id = :evidence_id
                            LIMIT 1
                            """
                        ),
                        {"run_id": run_id, "evidence_id": evidence_id},
                    )
                    .mappings()
                    .first()
                )
        except SQLAlchemyError as exc:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc
        if row is None:
            return None
        try:
            return {
                **dict(row),
                "query_spec": _decode_json_column(row["query_spec"]),
                "result_summary": _decode_json_column(row["result_summary"]),
            }
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc

    def get_trace_steps(self, run_id: str) -> list[dict[str, Any]]:
        try:
            with self._audit_engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            """
                            SELECT step_id, run_id, seq, node, action, input_summary,
                                   output_summary, error_code, latency_ms, token_usage, created_at
                            FROM trace_step
                            WHERE run_id = :run_id
                            ORDER BY seq ASC
                            """
                        ),
                        {"run_id": run_id},
                    )
                    .mappings()
                    .all()
                )
            return [
                {
                    **dict(row),
                    "input_summary": _decode_json_column(row["input_summary"]),
                    "output_summary": _decode_json_column(row["output_summary"]),
                    "token_usage": _decode_json_column(row["token_usage"]) if row["token_usage"] is not None else None,
                }
                for row in rows
            ]
        except (SQLAlchemyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc

    def get_evidences(self, run_id: str) -> list[dict[str, Any]]:
        try:
            with self._audit_engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            """
                            SELECT evidence_id, run_id, query_spec, sql_text, sql_hash,
                                   guard_status, result_summary, data_source, created_at
                            FROM evidence
                            WHERE run_id = :run_id
                            ORDER BY created_at ASC, evidence_id ASC
                            """
                        ),
                        {"run_id": run_id},
                    )
                    .mappings()
                    .all()
                )
            return [
                {
                    **dict(row),
                    "query_spec": _decode_json_column(row["query_spec"]),
                    "result_summary": _decode_json_column(row["result_summary"]),
                }
                for row in rows
            ]
        except (SQLAlchemyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc

    def get_sql_audit_rows(self, run_id: str) -> list[dict[str, Any]]:
        try:
            with self._audit_engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            """
                            SELECT audit_id, run_id, sql_text, sql_hash, guard_status,
                                   guard_errors, row_count, latency_ms, created_at
                            FROM sql_audit
                            WHERE run_id = :run_id
                            ORDER BY audit_id ASC
                            """
                        ),
                        {"run_id": run_id},
                    )
                    .mappings()
                    .all()
                )
            return [
                {**dict(row), "guard_errors": _decode_json_column(row["guard_errors"])}
                for row in rows
            ]
        except (SQLAlchemyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc

    def get_operation_tasks(self, run_id: str) -> list[dict[str, Any]]:
        try:
            with self._audit_engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            """
                            SELECT task_id, run_id, title, root_cause_type, payload, created_at
                            FROM operation_task
                            WHERE run_id = :run_id
                            ORDER BY created_at ASC, task_id ASC
                            """
                        ),
                        {"run_id": run_id},
                    )
                    .mappings()
                    .all()
                )
            return [
                {**dict(row), "payload": _decode_json_column(row["payload"])}
                for row in rows
            ]
        except (SQLAlchemyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc

    def get_eval_run(self, eval_id: str) -> dict[str, Any] | None:
        try:
            with self._audit_engine.connect() as conn:
                row = (
                    conn.execute(
                        text(
                            """
                            SELECT eval_id, created_at, summary
                            FROM eval_run
                            WHERE eval_id = :eval_id
                            LIMIT 1
                            """
                        ),
                        {"eval_id": eval_id},
                    )
                    .mappings()
                    .first()
                )
            if row is None:
                return None
            return {**dict(row), "summary": _decode_json_column(row["summary"])}
        except (SQLAlchemyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc

    def get_eval_case_results(self, eval_id: str) -> list[dict[str, Any]]:
        try:
            with self._audit_engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            """
                            SELECT id, eval_id, case_id, intent_ok, anomaly_ok, top1_ok,
                                   top3_ok, evidence_coverage, sql_safe,
                                   reflection_repair_ok, detail
                            FROM eval_case_result
                            WHERE eval_id = :eval_id
                            ORDER BY id ASC
                            """
                        ),
                        {"eval_id": eval_id},
                    )
                    .mappings()
                    .all()
                )
            return [
                {**dict(row), "detail": _decode_json_column(row["detail"])}
                for row in rows
            ]
        except (SQLAlchemyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc

    def get_ground_truth_cases(self, case_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not case_ids:
            return {}

        placeholders = ", ".join(f":case_id_{index}" for index, _ in enumerate(case_ids))
        params = {f"case_id_{index}": case_id for index, case_id in enumerate(case_ids)}

        try:
            with self._audit_engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            f"""
                            SELECT case_id, business_date, metric_id, expected_anomaly,
                                   root_cause_type, dimension, element
                            FROM anomaly_ground_truth
                            WHERE case_id IN ({placeholders})
                            """
                        ),
                        params,
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as exc:
            raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc

        return {str(row["case_id"]): dict(row) for row in rows}

    def close(self) -> None:
        # 显式释放连接池，保证 -W error::ResourceWarning 下无未关闭连接告警。
        self._readonly_engine.dispose()
        self._audit_engine.dispose()

    # —— 系统表写入：每个方法把 dict 中的嵌套结构 json.dumps 后参数化插入 ——
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

    def update_agent_run_context(self, *, run_id: str, metric_id: str, target_date: Any) -> None:
        self._write(
            """
            UPDATE agent_run
            SET metric_id = :metric_id, target_date = :target_date
            WHERE run_id = :run_id
            """,
            {"run_id": run_id, "metric_id": metric_id, "target_date": target_date},
        )

    def finish_agent_run(
        self,
        *,
        run_id: str,
        status: str,
        error_code: str | None,
        finished_at: datetime,
    ) -> None:
        self._write(
            """
            UPDATE agent_run
            SET status = :status, error_code = :error_code, finished_at = :finished_at
            WHERE run_id = :run_id
            """,
            {
                "run_id": run_id,
                "status": status,
                "error_code": error_code,
                "finished_at": finished_at,
            },
        )

    def create_trace_step(self, row: dict[str, Any]) -> None:
        # input/output_summary 是 JSON 列，序列化后写入。
        payload = {
            **row,
            "input_summary": json.dumps(row["input_summary"]),
            "output_summary": json.dumps(row["output_summary"]),
            "token_usage": json.dumps(row.get("token_usage")) if row.get("token_usage") is not None else None,
        }
        self._insert(
            """
            INSERT INTO trace_step (
              step_id, run_id, seq, node, action, input_summary, output_summary,
              error_code, latency_ms, token_usage, created_at
            )
            VALUES (
              :step_id, :run_id, :seq, :node, :action, :input_summary, :output_summary,
              :error_code, :latency_ms, :token_usage, :created_at
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

    def update_evidence_result_summary(self, *, run_id: str, evidence_id: str, result_summary: dict[str, Any]) -> None:
        self._write(
            """
            UPDATE evidence
            SET result_summary = :result_summary
            WHERE run_id = :run_id AND evidence_id = :evidence_id
            """,
            {
                "run_id": run_id,
                "evidence_id": evidence_id,
                "result_summary": json.dumps(result_summary),
            },
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
        """按 (列=值) 统计系统表行数。表名/列名走白名单——即便是内部计数也不拼任意标识符。"""
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
                # 表名/列名已过白名单，值仍走绑定参数。
                row = conn.execute(
                    text(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = :value"),
                    {"value": value},
                ).one()
                counts[table] = int(row.n)
        return counts

    def _insert(self, sql: str, params: dict[str, Any]) -> None:
        # 系统表写入统一走应用账号事务（begin() 自动提交/回滚）。
        self._write(sql, params)

    def _write(self, sql: str, params: dict[str, Any]) -> None:
        # 系统表写入统一走应用账号事务（begin() 自动提交/回滚）。
        for attempt in range(1, SYSTEM_WRITE_MAX_ATTEMPTS + 1):
            try:
                with self._audit_engine.begin() as conn:
                    conn.execute(text(sql), params)
                return
            except SQLAlchemyError as exc:
                if attempt < SYSTEM_WRITE_MAX_ATTEMPTS and _is_transient_system_write_error(exc):
                    time.sleep(SYSTEM_WRITE_RETRY_DELAY_SECONDS * attempt)
                    continue
                raise RuntimeError(f"SYSTEM_TABLE_WRITE_FAILED: {type(exc).__name__}: {exc}") from exc

    def _write_audit(
        self,
        *,
        run_id: str,
        plan: SQLPlan,
        row_count: int | None,
        latency_ms: int,
    ) -> None:
        # 每条执行的 SQL 都落 sql_audit：sql 文本 / hash / 守卫状态 / 守卫错误 / 行数 / 时延。
        self._insert(
            """
            INSERT INTO sql_audit (
              run_id, sql_text, sql_hash, guard_status, guard_errors,
              row_count, latency_ms, created_at
            )
            VALUES (
              :run_id, :sql_text, :sql_hash, :guard_status, :guard_errors,
              :row_count, :latency_ms, :created_at
            )
            """,
            {
                "run_id": run_id,
                "sql_text": plan.sql,
                "sql_hash": plan.sql_hash,
                "guard_status": plan.guard_status,
                "guard_errors": json.dumps(plan.guard_errors),
                "row_count": row_count,
                "latency_ms": latency_ms,
                # 系统时间戳用 UTC（去 tzinfo 以匹配 DATETIME 列）。
                "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
            },
        )


def _decode_json_column(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _is_transient_system_write_error(exc: SQLAlchemyError) -> bool:
    if isinstance(exc, SQLAlchemyTimeoutError):
        return True
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ())
    if not args:
        return False
    try:
        errno = int(args[0])
    except (TypeError, ValueError):
        return False
    return errno in TRANSIENT_SYSTEM_WRITE_ERRNOS

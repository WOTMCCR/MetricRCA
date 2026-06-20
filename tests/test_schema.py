from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, text

from metric_rca.config.settings import get_settings
from metric_rca.data.seed_data import _ensure_p6_schema


EXPECTED_TABLES = {
    "dim_product",
    "dim_user",
    "fact_order",
    "fact_traffic",
    "fact_inventory",
    "fact_campaign",
    "fact_customer_ticket",
    "metric_definition",
    "anomaly_ground_truth",
    "agent_run",
    "trace_step",
    "evidence",
    "sql_audit",
    "operation_task",
    "memory_record",
    "eval_run",
    "eval_case_result",
}

EXPECTED_COLUMNS = {
    "dim_product": {
        "product_id": "int",
        "product_name": "varchar",
        "category": "varchar",
        "price": "decimal",
    },
    "dim_user": {
        "user_id": "int",
        "reg_date": "date",
        "city": "varchar",
    },
    "fact_order": {
        "order_id": "bigint",
        "business_date": "date",
        "user_id": "int",
        "product_id": "int",
        "channel": "varchar",
        "device": "varchar",
        "order_amount": "decimal",
        "is_paid": "tinyint",
        "is_refunded": "tinyint",
        "refund_amount": "decimal",
    },
    "fact_traffic": {
        "business_date": "date",
        "channel": "varchar",
        "device": "varchar",
        "product_id": "int",
        "uv": "int",
        "pv": "int",
        "add_cart_cnt": "int",
        "pay_user_cnt": "int",
    },
    "fact_inventory": {
        "business_date": "date",
        "product_id": "int",
        "warehouse": "varchar",
        "stockout_hours": "decimal",
        "avail_hours": "decimal",
    },
    "fact_campaign": {
        "business_date": "date",
        "campaign_id": "int",
        "channel": "varchar",
        "spend": "decimal",
        "clicks": "int",
        "impressions": "int",
    },
    "fact_customer_ticket": {
        "ticket_id": "bigint",
        "business_date": "date",
        "product_id": "int",
        "ticket_type": "varchar",
        "is_complaint": "tinyint",
    },
    "metric_definition": {
        "metric_id": "varchar",
        "display_name": "varchar",
        "formula": "varchar",
        "metric_family": "varchar",
        "numerator_sql_fragment": "varchar",
        "denominator_sql_fragment": "varchar",
        "higher_is_better": "tinyint",
        "source_table": "varchar",
        "allowed_dimensions": "varchar",
    },
    "anomaly_ground_truth": {
        "case_id": "varchar",
        "scenario_id": "varchar",
        "split": "varchar",
        "seed": "int",
        "profile": "varchar",
        "business_date": "date",
        "metric_id": "varchar",
        "expected_anomaly": "tinyint",
        "root_cause_type": "varchar",
        "dimension": "varchar",
        "element": "varchar",
        "root_causes": "json",
        "confounders": "json",
        "expected_behavior": "json",
    },
    "agent_run": {
        "run_id": "varchar",
        "question": "varchar",
        "metric_id": "varchar",
        "target_date": "date",
        "status": "varchar",
        "error_code": "varchar",
        "runtime_version": "int",
        "total_tokens": "int",
        "total_latency_ms": "int",
        "token_breakdown": "json",
        "created_at": "datetime",
        "finished_at": "datetime",
    },
    "trace_step": {
        "step_id": "varchar",
        "run_id": "varchar",
        "seq": "int",
        "node": "varchar",
        "action": "varchar",
        "input_summary": "json",
        "output_summary": "json",
        "error_code": "varchar",
        "latency_ms": "int",
        "token_usage": "json",
        "created_at": "datetime",
    },
    "evidence": {
        "evidence_pk": "bigint",
        "evidence_id": "varchar",
        "run_id": "varchar",
        "alias": "varchar",
        "query_spec": "json",
        "sql_text": "text",
        "sql_hash": "char",
        "guard_status": "varchar",
        "result_summary": "json",
        "data_source": "varchar",
        "created_at": "datetime",
    },
    "sql_audit": {
        "audit_id": "bigint",
        "audit_key": "varchar",
        "run_id": "varchar",
        "sql_text": "text",
        "sql_hash": "char",
        "guard_status": "varchar",
        "guard_errors": "json",
        "row_count": "int",
        "latency_ms": "int",
        "created_at": "datetime",
    },
    "operation_task": {
        "task_id": "varchar",
        "run_id": "varchar",
        "title": "varchar",
        "root_cause_type": "varchar",
        "payload": "json",
        "created_at": "datetime",
    },
    "memory_record": {
        "memory_id": "varchar",
        "layer": "varchar",
        "mem_key": "varchar",
        "payload": "json",
        "confidence": "decimal",
        "source": "varchar",
        "version": "int",
        "ttl_days": "int",
        "created_at": "datetime",
    },
    "eval_run": {
        "eval_id": "varchar",
        "created_at": "datetime",
        "summary": "json",
    },
    "eval_case_result": {
        "id": "bigint",
        "eval_id": "varchar",
        "case_id": "varchar",
        "intent_ok": "tinyint",
        "anomaly_ok": "tinyint",
        "top1_ok": "tinyint",
        "top3_ok": "tinyint",
        "evidence_coverage": "decimal",
        "sql_safe": "tinyint",
        "reflection_repair_ok": "tinyint",
        "detail": "json",
    },
}


def test_schema_has_exact_phase1_tables_columns_and_indexes() -> None:
    settings = get_settings()
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            tables = {
                row.TABLE_NAME
                for row in conn.execute(
                    text(
                        """
                        SELECT TABLE_NAME
                        FROM information_schema.TABLES
                        WHERE TABLE_SCHEMA = DATABASE()
                        """
                    )
                )
            }
            assert EXPECTED_TABLES <= tables
            assert "fact_ticket" not in tables

            columns = {
                (row.TABLE_NAME, row.COLUMN_NAME): row.DATA_TYPE
                for row in conn.execute(
                    text(
                        """
                        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
                        FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE()
                        """
                    )
                )
            }
            for table, expected_columns in EXPECTED_COLUMNS.items():
                actual_for_table = {
                    column
                    for actual_table, column in columns
                    if actual_table == table
                }
                assert actual_for_table == set(expected_columns), table
                for column, data_type in expected_columns.items():
                    assert columns[(table, column)] == data_type

            metric_family_default = conn.execute(
                text(
                    """
                    SELECT COLUMN_DEFAULT
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'metric_definition'
                      AND COLUMN_NAME = 'metric_family'
                    """
                )
            ).scalar_one()
            assert metric_family_default is None

            primary_keys = {
                row.TABLE_NAME
                for row in conn.execute(
                    text(
                        """
                        SELECT TABLE_NAME
                        FROM information_schema.TABLE_CONSTRAINTS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND CONSTRAINT_TYPE = 'PRIMARY KEY'
                        """
                    )
                )
            }
            assert EXPECTED_TABLES <= primary_keys

            indexes = {
                (row.TABLE_NAME, row.INDEX_NAME)
                for row in conn.execute(
                    text(
                        """
                        SELECT TABLE_NAME, INDEX_NAME
                        FROM information_schema.STATISTICS
                        WHERE TABLE_SCHEMA = DATABASE()
                        """
                    )
                )
            }
            assert ("fact_order", "idx_date_channel") in indexes
            assert ("fact_customer_ticket", "idx_date_product") in indexes
            assert ("memory_record", "idx_layer_key") in indexes
            assert ("sql_audit", "uq_audit_key") in indexes
            assert ("evidence", "uq_evidence_id") in indexes
            assert ("evidence", "uq_evidence_run_alias") in indexes
            assert ("eval_case_result", "idx_eval") in indexes
            assert ("eval_case_result", "uq_eval_case") in indexes

            check_constraints = {
                (row.TABLE_NAME, row.CONSTRAINT_NAME)
                for row in conn.execute(
                    text(
                        """
                        SELECT TABLE_NAME, CONSTRAINT_NAME
                        FROM information_schema.TABLE_CONSTRAINTS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND CONSTRAINT_TYPE = 'CHECK'
                        """
                    )
                )
            }
            for constraint in {
                "chk_eval_case_result_intent_ok",
                "chk_eval_case_result_anomaly_ok",
                "chk_eval_case_result_top1_ok",
                "chk_eval_case_result_top3_ok",
                "chk_eval_case_result_sql_safe",
                "chk_eval_case_result_reflection_repair_ok",
                "chk_eval_case_result_evidence_coverage",
            }:
                assert ("eval_case_result", constraint) in check_constraints
    finally:
        engine.dispose()


def test_seed_schema_migration_adds_root_causes_column_when_missing() -> None:
    conn = _MigrationConn(missing_columns={("anomaly_ground_truth", "root_causes")})

    _ensure_p6_schema(conn)

    assert (
        "ALTER TABLE anomaly_ground_truth ADD COLUMN root_causes JSON NULL AFTER element"
        in conn.ddl_statements
    )


def test_seed_schema_migration_upgrades_empty_legacy_evidence_table() -> None:
    conn = _MigrationConn(
        missing_columns={("evidence", "evidence_pk"), ("evidence", "alias")}
    )

    _ensure_p6_schema(conn)

    migration = next(
        statement
        for statement in conn.ddl_statements
        if "ADD COLUMN evidence_pk BIGINT UNSIGNED" in statement
    )
    assert "MODIFY COLUMN evidence_id VARCHAR(192) NOT NULL" in migration
    assert "ADD COLUMN alias VARCHAR(96) NOT NULL AFTER run_id" in migration
    assert "ADD UNIQUE KEY uq_evidence_run_alias (run_id, alias)" in migration


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _MigrationConn:
    def __init__(self, *, missing_columns: set[tuple[str, str]]) -> None:
        self._missing_columns = missing_columns
        self.ddl_statements: list[str] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _ScalarResult:
        sql = str(statement)
        params = params or {}
        if "information_schema.COLUMNS" in sql and "COUNT(*)" in sql:
            table = str(params.get("table") or "")
            column = str(params.get("column") or "")
            return _ScalarResult(0 if (table, column) in self._missing_columns else 1)
        if "COLUMN_DEFAULT" in sql:
            return _ScalarResult(None)
        if "information_schema.STATISTICS" in sql:
            return _ScalarResult(1)
        if "information_schema.TABLE_CONSTRAINTS" in sql:
            return _ScalarResult(1)
        self.ddl_statements.append(sql)
        return _ScalarResult(None)

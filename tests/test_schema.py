from __future__ import annotations

from sqlalchemy import create_engine, text

from metric_rca.config.settings import get_settings


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
        "numerator_sql_fragment": "varchar",
        "denominator_sql_fragment": "varchar",
        "higher_is_better": "tinyint",
        "source_table": "varchar",
        "allowed_dimensions": "varchar",
    },
    "anomaly_ground_truth": {
        "case_id": "varchar",
        "business_date": "date",
        "metric_id": "varchar",
        "expected_anomaly": "tinyint",
        "root_cause_type": "varchar",
        "dimension": "varchar",
        "element": "varchar",
    },
    "agent_run": {
        "run_id": "varchar",
        "question": "varchar",
        "metric_id": "varchar",
        "target_date": "date",
        "status": "varchar",
        "error_code": "varchar",
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
        "created_at": "datetime",
    },
    "evidence": {
        "evidence_id": "varchar",
        "run_id": "varchar",
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
            assert ("eval_case_result", "idx_eval") in indexes
    finally:
        engine.dispose()

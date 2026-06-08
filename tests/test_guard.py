from __future__ import annotations

from datetime import date
import hashlib

from metric_rca.domain.models import SQLPlan
from metric_rca.guardrails.query_spec import build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql


def assert_rejected(sql: str, contains: str) -> None:
    plan = guard_sql(sql)
    assert plan.guard_status == "rejected"
    assert any(contains in err for err in plan.guard_errors), plan.guard_errors


def test_sql_guard_rejects_documented_dangerous_sql() -> None:
    assert_rejected("SELECT * FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1000", "SELECT *")
    assert_rejected("SELECT order_amount FROM fact_order; DROP TABLE x", "multiple statements")
    assert_rejected("DELETE FROM fact_order", "not a read-only SELECT")
    assert_rejected("INSERT INTO fact_order (order_id) VALUES (1)", "not a read-only SELECT")
    assert_rejected("UPDATE fact_order SET order_amount = 1", "not a read-only SELECT")
    assert_rejected("DROP TABLE fact_order", "not a read-only SELECT")
    assert_rejected("ALTER TABLE fact_order ADD COLUMN x INT", "not a read-only SELECT")
    assert_rejected("CREATE TABLE x (id INT)", "not a read-only SELECT")
    assert_rejected("PRAGMA table_info(fact_order)", "not a read-only SELECT")
    assert_rejected("SELECT amount FROM secret_table WHERE business_date = '2026-06-05' LIMIT 1000", "table not allowed")
    assert_rejected("SELECT order_amount FROM fact_order LIMIT 1000", "missing business_date")
    assert_rejected("SELECT order_amount FROM fact_order WHERE business_date LIMIT 1000", "missing business_date")
    assert_rejected("SELECT order_amount FROM fact_order WHERE business_date = business_date LIMIT 1000", "missing business_date")
    assert_rejected("SELECT order_amount FROM fact_order WHERE business_date IS NOT NULL LIMIT 1000", "missing business_date")
    assert_rejected("SELECT order_amount FROM fact_order WHERE business_date = '2026-06-05'", "missing LIMIT")
    assert_rejected("WITH x AS (SELECT order_amount FROM fact_order WHERE business_date = '2026-06-05') SELECT order_amount FROM x LIMIT 1000", "CTE")
    assert_rejected("SELECT order_amount FROM (SELECT order_amount FROM fact_order WHERE business_date = '2026-06-05') t LIMIT 1000", "subquery")
    assert_rejected("SELECT fact_order.order_amount FROM fact_order INNER JOIN dim_product ON fact_order.user_id = dim_product.product_id WHERE fact_order.business_date = '2026-06-05' LIMIT 1000", "join not renderer-generated")
    assert_rejected("SELECT dim_product.product_id FROM fact_order INNER JOIN dim_product ON dim_product.product_id = fact_order.product_id WHERE fact_order.business_date = '2026-06-05' LIMIT 1000", "join not renderer-generated")
    assert_rejected("SELECT dim_product.category, SUM(fact_order.order_amount) AS metric_value FROM fact_order INNER JOIN dim_product ON fact_order.product_id = dim_product.product_id WHERE fact_order.business_date = '2026-06-05' GROUP BY dim_product.category LIMIT 1000", "join not renderer-generated")
    assert_rejected("SELECT password_hash FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1000", "column not allowed")
    assert_rejected("SELECT product_name FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1000", "column not allowed")
    assert_rejected("SELECT SUM(uv) FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1000", "column not allowed")
    assert_rejected("SELECT SLEEP(1) AS metric_value FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1000", "function not allowed")

    forged_renderer_plan = SQLPlan(
        sql="SELECT dim_product.category, SUM(fact_order.order_amount) AS metric_value FROM fact_order INNER JOIN dim_product ON fact_order.product_id = dim_product.product_id WHERE fact_order.business_date = '2026-06-05' GROUP BY dim_product.category LIMIT 1000",
        sql_hash="not-the-real-hash",
        renderer_signature="forged",
    )
    guarded = guard_sql(forged_renderer_plan)
    assert guarded.guard_status == "rejected"
    assert "sql_hash does not match sql" in guarded.guard_errors

    forged_sql = "SELECT dim_product.category, SUM(fact_order.order_amount) AS metric_value FROM fact_order INNER JOIN dim_product ON fact_order.product_id = dim_product.product_id WHERE fact_order.business_date = '2026-06-05' GROUP BY dim_product.category LIMIT 1000"
    forged_with_hash = SQLPlan(
        sql=forged_sql,
        sql_hash=hashlib.sha256(forged_sql.encode("utf-8")).hexdigest(),
        renderer_signature="forged",
    )
    guarded_forged_with_hash = guard_sql(forged_with_hash)
    assert guarded_forged_with_hash.guard_status == "rejected"
    assert "join not renderer-generated" in guarded_forged_with_hash.guard_errors

    rendered = SQLRenderer().render(
        build_query_spec(
            metric_id="gmv",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 5),
            group_by=["category"],
        )
    )
    tampered = rendered.model_copy(
        update={
            "sql": rendered.sql.replace("fact_order.business_date", "fact_order.business_date"),
            "params": rendered.params,
        }
    )
    tampered = tampered.model_copy(update={"sql": tampered.sql + " "})
    guarded_tampered = guard_sql(tampered)
    assert guarded_tampered.guard_status == "rejected"
    assert "sql_hash does not match sql" in guarded_tampered.guard_errors


def test_sql_guard_allows_valid_select_and_renderer_category_join() -> None:
    valid = guard_sql(
        "SELECT order_amount FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1000"
    )
    assert valid.guard_status == "passed", valid.guard_errors

    spec = build_query_spec(
        metric_id="gmv",
        start_date=date(2026, 6, 5),
        end_date=date(2026, 6, 5),
        group_by=["category"],
    )
    guarded = guard_sql(SQLRenderer().render(spec))
    assert guarded.guard_status == "passed", guarded.guard_errors


def test_sql_guard_ast_proofs_defeat_regex_shortcuts() -> None:
    assert_rejected(
        "SELECT order_amount FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1000; Dr/**/Op TABLE fact_order",
        "multiple statements",
    )
    assert_rejected(
        "SELECT t.order_amount FROM (SELECT order_amount FROM fact_order WHERE business_date = '2026-06-05' LIMIT 1) AS t LIMIT 1000",
        "subquery",
    )

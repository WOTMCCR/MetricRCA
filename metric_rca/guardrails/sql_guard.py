from __future__ import annotations

import hashlib
import hmac
import secrets

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from metric_rca.domain.models import SQLPlan
from metric_rca.guardrails.renderer import is_renderer_signed


_GUARD_SECRET = secrets.token_hex(32)


def _guard_signature(sql_hash: str) -> str:
    return hmac.new(_GUARD_SECRET.encode(), sql_hash.encode(), "sha256").hexdigest()


def is_guard_signed(plan: SQLPlan) -> bool:
    return plan.guard_signature == _guard_signature(plan.sql_hash)


ALLOWED_TABLES = {
    "fact_order",
    "fact_traffic",
    "fact_inventory",
    "fact_campaign",
    "fact_customer_ticket",
    "dim_product",
}
FACT_TABLES = {
    "fact_order",
    "fact_traffic",
    "fact_inventory",
    "fact_campaign",
    "fact_customer_ticket",
}
ALLOWED_JOINS = {
    ("fact_order", "dim_product", "product_id"),
    ("fact_traffic", "dim_product", "product_id"),
    ("fact_inventory", "dim_product", "product_id"),
    ("fact_customer_ticket", "dim_product", "product_id"),
}
ALLOWED_COLUMNS = {
    "fact_order": {
        "order_id",
        "business_date",
        "user_id",
        "product_id",
        "channel",
        "device",
        "order_amount",
        "is_paid",
        "is_refunded",
        "refund_amount",
    },
    "fact_traffic": {
        "business_date",
        "channel",
        "device",
        "product_id",
        "uv",
        "pv",
        "add_cart_cnt",
        "pay_user_cnt",
    },
    "fact_inventory": {
        "business_date",
        "product_id",
        "warehouse",
        "stockout_hours",
        "avail_hours",
    },
    "fact_campaign": {
        "business_date",
        "campaign_id",
        "channel",
        "spend",
        "clicks",
        "impressions",
    },
    "fact_customer_ticket": {
        "ticket_id",
        "business_date",
        "product_id",
        "ticket_type",
        "is_complaint",
    },
    "dim_product": {"product_id", "product_name", "category", "price"},
}
ALLOWED_UNQUALIFIED_COLUMNS = set().union(*ALLOWED_COLUMNS.values()) | {
    "metric_value",
    "channel",
    "category",
    "device",
    "product",
    "warehouse",
}
FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
)


def guard_sql(plan_or_sql: SQLPlan | str) -> SQLPlan:
    if isinstance(plan_or_sql, SQLPlan):
        plan = plan_or_sql
        require_renderer_join = is_renderer_signed(plan)
    else:
        sql = plan_or_sql
        plan = SQLPlan(sql=sql, sql_hash=hashlib.sha256(sql.encode("utf-8")).hexdigest())
        require_renderer_join = False

    expected_hash = hashlib.sha256(plan.sql.encode("utf-8")).hexdigest()
    if plan.sql_hash != expected_hash:
        return plan.model_copy(
            update={
                "guard_status": "rejected",
                "guard_errors": ["sql_hash does not match sql"],
                "guard_signature": None,
            }
        )

    errors = _validate_sql(plan.sql, require_renderer_join=require_renderer_join)
    if errors:
        return plan.model_copy(
            update={"guard_status": "rejected", "guard_errors": errors, "guard_signature": None}
        )
    return plan.model_copy(
        update={
            "guard_status": "passed",
            "guard_errors": [],
            "guard_signature": _guard_signature(plan.sql_hash),
        }
    )


def _validate_sql(sql: str, *, require_renderer_join: bool) -> list[str]:
    stripped = sql.strip().rstrip(";")
    if ";" in stripped:
        return ["multiple statements not allowed"]

    try:
        statements = [stmt for stmt in sqlglot.parse(sql, read="mysql") if stmt is not None]
    except ParseError as exc:
        return [f"parse error: {exc}"]

    if len(statements) != 1:
        return ["multiple statements not allowed"]

    ast = statements[0]
    if not isinstance(ast, exp.Select):
        return ["not a read-only SELECT"]

    errors: list[str] = []
    for node in ast.walk():
        if isinstance(node, FORBIDDEN):
            errors.append(f"forbidden {type(node).__name__}")
        if isinstance(node, exp.Anonymous):
            errors.append(f"function not allowed: {node.name}")

    if any(isinstance(star, exp.Star) for star in ast.find_all(exp.Star)):
        errors.append("SELECT * not allowed")

    if ast.args.get("with") is not None or any(isinstance(node, exp.With) for node in ast.walk()):
        errors.append("CTE not allowed")

    if any(isinstance(node, exp.Subquery) for node in ast.walk()):
        errors.append("subquery not allowed")

    physical_tables = _physical_tables(ast, errors)
    fact_tables = physical_tables & FACT_TABLES
    if not fact_tables:
        errors.append("missing fact table")

    errors.extend(_join_errors(ast, require_renderer_join=require_renderer_join))
    errors.extend(_column_errors(ast, physical_tables))

    if fact_tables and not _has_fact_business_date_filter(ast, fact_tables):
        errors.append("missing business_date filter")

    if ast.args.get("limit") is None:
        errors.append("missing LIMIT")

    return errors


def _physical_tables(ast: exp.Expression, errors: list[str]) -> set[str]:
    tables: set[str] = set()
    for table in ast.find_all(exp.Table):
        table_name = table.name
        if table_name not in ALLOWED_TABLES:
            errors.append(f"table not allowed: {table_name}")
        tables.add(table_name)
    return tables


def _join_errors(ast: exp.Expression, *, require_renderer_join: bool) -> list[str]:
    errors: list[str] = []
    from_expr = ast.find(exp.From)
    base_table = from_expr.this.name if from_expr and isinstance(from_expr.this, exp.Table) else None

    for join in ast.find_all(exp.Join):
        if not require_renderer_join:
            errors.append("join not renderer-generated")
            continue
        if join.args.get("kind") != "INNER":
            errors.append("join not allowed: only INNER JOIN is allowed")
            continue
        joined_table = join.this.name if isinstance(join.this, exp.Table) else None
        on_expr = join.args.get("on")
        if not base_table or not joined_table or not isinstance(on_expr, exp.EQ):
            errors.append("join not allowed")
            continue
        left = on_expr.this
        right = on_expr.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            errors.append("join not allowed")
            continue
        join_key = (base_table, joined_table, left.name)
        reverse_key = (base_table, joined_table, right.name)
        valid = (
            join_key in ALLOWED_JOINS
            and left.table == base_table
            and right.table == joined_table
            and left.name == right.name
        )
        if not valid:
            errors.append("join not allowed")
    return errors


def _column_errors(ast: exp.Expression, physical_tables: set[str]) -> list[str]:
    errors: list[str] = []
    for column in ast.find_all(exp.Column):
        table = column.table
        if table:
            allowed = ALLOWED_COLUMNS.get(table, set())
        else:
            if column.name == "metric_value":
                continue
            candidate_tables = [
                table_name
                for table_name in physical_tables
                if column.name in ALLOWED_COLUMNS.get(table_name, set())
            ]
            if len(candidate_tables) != 1:
                errors.append(f"column not allowed: {column.name}")
                continue
            allowed = ALLOWED_COLUMNS[candidate_tables[0]]
        if column.name not in allowed:
            errors.append(f"column not allowed: {column.name}")
    return errors


def _has_fact_business_date_filter(ast: exp.Expression, fact_tables: set[str]) -> bool:
    where = ast.args.get("where")
    if where is None:
        return False
    for node in where.walk():
        if _is_business_date_between(node, fact_tables):
            return True
        if _is_business_date_binary_comparison(node, fact_tables):
            return True
        if _is_business_date_in(node, fact_tables):
            return True
    return False


def _is_fact_business_date_column(node: exp.Expression, fact_tables: set[str]) -> bool:
    return (
        isinstance(node, exp.Column)
        and node.name == "business_date"
        and (not node.table or node.table in fact_tables)
    )


def _is_bound_value(node: exp.Expression) -> bool:
    return isinstance(node, (exp.Literal, exp.Placeholder))


def _is_business_date_between(node: exp.Expression, fact_tables: set[str]) -> bool:
    return (
        isinstance(node, exp.Between)
        and _is_fact_business_date_column(node.this, fact_tables)
        and _is_bound_value(node.args["low"])
        and _is_bound_value(node.args["high"])
    )


def _is_business_date_binary_comparison(node: exp.Expression, fact_tables: set[str]) -> bool:
    comparison_types = (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
    if not isinstance(node, comparison_types):
        return False
    left = node.this
    right = node.expression
    return (
        _is_fact_business_date_column(left, fact_tables)
        and _is_bound_value(right)
    ) or (
        _is_fact_business_date_column(right, fact_tables)
        and _is_bound_value(left)
    )


def _is_business_date_in(node: exp.Expression, fact_tables: set[str]) -> bool:
    return (
        isinstance(node, exp.In)
        and _is_fact_business_date_column(node.this, fact_tables)
        and bool(node.expressions)
        and all(_is_bound_value(item) for item in node.expressions)
    )

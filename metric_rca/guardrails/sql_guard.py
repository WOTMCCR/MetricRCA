"""SQLGuard：基于 sqlglot AST 的只读 SQL 守卫（P0：必须是 AST，不能是正则）。

为什么用 AST 而非正则：正则容易被注释、大小写、派生表等手法绕过；AST 直接看语法结构，
能稳健地判定语句类型、表/列、JOIN、子查询、CTE 等。守卫把一条 SQL 判为 passed/rejected，
通过即盖 `guard_signature`（HMAC），仓库执行前会校验这枚签名——构成"不可旁路"的最后一道闸。

JOIN 策略：只有"渲染器签名有效"的 Plan 才允许出现白名单 INNER JOIN；裸字符串 SQL 一旦含
JOIN 一律拒绝（因为它不可能来自受信渲染器）。

对应 docs/COMPLIANCE_MATRIX.md 第 9 行；docs/MetricRCA.md §11 守卫清单。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from metric_rca.domain.models import SQLPlan
from metric_rca.guardrails.renderer import is_renderer_signed


# 守卫自己的进程内密钥：与渲染器密钥独立，形成"双盖章"。
_GUARD_SECRET = secrets.token_hex(32)


def _guard_signature(sql_hash: str) -> str:
    """对 sql_hash 做 HMAC，作为"本守卫已放行"的密码学证明。"""
    return hmac.new(_GUARD_SECRET.encode(), sql_hash.encode(), "sha256").hexdigest()


def is_guard_signed(plan: SQLPlan) -> bool:
    """校验 plan 是否携带本进程守卫的有效签名。"""
    return plan.guard_signature == _guard_signature(plan.sql_hash)


# —— 白名单：表 / 事实表 / JOIN / 列 ——
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
# 允许的 JOIN：(基表, 被连表, 连接键)，且只允许 INNER JOIN、键名两侧一致。
ALLOWED_JOINS = {
    ("fact_order", "dim_product", "product_id"),
    ("fact_traffic", "dim_product", "product_id"),
    ("fact_inventory", "dim_product", "product_id"),
    ("fact_customer_ticket", "dim_product", "product_id"),
}
# 列白名单：按表逐列定义，拒绝任何未登记列（含潜在 PII / 越权列）。
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
# 未带表前缀的列（如渲染别名 metric_value、下钻别名 channel/category 等）的允许集合。
ALLOWED_UNQUALIFIED_COLUMNS = set().union(*ALLOWED_COLUMNS.values()) | {
    "metric_value",
    "channel",
    "category",
    "device",
    "product",
    "warehouse",
}
# 禁止出现的 AST 节点类型：所有改数/建表/命令类语句。
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
    """守卫入口：接受 SQLPlan 或裸 SQL 字符串，返回带 guard_status 的新 SQLPlan。

    - 传入 SQLPlan 且渲染器签名有效 → 允许白名单 JOIN（require_renderer_join=True）；
    - 传入裸字符串（测试 / 外部 SQL）→ 任何 JOIN 都拒绝（不可能来自受信渲染器）。
    """
    if isinstance(plan_or_sql, SQLPlan):
        plan = plan_or_sql
        require_renderer_join = is_renderer_signed(plan)
    else:
        sql = plan_or_sql
        plan = SQLPlan(sql=sql, sql_hash=hashlib.sha256(sql.encode("utf-8")).hexdigest())
        require_renderer_join = False

    # 防篡改：sql_hash 必须与 sql 文本一致，否则直接拒绝（且清空签名）。
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
        # 有任何违规 → rejected，附全部错误，绝不盖签名。
        return plan.model_copy(
            update={"guard_status": "rejected", "guard_errors": errors, "guard_signature": None}
        )
    # 全部通过 → passed，并盖上守卫签名（仓库执行前会校验）。
    return plan.model_copy(
        update={
            "guard_status": "passed",
            "guard_errors": [],
            "guard_signature": _guard_signature(plan.sql_hash),
        }
    )


def _validate_sql(sql: str, *, require_renderer_join: bool) -> list[str]:
    """对单条 SQL 跑全部守卫规则，返回错误列表（空列表=通过）。"""
    # 多语句快速判定：去掉首尾分号后若仍含 ';' 即多语句。
    stripped = sql.strip().rstrip(";")
    if ";" in stripped:
        return ["multiple statements not allowed"]

    # 解析为 AST；语法错误直接拒绝。
    try:
        statements = [stmt for stmt in sqlglot.parse(sql, read="mysql") if stmt is not None]
    except ParseError as exc:
        return [f"parse error: {exc}"]

    if len(statements) != 1:
        return ["multiple statements not allowed"]

    ast = statements[0]
    # 只允许只读 SELECT。
    if not isinstance(ast, exp.Select):
        return ["not a read-only SELECT"]

    errors: list[str] = []
    # 遍历整棵树：禁改数/命令节点；禁未知函数（Anonymous）。
    for node in ast.walk():
        if isinstance(node, FORBIDDEN):
            errors.append(f"forbidden {type(node).__name__}")
        if isinstance(node, exp.Anonymous):
            errors.append(f"function not allowed: {node.name}")

    # 禁 SELECT *。
    if any(isinstance(star, exp.Star) for star in ast.find_all(exp.Star)):
        errors.append("SELECT * not allowed")

    # 禁 CTE（with）。
    if ast.args.get("with") is not None or any(isinstance(node, exp.With) for node in ast.walk()):
        errors.append("CTE not allowed")

    # 禁子查询 / 派生表（sqlglot AST primer 指出 CTE/子查询里的 Table 不一定是物理表）。
    if any(isinstance(node, exp.Subquery) for node in ast.walk()):
        errors.append("subquery not allowed")

    # 表白名单 + 至少一个事实表。
    physical_tables = _physical_tables(ast, errors)
    fact_tables = physical_tables & FACT_TABLES
    if not fact_tables:
        errors.append("missing fact table")

    # JOIN 白名单 + 列白名单。
    errors.extend(_join_errors(ast, require_renderer_join=require_renderer_join))
    errors.extend(_column_errors(ast, physical_tables))

    # 事实表必须带 business_date 过滤（且过滤值是绑定参数 / 字面量，防全表扫描与注入）。
    if fact_tables and not _has_fact_business_date_filter(ast, fact_tables):
        errors.append("missing business_date filter")

    # 强制 LIMIT。
    if ast.args.get("limit") is None:
        errors.append("missing LIMIT")

    return errors


def _physical_tables(ast: exp.Expression, errors: list[str]) -> set[str]:
    """收集 AST 中出现的所有表名，非白名单表登记为错误。"""
    tables: set[str] = set()
    for table in ast.find_all(exp.Table):
        table_name = table.name
        if table_name not in ALLOWED_TABLES:
            errors.append(f"table not allowed: {table_name}")
        tables.add(table_name)
    return tables


def _join_errors(ast: exp.Expression, *, require_renderer_join: bool) -> list[str]:
    """校验每个 JOIN：必须是渲染器生成、INNER、且(基表,被连表,键)∈白名单、两侧键名一致。"""
    errors: list[str] = []
    from_expr = ast.find(exp.From)
    base_table = from_expr.this.name if from_expr and isinstance(from_expr.this, exp.Table) else None

    for join in ast.find_all(exp.Join):
        # 非渲染器来源（裸 SQL）一律拒绝 JOIN。
        if not require_renderer_join:
            errors.append("join not renderer-generated")
            continue
        # 只允许 INNER JOIN。
        if join.args.get("kind") != "INNER":
            errors.append("join not allowed: only INNER JOIN is allowed")
            continue
        joined_table = join.this.name if isinstance(join.this, exp.Table) else None
        on_expr = join.args.get("on")
        # ON 必须是简单等值连接 a.col = b.col。
        if not base_table or not joined_table or not isinstance(on_expr, exp.EQ):
            errors.append("join not allowed")
            continue
        left = on_expr.this
        right = on_expr.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            errors.append("join not allowed")
            continue
        # 连接键三元组必须在白名单，且左右表前缀与键名严格匹配。
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
    """列白名单校验。带表前缀的列查对应表白名单；无前缀列必须能在物理表中唯一归属。"""
    errors: list[str] = []
    for column in ast.find_all(exp.Column):
        table = column.table
        if table:
            # 有表前缀：直接查该表允许列。
            allowed = ALLOWED_COLUMNS.get(table, set())
        else:
            # 渲染别名 metric_value 放行。
            if column.name == "metric_value":
                continue
            # 无前缀列：必须恰好能归属到一个物理表，否则视为歧义/非法。
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
    """WHERE 中是否存在对事实表 business_date 的有效过滤（BETWEEN / 比较 / IN）。"""
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
    """节点是否为某事实表的 business_date 列（允许无表前缀）。"""
    return (
        isinstance(node, exp.Column)
        and node.name == "business_date"
        and (not node.table or node.table in fact_tables)
    )


def _is_bound_value(node: exp.Expression) -> bool:
    """过滤值必须是字面量或占位参数（杜绝把子查询/表达式当作日期条件来"骗过"守卫）。"""
    return isinstance(node, (exp.Literal, exp.Placeholder))


def _is_business_date_between(node: exp.Expression, fact_tables: set[str]) -> bool:
    """business_date BETWEEN :low AND :high 形式。"""
    return (
        isinstance(node, exp.Between)
        and _is_fact_business_date_column(node.this, fact_tables)
        and _is_bound_value(node.args["low"])
        and _is_bound_value(node.args["high"])
    )


def _is_business_date_binary_comparison(node: exp.Expression, fact_tables: set[str]) -> bool:
    """business_date =/>/>=/</<= 绑定值（任一侧为日期列均可）。"""
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
    """business_date IN (:d1, :d2, ...) 且元素均为绑定值。"""
    return (
        isinstance(node, exp.In)
        and _is_fact_business_date_column(node.this, fact_tables)
        and bool(node.expressions)
        and all(_is_bound_value(item) for item in node.expressions)
    )

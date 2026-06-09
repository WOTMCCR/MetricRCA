"""SQLRenderer：把 QuerySpec 确定性地渲染成参数化 SQL（绝不让 LLM 写 SQL）。

为什么要模板化渲染：安全（防注入/改数/越权）、可复现（同一 QuerySpec → 同一 SQL）、
可审计（sql_hash + sql_audit）。维度、JOIN、列都来自代码内白名单，调用方无法注入任意片段。

工程加固——渲染器签名（HMAC）：每条渲染产物都用进程内密钥对 sql_hash 盖一枚
`renderer_signature`。下游守卫 / 仓库据此判断"这条 SQL 确实由本渲染器生成"，从而：
  - 只对"渲染器生成的 SQL"放行白名单 INNER JOIN；
  - 阻止手工伪造的 SQLPlan 绕过取数链路。

对应 docs/COMPLIANCE_MATRIX.md 第 8 行；docs/MetricRCA.md §11 + roadmap §2.3。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from dataclasses import dataclass

from metric_rca.domain.models import QuerySpec, SQLPlan


# 进程内随机密钥：每次进程启动重新生成，签名不可跨进程伪造、也无需持久化。
_RENDERER_SECRET = secrets.token_hex(32)


def _renderer_signature(sql_hash: str) -> str:
    """对 sql_hash 做 HMAC-SHA256，作为"本渲染器产出"的密码学证明。"""
    return hmac.new(_RENDERER_SECRET.encode(), sql_hash.encode(), "sha256").hexdigest()


def is_renderer_signed(plan: SQLPlan) -> bool:
    """校验 plan 是否携带本进程渲染器的有效签名。"""
    return plan.renderer_signature == _renderer_signature(plan.sql_hash)


@dataclass(frozen=True)
class MetricTemplate:
    """单个指标的渲染模板：事实表 + 聚合表达式。"""

    fact_table: str
    expression: str


# —— 指标模板白名单：每个指标对应一个事实表与确定性的聚合口径 ——
# 注意 AOV 用 order 计数(pay_user 近似)，refund_rate=退款额/已支付额，pay_cvr=支付人数/UV。
METRIC_TEMPLATES: dict[str, MetricTemplate] = {
    "gmv": MetricTemplate(
        "fact_order",
        "SUM(CASE WHEN fact_order.is_paid = 1 THEN fact_order.order_amount ELSE 0 END)",
    ),
    "net_gmv": MetricTemplate(
        "fact_order",
        "SUM(CASE WHEN fact_order.is_paid = 1 THEN fact_order.order_amount - fact_order.refund_amount ELSE 0 END)",
    ),
    "aov": MetricTemplate(
        "fact_order",
        "SUM(CASE WHEN fact_order.is_paid = 1 THEN fact_order.order_amount ELSE 0 END) / NULLIF(COUNT(CASE WHEN fact_order.is_paid = 1 THEN 1 END), 0)",
    ),
    "refund_rate": MetricTemplate(
        "fact_order",
        "SUM(fact_order.refund_amount) / NULLIF(SUM(CASE WHEN fact_order.is_paid = 1 THEN fact_order.order_amount ELSE 0 END), 0)",
    ),
    "uv": MetricTemplate("fact_traffic", "SUM(fact_traffic.uv)"),
    "pay_cvr": MetricTemplate(
        "fact_traffic",
        "SUM(fact_traffic.pay_user_cnt) / NULLIF(SUM(fact_traffic.uv), 0)",
    ),
    "stockout_rate": MetricTemplate(
        "fact_inventory",
        "SUM(fact_inventory.stockout_hours) / NULLIF(SUM(fact_inventory.avail_hours), 0)",
    ),
    "complaint_rate": MetricTemplate(
        "fact_customer_ticket",
        "SUM(fact_customer_ticket.is_complaint) / NULLIF(COUNT(fact_customer_ticket.ticket_id), 0)",
    ),
}


# 维度 → 物理列映射：按事实表分别定义。category 是跨表维度（落在 dim_product），
# 因此需要白名单 JOIN（见下）；其余维度在事实表本表。
DIMENSION_COLUMNS: dict[str, dict[str, str]] = {
    "fact_order": {
        "channel": "fact_order.channel",
        "device": "fact_order.device",
        "product": "fact_order.product_id",
        "category": "dim_product.category",
    },
    "fact_traffic": {
        "channel": "fact_traffic.channel",
        "device": "fact_traffic.device",
        "product": "fact_traffic.product_id",
        "category": "dim_product.category",
    },
    "fact_inventory": {
        "warehouse": "fact_inventory.warehouse",
        "product": "fact_inventory.product_id",
        "category": "dim_product.category",
    },
    "fact_customer_ticket": {
        "product": "fact_customer_ticket.product_id",
        "category": "dim_product.category",
    },
    "fact_campaign": {
        "channel": "fact_campaign.channel",
    },
}


# JOIN 白名单：只有(事实表, category) 才允许，且 JOIN 文本固定，杜绝任意 join 条件。
JOIN_BY_FACT_AND_DIMENSION: dict[tuple[str, str], str] = {
    (
        "fact_order",
        "category",
    ): "INNER JOIN dim_product ON fact_order.product_id = dim_product.product_id",
    (
        "fact_traffic",
        "category",
    ): "INNER JOIN dim_product ON fact_traffic.product_id = dim_product.product_id",
    (
        "fact_inventory",
        "category",
    ): "INNER JOIN dim_product ON fact_inventory.product_id = dim_product.product_id",
    (
        "fact_customer_ticket",
        "category",
    ): "INNER JOIN dim_product ON fact_customer_ticket.product_id = dim_product.product_id",
}


class SQLRenderer:
    """QuerySpec → SQLPlan 的确定性渲染器。"""

    def render(self, spec: QuerySpec) -> SQLPlan:
        template = self._template_for(spec)
        select_parts: list[str] = []
        group_parts: list[str] = []
        joins: list[str] = []
        # 日期始终作为绑定参数，永不字符串拼接（防注入）。
        params: dict[str, object] = {
            "start_date": spec.time_range.start_date,
            "end_date": spec.time_range.end_date,
        }
        baseline = spec.purpose == "baseline"
        if baseline:
            target_date = spec.time_range.start_date
            params = {
                f"baseline_d{index}": target_date - timedelta(days=7 * (index + 1))
                for index in range(4)
            }

        # 下钻维度：拼 SELECT 列 + GROUP BY 列；若该(表,维度)需要 JOIN，则加入白名单 JOIN。
        for dimension in spec.group_by:
            column = DIMENSION_COLUMNS[template.fact_table][dimension]
            select_parts.append(f"{column} AS {dimension}")
            group_parts.append(column)
            join = JOIN_BY_FACT_AND_DIMENSION.get((template.fact_table, dimension))
            if join and join not in joins:
                joins.append(join)

        # 过滤维度也可能需要同样的白名单 JOIN（如按 category 过滤）。
        for dimension in spec.filters:
            join = JOIN_BY_FACT_AND_DIMENSION.get((template.fact_table, dimension))
            if join and join not in joins:
                joins.append(join)

        # 指标聚合列统一别名 metric_value，方便下游解析与排序。
        if baseline:
            business_date_column = f"{template.fact_table}.business_date"
            select_parts.insert(0, f"{business_date_column} AS business_date")
            group_parts.append(business_date_column)
        select_parts.append(f"{template.expression} AS metric_value")

        # WHERE：事实表 business_date 区间（绑定参数）是守卫的硬性要求；过滤值也走绑定参数。
        if baseline:
            where_parts = [
                f"{template.fact_table}.business_date IN (:baseline_d0, :baseline_d1, :baseline_d2, :baseline_d3)"
            ]
        else:
            where_parts = [
                f"{template.fact_table}.business_date BETWEEN :start_date AND :end_date"
            ]
        for dimension, value in sorted(spec.filters.items()):  # sorted 保证 SQL 文本确定性
            column = DIMENSION_COLUMNS[template.fact_table][dimension]
            param_name = f"filter_{dimension}"
            where_parts.append(f"{column} = :{param_name}")
            params[param_name] = value

        # 组装：SELECT ... FROM 事实表 [JOIN ...] WHERE ... [GROUP BY ... ORDER BY ...] LIMIT
        sql_parts = [
            "SELECT",
            ", ".join(select_parts),
            f"FROM {template.fact_table}",
        ]
        sql_parts.extend(joins)
        sql_parts.append("WHERE " + " AND ".join(where_parts))
        if group_parts:
            sql_parts.append("GROUP BY " + ", ".join(group_parts))
            sql_parts.append("ORDER BY metric_value DESC")  # 便于取 top 贡献元素
        sql_parts.append(f"LIMIT {spec.limit}")  # 强制 LIMIT（守卫会再次检查）

        sql = " ".join(sql_parts)
        return SQLPlan(
            sql=sql,
            sql_hash=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            params=params,
            # 盖上渲染器签名：证明"本进程渲染器生成"，供守卫放行 JOIN、供仓库验证溯源。
            renderer_signature=_renderer_signature(hashlib.sha256(sql.encode("utf-8")).hexdigest()),
        )

    def _template_for(self, spec: QuerySpec) -> MetricTemplate:
        if spec.signal_type == "campaign" and spec.metric_id == "gmv" and "channel" in spec.filters:
            return MetricTemplate("fact_campaign", "SUM(fact_campaign.clicks)")
        return METRIC_TEMPLATES[spec.metric_id]

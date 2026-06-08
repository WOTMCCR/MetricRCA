from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from metric_rca.domain.models import QuerySpec, SQLPlan


_RENDERER_SECRET = secrets.token_hex(32)


def _renderer_signature(sql_hash: str) -> str:
    return hmac.new(_RENDERER_SECRET.encode(), sql_hash.encode(), "sha256").hexdigest()


def is_renderer_signed(plan: SQLPlan) -> bool:
    return plan.renderer_signature == _renderer_signature(plan.sql_hash)


@dataclass(frozen=True)
class MetricTemplate:
    fact_table: str
    expression: str


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
}


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
    def render(self, spec: QuerySpec) -> SQLPlan:
        template = METRIC_TEMPLATES[spec.metric_id]
        select_parts: list[str] = []
        group_parts: list[str] = []
        joins: list[str] = []
        params: dict[str, object] = {
            "start_date": spec.time_range.start_date,
            "end_date": spec.time_range.end_date,
        }

        for dimension in spec.group_by:
            column = DIMENSION_COLUMNS[template.fact_table][dimension]
            select_parts.append(f"{column} AS {dimension}")
            group_parts.append(column)
            join = JOIN_BY_FACT_AND_DIMENSION.get((template.fact_table, dimension))
            if join and join not in joins:
                joins.append(join)

        for dimension in spec.filters:
            join = JOIN_BY_FACT_AND_DIMENSION.get((template.fact_table, dimension))
            if join and join not in joins:
                joins.append(join)

        select_parts.append(f"{template.expression} AS metric_value")
        where_parts = [
            f"{template.fact_table}.business_date BETWEEN :start_date AND :end_date"
        ]
        for dimension, value in sorted(spec.filters.items()):
            column = DIMENSION_COLUMNS[template.fact_table][dimension]
            param_name = f"filter_{dimension}"
            where_parts.append(f"{column} = :{param_name}")
            params[param_name] = value

        sql_parts = [
            "SELECT",
            ", ".join(select_parts),
            f"FROM {template.fact_table}",
        ]
        sql_parts.extend(joins)
        sql_parts.append("WHERE " + " AND ".join(where_parts))
        if group_parts:
            sql_parts.append("GROUP BY " + ", ".join(group_parts))
            sql_parts.append("ORDER BY metric_value DESC")
        sql_parts.append(f"LIMIT {spec.limit}")

        sql = " ".join(sql_parts)
        return SQLPlan(
            sql=sql,
            sql_hash=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            params=params,
            renderer_signature=_renderer_signature(hashlib.sha256(sql.encode("utf-8")).hexdigest()),
        )

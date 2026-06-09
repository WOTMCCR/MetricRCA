from __future__ import annotations

import hashlib
from datetime import date
from itertools import combinations

import pytest
from pydantic import ValidationError

from metric_rca.domain.enums import DimensionId, MetricId
from metric_rca.domain.models import QuerySpec, TimeRange
from metric_rca.guardrails.query_spec import build_query_spec
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.guardrails.sql_guard import guard_sql


def _spec(
    metric_id: str,
    group_by: list[str] | None = None,
    purpose: str = "current",
    filters: dict[str, str] | None = None,
):
    return build_query_spec(
        metric_id=metric_id,
        start_date=date(2026, 6, 5),
        end_date=date(2026, 6, 5),
        group_by=group_by or [],
        filters=filters or {},
        purpose=purpose,
        limit=1000,
    )


def test_gmv_current_render_contract() -> None:
    plan = SQLRenderer().render(_spec("gmv"))
    sql = plan.sql.lower()

    assert sql.count("select") == 1
    assert "business_date" in sql
    assert " limit " in sql
    assert "*" not in sql
    assert plan.sql_hash == hashlib.sha256(plan.sql.encode("utf-8")).hexdigest()
    assert plan.params["start_date"] == date(2026, 6, 5)


def test_baseline_uses_exact_previous_four_same_weekdays_not_broad_between() -> None:
    plan = SQLRenderer().render(_spec("gmv", purpose="baseline"))
    guarded = guard_sql(plan)

    assert guarded.guard_status == "passed", guarded.guard_errors
    assert "business_date IN (:baseline_d0, :baseline_d1, :baseline_d2, :baseline_d3)" in plan.sql
    assert " BETWEEN " not in plan.sql
    assert plan.params["baseline_d0"] == date(2026, 5, 29)
    assert plan.params["baseline_d1"] == date(2026, 5, 22)
    assert plan.params["baseline_d2"] == date(2026, 5, 15)
    assert plan.params["baseline_d3"] == date(2026, 5, 8)
    assert "fact_order.business_date AS business_date" in plan.sql
    assert "GROUP BY fact_order.business_date" in plan.sql


def test_campaign_signal_baseline_uses_fact_campaign_same_weekday_in_query() -> None:
    spec = build_query_spec(
        metric_id="gmv",
        start_date=date(2026, 6, 5),
        end_date=date(2026, 6, 5),
        filters={"channel": "paid_ads"},
        purpose="baseline",
        signal_type="campaign",
    )
    plan = SQLRenderer().render(spec)
    guarded = guard_sql(plan)

    assert guarded.guard_status == "passed", guarded.guard_errors
    assert "FROM fact_campaign" in plan.sql
    assert "fact_campaign.business_date IN (:baseline_d0, :baseline_d1, :baseline_d2, :baseline_d3)" in plan.sql
    assert "fact_order" not in plan.sql


def test_category_uses_exact_renderer_join_and_channel_has_no_join() -> None:
    category = SQLRenderer().render(_spec("gmv", ["category"]))
    assert (
        "INNER JOIN dim_product ON fact_order.product_id = dim_product.product_id"
        in category.sql
    )

    channel = SQLRenderer().render(_spec("gmv", ["channel"]))
    assert "JOIN" not in channel.sql


def test_filters_render_as_bound_predicates_and_required_joins() -> None:
    renderer = SQLRenderer()

    channel_filter = renderer.render(_spec("gmv", filters={"channel": "paid_ads"}))
    assert "fact_order.channel = :filter_channel" in channel_filter.sql
    assert channel_filter.params["filter_channel"] == "paid_ads"
    assert "paid_ads" not in channel_filter.sql
    assert "JOIN" not in channel_filter.sql
    assert "GROUP BY" not in channel_filter.sql
    assert guard_sql(channel_filter).guard_status == "passed"

    category_filter = renderer.render(_spec("gmv", filters={"category": "electronics"}))
    assert (
        "INNER JOIN dim_product ON fact_order.product_id = dim_product.product_id"
        in category_filter.sql
    )
    assert "dim_product.category = :filter_category" in category_filter.sql
    assert category_filter.params["filter_category"] == "electronics"
    assert "electronics" not in category_filter.sql
    assert "GROUP BY" not in category_filter.sql
    assert guard_sql(category_filter).guard_status == "passed"

    inventory_category_filter = renderer.render(
        _spec("stockout_rate", filters={"category": "electronics"})
    )
    assert (
        "INNER JOIN dim_product ON fact_inventory.product_id = dim_product.product_id"
        in inventory_category_filter.sql
    )
    assert "dim_product.category = :filter_category" in inventory_category_filter.sql
    assert inventory_category_filter.params["filter_category"] == "electronics"
    assert guard_sql(inventory_category_filter).guard_status == "passed"


def test_renderer_and_guard_share_whitelist_for_question_families() -> None:
    specs = [
        _spec("gmv"),
        _spec("gmv", ["channel"], "drilldown"),
        _spec("gmv", ["category"], "drilldown"),
        _spec("pay_cvr", ["device"], "drilldown"),
        _spec("refund_rate", ["category"], "drilldown"),
        _spec("stockout_rate", ["category"], "signal"),
    ]

    for spec in specs:
        guarded = guard_sql(SQLRenderer().render(spec))
        assert guarded.guard_status == "passed", guarded.guard_errors


def test_all_directly_accepted_query_specs_are_renderable_or_rejected() -> None:
    renderable = 0
    filter_values = {
        "channel": "paid_ads",
        "device": "mobile",
        "category": "electronics",
        "product": "1",
        "warehouse": "tokyo",
    }
    for metric_id in MetricId:
        dimension_sets = [[]]
        dimension_sets.extend([[dimension.value] for dimension in DimensionId])
        dimension_sets.extend(
            [list(pair) for pair in combinations([dimension.value for dimension in DimensionId], 2)]
        )
        for group_by in dimension_sets:
            filter_candidates = [{}]
            filter_candidates.extend(
                [{dimension.value: filter_values[dimension.value]} for dimension in DimensionId]
            )
            filter_candidates.extend(
                [
                    {
                        first.value: filter_values[first.value],
                        second.value: filter_values[second.value],
                    }
                    for first, second in combinations(DimensionId, 2)
                ]
            )
            for filters in filter_candidates:
                try:
                    spec = QuerySpec(
                        metric_id=metric_id.value,
                        time_range=TimeRange(start_date=date(2026, 6, 5), end_date=date(2026, 6, 5)),
                        group_by=group_by,
                        filters=filters,
                    )
                except ValidationError:
                    continue
                plan = SQLRenderer().render(spec)
                guarded = guard_sql(plan)
                assert guarded.guard_status == "passed", guarded.guard_errors
                for dimension, value in filters.items():
                    assert f":filter_{dimension}" in plan.sql
                    assert plan.params[f"filter_{dimension}"] == value
                if "category" in [*group_by, *filters.keys()]:
                    assert "INNER JOIN dim_product ON" in plan.sql
                renderable += 1
    assert renderable > 0

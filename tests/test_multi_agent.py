from __future__ import annotations

from datetime import date

from metric_rca.domain.models import MetricDefinition
from metric_rca.agent.subagents import route_metric_family
from metric_rca.runtime.plan_compiler import RcaPlanCompiler
from metric_rca.services.metric_contracts import ParsedIntent


def _compiler(*, family: str) -> RcaPlanCompiler:
    return RcaPlanCompiler(metric_service=_MetricCatalog(family))


class _MetricCatalog:
    def __init__(self, family: str) -> None:
        self.family = family

    def get_metric_definition(self, metric_id: str) -> MetricDefinition:
        return MetricDefinition(
            metric_id=metric_id,
            display_name=metric_id,
            formula="test",
            metric_family=self.family,
            source_table="fact_order",
            allowed_dimensions=["channel", "category", "device", "product"],
        )


def test_plan_compiler_routes_gmv_metrics_to_gmv_family() -> None:
    plan = _compiler(family="gmv_family").compile(
        run_id="run-1",
        parsed_intent=ParsedIntent(
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            question_family="gmv_drop",
        ),
    )

    assert plan.family == "gmv_family"
    assert [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"] == [
        "channel",
        "category",
        "product",
    ]


def test_plan_compiler_routes_rate_metrics_to_rate_family() -> None:
    plan = _compiler(family="rate_family").compile(
        run_id="run-1",
        parsed_intent=ParsedIntent(
            metric_id="pay_cvr",
            target_date=date(2026, 6, 5),
            question_family="pay_cvr_drop",
        ),
    )

    assert plan.family == "rate_family"
    assert [action.args.get("dimension") for action in plan.actions if action.kind == "drilldown_dimension"] == [
        "device"
    ]


def test_plan_compiler_family_comes_from_metric_metadata_not_metric_id_list() -> None:
    plan = _compiler(family="rate_family").compile(
        run_id="run-1",
        parsed_intent=ParsedIntent(
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            question_family="gmv_drop",
        ),
    )

    assert plan.family == "rate_family"


def test_legacy_subagent_route_family_comes_from_metric_metadata() -> None:
    assert route_metric_family("custom_metric", metric_service=_MetricCatalog("rate_family")) == "rate_family"


def test_rate_family_refund_policy_uses_metric_fallback_for_nonstandard_strategy() -> None:
    plan = _compiler(family="rate_family").compile(
        run_id="run-1",
        parsed_intent=ParsedIntent(
            metric_id="refund_rate",
            target_date=date(2026, 6, 5),
            question_family="refund_rate_increase",
            analysis_strategy="channel_first",
        ),
    )

    signal_action = next(action for action in plan.actions if action.kind == "fetch_related_signal")
    assert plan.family == "rate_family"
    assert signal_action.args["dimension"] == "product"
    assert signal_action.args["signal_type"] == "refund_quality"

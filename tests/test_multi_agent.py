from __future__ import annotations

from datetime import date

from metric_rca.runtime.plan_compiler import RcaPlanCompiler
from metric_rca.services.metric_contracts import ParsedIntent


def test_plan_compiler_routes_gmv_metrics_to_gmv_family() -> None:
    plan = RcaPlanCompiler().compile(
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
    plan = RcaPlanCompiler().compile(
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


def test_rate_family_refund_policy_uses_metric_fallback_for_nonstandard_strategy() -> None:
    plan = RcaPlanCompiler().compile(
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

from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from langchain_core.outputs import LLMResult

from metric_rca.agent.deep_tools import (
    CalculateContributionIn,
    DetectAnomalyIn,
    DrilldownDimensionIn,
    FetchRelatedSignalIn,
    RankRootCausesIn,
)
from metric_rca.agent.discovery_policy import DiscoveryPolicy, discovery_policy_from_intent
from metric_rca.agent.middleware import GuardMiddleware, MetricRCATokenUsageCallback, RunGuardContext
from metric_rca.services.metric_contracts import ParsedIntent


def test_illegal_tool_args_short_circuit_without_execution() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]})

    request = _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05", "extra": "blocked"})
    result = middleware.wrap_tool_call(request, handler)

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert called is False
    assert context.failed is False
    assert writer.steps[-1]["error_code"] == "ACTION_SCHEMA_INVALID"


def test_single_illegal_args_then_valid_call_continues() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]})

    bad = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05", "extra": "blocked"}),
        handler,
    )
    good = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        handler,
    )

    assert json.loads(bad.content)["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert json.loads(good.content)["observation"]["ok"] is True
    assert calls == 1
    assert context.failed is False


def test_two_consecutive_illegal_args_same_tool_fails_run() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)

    def handler(request):
        raise AssertionError("handler must not run for invalid args")

    first = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05", "extra": "blocked"}),
        handler,
    )
    second = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05", "extra": "blocked"}),
        handler,
    )

    assert json.loads(first.content)["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert json.loads(second.content)["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert context.failed is True
    assert context.error_code == "ACTION_SCHEMA_INVALID"


def test_middleware_rejects_unknown_tool_without_handler() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]})

    result = middleware.wrap_tool_call(_request("read_file", {"path": "/tmp/secret"}), handler)

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert called is False
    assert context.failed is False
    assert writer.steps[-1]["action"] == "invalid_tool_call"


def test_middleware_normalizes_long_unknown_tool_name_before_trace_write() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)
    long_tool_name = "unknown_tool_" + ("x" * 200)

    result = middleware.wrap_tool_call(
        _request(long_tool_name, {"metric_id": "gmv"}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": []}),
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert payload["observation"]["action_name"] == long_tool_name
    assert writer.steps[-1]["action"] == "invalid_tool_call"
    assert len(writer.steps[-1]["action"]) <= 48


def test_non_mapping_tool_args_return_typed_schema_invalid() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]})

    result = middleware.wrap_tool_call(SimpleNamespace(tool_call={"name": "detect_anomaly", "args": ["not", "object"], "id": "call-1"}), handler)

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert payload["observation"]["message"] == "tool args must be a JSON object"
    assert called is False
    assert context.failed is False


def test_explicit_scope_filter_prevents_switching_dimension() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)

    middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05", "filters": {"category": "electronics"}}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]}),
    )
    result = middleware.wrap_tool_call(
        _request("drilldown_dimension", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "dimension": "device",
            "filters": {"category": "electronics"},
            "evidence_ids": ["run-1:E1"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E2"]}),
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert payload["observation"]["message"].startswith("explicit question scope requires dimension=category")
    assert context.failed is False


def test_repeated_explicit_scope_errors_do_not_trip_schema_invalid_fail_streak() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.explicit_filters = {"category": "electronics"}
    middleware = GuardMiddleware(context)

    def handler(request):
        raise AssertionError("handler must not run for explicit scope errors")

    for dimension in ["channel", "product", "device"]:
        result = middleware.wrap_tool_call(
            _request("drilldown_dimension", {
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "dimension": dimension,
                "filters": {"category": "electronics"},
                "evidence_ids": ["run-1:E1"],
            }),
            handler,
        )
        assert json.loads(result.content)["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"

    assert context.failed is False
    assert context.consecutive_schema_invalid_count == 0


def test_detect_with_extra_filter_does_not_disable_seeded_explicit_scope() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.explicit_filters = {"category": "electronics"}
    middleware = GuardMiddleware(context)

    detect = middleware.wrap_tool_call(
        _request(
            "detect_anomaly",
            {
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "filters": {"category": "electronics", "device": "mobile"},
            },
        ),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]}),
    )
    result = middleware.wrap_tool_call(
        _request(
            "drilldown_dimension",
            {
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "dimension": "device",
                "filters": {"category": "electronics", "device": "mobile"},
                "evidence_ids": ["run-1:E1"],
            },
        ),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E2"]}),
    )

    assert json.loads(detect.content)["observation"]["ok"] is True
    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert payload["observation"]["message"].startswith("explicit question scope requires dimension=category")
    assert context.explicit_filters == {"category": "electronics"}
    assert context.failed is False


def test_explicit_question_scope_requires_detect_filter_before_execution() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.explicit_filters = {"category": "electronics"}
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]})

    result = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert payload["observation"]["message"].startswith("explicit question scope requires filters.category=electronics")
    assert called is False
    assert context.failed is False


def test_evidence_ids_with_wrong_run_prefix_are_recoverable_precondition_errors() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E4"]})

    result = middleware.wrap_tool_call(
        _request(
            "calculate_contribution",
            {
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "dimension": "channel",
                "element": "paid_ads",
                "evidence_ids": ["run_1:E1", "run_1:E2", "run_1:E3"],
            },
        ),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "EVIDENCE_MISSING"
    assert "must start with current run prefix run-1:" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0


def test_metric_scope_violation_short_circuits_without_budget_or_handler() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.target_metric_id = "gmv"
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]})

    result = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "pay_cvr", "target_date": "2026-06-05"}),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "METRIC_SCOPE_VIOLATION"
    assert "run target metric is gmv" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0
    assert writer.steps[-1]["error_code"] == "METRIC_SCOPE_VIOLATION"


def test_target_date_scope_violation_short_circuits_without_budget_or_handler() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.target_date = "2026-06-05"
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]})

    result = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-04"}),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "METRIC_SCOPE_VIOLATION"
    assert "run target_date is 2026-06-05" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0


def test_fetch_related_signal_accepts_matching_filters_without_schema_error() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "inventory",
            "dimension": "category",
            "element": "electronics",
            "filters": {"category": "electronics"},
            "evidence_ids": ["run-1:E1", "run-1:E2_category"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_cat_electronics"]}),
    )

    assert json.loads(result.content)["observation"]["ok"] is True
    assert context.failed is False


def test_fetch_related_signal_rejects_conflicting_filters_before_budget_or_handler() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_cat_electronics"]})

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "inventory",
            "dimension": "category",
            "element": "electronics",
            "filters": {"category": "fashion"},
            "evidence_ids": ["run-1:E1", "run-1:E2_category"],
        }),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "filters must be empty or exactly match" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0


def test_unscoped_gmv_discovery_requires_channel_category_and_product_drilldowns() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.target_metric_id = "gmv"
    context.discovery_policy = DiscoveryPolicy(required_drilldowns=("channel", "category", "product"))
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_channel", "run_id": "run-1", "guard_status": "passed"},
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_ch_paid_ads"]})

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "campaign",
            "dimension": "channel",
            "element": "paid_ads",
            "evidence_ids": ["run-1:E1", "run-1:E2_channel"],
        }),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "EVIDENCE_MISSING"
    assert "E2_category" in payload["observation"]["message"]
    assert "E2_product" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0


def test_unscoped_gmv_discovery_allows_fetch_after_required_drilldowns() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.target_metric_id = "gmv"
    context.discovery_policy = DiscoveryPolicy(required_drilldowns=("channel", "category", "product"))
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_channel", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_category", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
        ]
    )
    middleware = GuardMiddleware(context)

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "campaign",
            "dimension": "channel",
            "element": "paid_ads",
            "evidence_ids": ["run-1:E1", "run-1:E2_channel"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_ch_paid_ads"]}),
    )

    assert json.loads(result.content)["observation"]["ok"] is True
    assert context.failed is False


def test_broad_overall_gmv_discovery_requires_channel_campaign_first_e3() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.target_metric_id = "gmv"
    context.discovery_policy = DiscoveryPolicy(
        required_drilldowns=("channel", "category", "product"),
        first_signal_dimension="channel",
        first_signal_type="campaign",
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_cat_electronics"]})

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "inventory",
            "dimension": "category",
            "element": "electronics",
            "evidence_ids": ["run-1:E1", "run-1:E2_category"],
        }),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "dimension=channel" in payload["observation"]["message"]
    assert "signal_type=campaign" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0


def test_channel_first_policy_requires_top_channel_candidate_element() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.discovery_policy = discovery_policy_from_intent(
        ParsedIntent(
            metric_id="gmv",
            target_date="2026-06-05",
            question_family="gmv_drop",
            analysis_strategy="channel_first",
        )
    )
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {
                "evidence_id": "run-1:E2_channel",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {
                    "candidates": [
                        {"dimension": "channel", "element": "paid_ads"},
                        {"dimension": "channel", "element": "organic"},
                    ]
                },
            },
            {"evidence_id": "run-1:E2_category", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_ch_organic"]})

    rejected = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "campaign",
            "dimension": "channel",
            "element": "organic",
            "evidence_ids": ["run-1:E1", "run-1:E2_channel"],
        }),
        handler,
    )

    payload = json.loads(rejected.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "element=paid_ads" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False


def test_first_signal_policy_enforces_structured_element_without_question_text() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.discovery_policy = discovery_policy_from_intent(
        ParsedIntent(
            metric_id="gmv",
            target_date="2026-06-05",
            question_family="gmv_drop",
            analysis_strategy="organic_first",
        )
    )
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_channel", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_category", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_ch_paid_ads"]})

    rejected = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "campaign",
            "dimension": "channel",
            "element": "paid_ads",
            "evidence_ids": ["run-1:E1", "run-1:E2_channel"],
        }),
        handler,
    )

    payload = json.loads(rejected.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "element=organic" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0

    accepted = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "campaign",
            "dimension": "channel",
            "element": "organic",
            "evidence_ids": ["run-1:E1", "run-1:E2_channel"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_ch_organic"]}),
    )

    assert json.loads(accepted.content)["observation"]["ok"] is True
    assert context.failed is False


def test_unscoped_pay_cvr_policy_requires_device_conversion_first_signal() -> None:
    context = _context(_TraceWriter())
    context.discovery_policy = discovery_policy_from_intent(
        ParsedIntent(
            metric_id="pay_cvr",
            target_date="2026-06-05",
            question_family="pay_cvr_drop",
            analysis_strategy="standard",
        )
    )
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {
                "evidence_id": "run-1:E2_device",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {
                    "candidates": [
                        {"dimension": "device", "element": "mobile"},
                        {"dimension": "device", "element": "desktop"},
                    ]
                },
            },
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_ch_social"]})

    rejected = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "pay_cvr",
            "target_date": "2026-06-05",
            "signal_type": "conversion",
            "dimension": "channel",
            "element": "social",
            "evidence_ids": ["run-1:E1", "run-1:E2_channel"],
        }),
        handler,
    )

    payload = json.loads(rejected.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "dimension=device" in payload["observation"]["message"]
    assert "signal_type=conversion" in payload["observation"]["message"]
    assert called is False

    accepted = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "pay_cvr",
            "target_date": "2026-06-05",
            "signal_type": "conversion",
            "dimension": "device",
            "element": "mobile",
            "evidence_ids": ["run-1:E1", "run-1:E2_device"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_dev_mobile"]}),
    )

    assert json.loads(accepted.content)["observation"]["ok"] is True


def test_unscoped_refund_and_uv_policies_use_structured_first_signals() -> None:
    refund_policy = discovery_policy_from_intent(
        ParsedIntent(
            metric_id="refund_rate",
            target_date="2026-06-05",
            question_family="refund_rate_increase",
            analysis_strategy="standard",
        )
    )
    uv_policy = discovery_policy_from_intent(
        ParsedIntent(
            metric_id="uv",
            target_date="2026-06-05",
            question_family="uv_drop",
            analysis_strategy="standard",
        )
    )

    assert refund_policy.required_drilldowns == ("product",)
    assert refund_policy.first_signal_dimension == "product"
    assert refund_policy.first_signal_type == "refund_quality"
    assert refund_policy.enforce_first_signal_top_candidate is True
    assert uv_policy.required_drilldowns == ("channel",)
    assert uv_policy.first_signal_dimension == "channel"
    assert uv_policy.first_signal_type == "campaign"
    assert uv_policy.enforce_first_signal_top_candidate is True


def test_unscoped_metric_policy_does_not_require_exact_question_family() -> None:
    policy = discovery_policy_from_intent(
        ParsedIntent(
            metric_id="refund_rate",
            target_date="2026-06-05",
            question_family="complaint_rate_increase",
            analysis_strategy="standard",
        )
    )

    assert policy.required_drilldowns == ("product",)
    assert policy.first_signal_dimension == "product"
    assert policy.first_signal_type == "refund_quality"
    assert policy.enforce_first_signal_top_candidate is True


def test_repair_action_guard_rejects_drift_without_budget() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.required_repair_action = "rank_root_causes"
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]})

    result = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "reflection repair requires rank_root_causes" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0


def test_fetch_signal_repair_allows_contribution_then_rank_after_required_evidence() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.required_repair_action = "fetch_related_signal"
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
            {
                "evidence_id": "run-1:E3_prod_2",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {"dimension": "product", "element": "2"},
            },
        ]
    )
    middleware = GuardMiddleware(context)

    contribution = middleware.wrap_tool_call(
        _request(
            "calculate_contribution",
            {
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "dimension": "product",
                "element": "2",
                "evidence_ids": ["run-1:E1", "run-1:E2_product", "run-1:E3_prod_2"],
            },
        ),
        lambda request: _message(
            request,
            {"observation": {"ok": True}, "evidence_ids": ["run-1:E4"]},
        ),
    )

    context.repository.evidences["run-1:E4"] = {
        "evidence_id": "run-1:E4",
        "run_id": "run-1",
        "guard_status": "passed",
    }
    rank = middleware.wrap_tool_call(
        _request("rank_root_causes", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        lambda request: _message(
            request,
            {"observation": {"ok": True}, "evidence_ids": ["run-1:E_rank"]},
        ),
    )

    assert json.loads(contribution.content)["observation"]["ok"] is True
    assert json.loads(rank.content)["observation"]["ok"] is True
    assert context.failed is False


def test_detect_repair_allows_normal_downstream_flow_after_e1() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.required_repair_action = "detect_anomaly"
    context.repository = _Repository(
        [
            {
                "evidence_id": "run-1:E1",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {"is_anomaly": True},
            }
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E2_channel"]})

    result = middleware.wrap_tool_call(
        _request(
            "drilldown_dimension",
            {
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "dimension": "channel",
                "evidence_ids": ["run-1:E1"],
            },
        ),
        handler,
    )

    assert json.loads(result.content)["observation"]["ok"] is True
    assert called is True
    assert context.failed is False


def test_detect_repair_rejects_downstream_flow_when_e1_lacks_anomaly_result() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.required_repair_action = "detect_anomaly"
    context.repository = _Repository(
        [
            {
                "evidence_id": "run-1:E1",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {"value": 1.0},
            }
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E2_channel"]})

    result = middleware.wrap_tool_call(
        _request(
            "drilldown_dimension",
            {
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "dimension": "channel",
                "evidence_ids": ["run-1:E1"],
            },
        ),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "reflection repair requires detect_anomaly" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False


def test_fetch_signal_repair_still_rejects_contribution_before_e3() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.required_repair_action = "fetch_related_signal"
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E4"]})

    result = middleware.wrap_tool_call(
        _request(
            "calculate_contribution",
            {
                "metric_id": "gmv",
                "target_date": "2026-06-05",
                "dimension": "product",
                "element": "2",
                "evidence_ids": ["run-1:E1", "run-1:E2_product", "run-1:E3_prod_2"],
            },
        ),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "reflection repair requires fetch_related_signal" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False


def test_repair_progression_rejects_rank_when_e4_failed_guard() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.required_repair_action = "calculate_contribution"
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E3_prod_2", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E4", "run_id": "run-1", "guard_status": "failed"},
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E_rank"]})

    result = middleware.wrap_tool_call(
        _request("rank_root_causes", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "reflection repair requires calculate_contribution" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False


def test_top_candidate_policy_rejects_malformed_e2_drilldown_summary() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.discovery_policy = DiscoveryPolicy(
        first_signal_dimension="product",
        first_signal_type="inventory",
        enforce_first_signal_top_candidate=True,
    )
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {
                "evidence_id": "run-1:E2_product",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {"metric_id": "gmv", "dimension": "product"},
            },
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_prod_2"]})

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "inventory",
            "dimension": "product",
            "element": "2",
            "evidence_ids": ["run-1:E1", "run-1:E2_product"],
        }),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "structured top-candidate drilldown evidence" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False


def test_first_signal_policy_does_not_parse_question_text() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.target_metric_id = "gmv"
    context.question = "Why did overall GMV fall yesterday?"
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_channel", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_category", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_cat_electronics"]})

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "inventory",
            "dimension": "category",
            "element": "electronics",
            "evidence_ids": ["run-1:E1", "run-1:E2_category"],
        }),
        handler,
    )

    assert json.loads(result.content)["observation"]["ok"] is True
    assert called is True
    assert context.failed is False


def test_merchandise_gmv_discovery_requires_product_inventory_first_e3() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.target_metric_id = "gmv"
    context.discovery_policy = DiscoveryPolicy(
        required_drilldowns=("channel", "category", "product"),
        first_signal_dimension="product",
        first_signal_type="inventory",
    )
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_channel", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_category", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
        ]
    )
    middleware = GuardMiddleware(context)

    rejected = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "campaign",
            "dimension": "channel",
            "element": "paid_ads",
            "evidence_ids": ["run-1:E1", "run-1:E2_channel"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_cat_electronics"]}),
    )

    payload = json.loads(rejected.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "dimension=product" in payload["observation"]["message"]
    assert "signal_type=inventory" in payload["observation"]["message"]
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0

    accepted = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "inventory",
            "dimension": "product",
            "element": "2",
            "evidence_ids": ["run-1:E1", "run-1:E2_product"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_prod_2"]}),
    )

    assert json.loads(accepted.content)["observation"]["ok"] is True
    assert context.failed is False


def test_existing_e3_takes_precedence_over_product_first_retry_hint() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.target_metric_id = "gmv"
    context.discovery_policy = DiscoveryPolicy(
        required_drilldowns=("channel", "category", "product"),
        first_signal_dimension="product",
        first_signal_type="inventory",
        enforce_first_signal_top_candidate=True,
    )
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_channel", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_category", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E3_prod_2", "run_id": "run-1", "guard_status": "passed"},
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_cat_electronics"]})

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "inventory",
            "dimension": "category",
            "element": "electronics",
            "evidence_ids": ["run-1:E1", "run-1:E2_category"],
        }),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "E3_ALREADY_EXISTS"
    message = payload["observation"]["message"]
    assert "run-1:E3_prod_2 already exists" in message
    assert "calculate_contribution" in message
    assert "['run-1:E1', 'run-1:E2_product', 'run-1:E3_prod_2']" in message
    assert "dimension=product" not in message
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0


def test_first_budget_exhaustion_is_recoverable_then_next_data_tool_fails_run() -> None:
    writer = _TraceWriter()
    context = _context(writer, max_query=0)
    middleware = GuardMiddleware(context)

    first = middleware.wrap_tool_call(
        _request("drilldown_dimension", {"metric_id": "gmv", "target_date": "2026-06-05", "dimension": "channel", "evidence_ids": ["run-1:E1"]}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E2"]}),
    )
    second = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]}),
    )

    first_payload = json.loads(first.content)
    second_payload = json.loads(second.content)
    assert first_payload["observation"]["error_code"] == "BUDGET_EXCEEDED"
    assert first_payload["observation"]["message"] == "query budget exhausted; call rank_root_causes or stop"
    assert second_payload["observation"]["error_code"] == "BUDGET_EXCEEDED"
    assert second_payload["observation"]["message"] == "data tool attempted after budget exhaustion"
    assert context.failed is True
    assert context.error_code == "BUDGET_EXCEEDED"
    assert writer.steps[0]["error_code"] == "BUDGET_EXCEEDED"
    assert writer.steps[1]["error_code"] == "BUDGET_EXCEEDED"


def test_step_budget_allows_finalizer_and_rejects_data_tool() -> None:
    writer = _TraceWriter()
    context = _context(writer, max_steps=1)
    context.step_count = 1
    middleware = GuardMiddleware(context)

    rank = middleware.wrap_tool_call(
        _request("rank_root_causes", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": []}),
    )
    plan = middleware.wrap_tool_call(
        _request("write_todos", {"todos": [{"content": "Finalize", "status": "completed"}]}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": []}),
    )
    rejected = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E1"]}),
    )
    failed = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "campaign",
            "dimension": "channel",
            "element": "paid_ads",
            "evidence_ids": ["run-1:E2"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3"]}),
    )

    assert json.loads(rank.content)["observation"]["ok"] is True
    assert json.loads(plan.content)["observation"]["ok"] is True
    assert json.loads(rejected.content)["observation"]["error_code"] == "BUDGET_EXCEEDED"
    assert json.loads(rejected.content)["observation"]["message"] == "step budget exhausted; call rank_root_causes or stop"
    assert json.loads(failed.content)["observation"]["error_code"] == "BUDGET_EXCEEDED"
    assert json.loads(failed.content)["observation"]["message"] == "data tool attempted after budget exhaustion"
    assert context.failed is True
    assert context.error_code == "BUDGET_EXCEEDED"


def test_drilldown_depth_exhaustion_does_not_block_followup_signal_tool() -> None:
    writer = _TraceWriter()
    context = _context(writer, max_drilldown_depth=1)
    context.drilldown_depth = 1
    middleware = GuardMiddleware(context)
    called = False

    exhausted = middleware.wrap_tool_call(
        _request("drilldown_dimension", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "dimension": "warehouse",
            "evidence_ids": ["run-1:E1", "run-1:E2_product"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E2_warehouse"]}),
    )

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_prod_2"]})

    signal = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "inventory",
            "dimension": "product",
            "element": "2",
            "evidence_ids": ["run-1:E1", "run-1:E2_product"],
        }),
        handler,
    )

    assert json.loads(exhausted.content)["observation"]["error_code"] == "BUDGET_EXCEEDED"
    assert json.loads(exhausted.content)["observation"]["message"] == "drilldown depth exhausted; call rank_root_causes or stop"
    assert json.loads(signal.content)["observation"]["ok"] is True
    assert called is True
    assert context.budget_exhausted_once is False
    assert context.failed is False


def test_existing_drilldown_reuse_does_not_consume_or_trip_drilldown_depth() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.drilldown_depth = 2
    context.query_count = 2
    context.step_count = 2
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {
                "evidence_id": "run-1:E2_category",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {
                    "metric_id": "gmv",
                    "dimension": "category",
                    "filters": {},
                    "input_evidence_ids": ["run-1:E1"],
                },
            },
            {
                "evidence_id": "run-1:E2_channel",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {
                    "metric_id": "gmv",
                    "dimension": "channel",
                    "filters": {},
                    "input_evidence_ids": ["run-1:E1"],
                },
            },
        ]
    )
    middleware = GuardMiddleware(context)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E2_channel"]})

    result = middleware.wrap_tool_call(
        _request("drilldown_dimension", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "dimension": "channel",
            "evidence_ids": ["run-1:E1", "run-1:E2_category"],
        }),
        handler,
    )

    assert json.loads(result.content)["observation"]["ok"] is True
    assert calls == 0
    assert context.failed is False
    assert context.budget_exhausted_once is False
    assert context.step_count == 2
    assert context.query_count == 2
    assert context.drilldown_depth == 2


def test_budget_exhaustion_allows_matching_e4_contribution_finalizer() -> None:
    writer = _TraceWriter()
    context = _context(writer, max_steps=1)
    context.step_count = 1
    context.budget_exhausted_once = True
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_channel", "run_id": "run-1", "guard_status": "passed"},
            {
                "evidence_id": "run-1:E3_ch_organic",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {"dimension": "channel", "element": "organic"},
            },
        ]
    )
    middleware = GuardMiddleware(context)

    result = middleware.wrap_tool_call(
        _request("calculate_contribution", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "dimension": "channel",
            "element": "organic",
            "evidence_ids": ["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_organic"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E4"]}),
    )
    rejected = middleware.wrap_tool_call(
        _request("calculate_contribution", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "dimension": "channel",
            "element": "paid_ads",
            "evidence_ids": ["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_organic"],
        }),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E4"]}),
    )

    assert json.loads(result.content)["observation"]["ok"] is True
    assert json.loads(rejected.content)["observation"]["error_code"] == "BUDGET_EXCEEDED"
    assert json.loads(rejected.content)["observation"]["message"] == "data tool attempted after budget exhaustion"
    assert context.failed is True
    assert context.error_code == "BUDGET_EXCEEDED"


def test_product_first_requires_strongest_product_candidate_element() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.target_metric_id = "gmv"
    context.discovery_policy = DiscoveryPolicy(
        required_drilldowns=("channel", "category", "product"),
        first_signal_dimension="product",
        first_signal_type="inventory",
        enforce_first_signal_top_candidate=True,
    )
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_channel", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_category", "run_id": "run-1", "guard_status": "passed"},
            {
                "evidence_id": "run-1:E2_product",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {
                    "metric_id": "gmv",
                    "dimension": "product",
                    "filters": {},
                    "input_evidence_ids": ["run-1:E1"],
                    "candidates": [{"dimension": "product", "element": "2"}],
                },
            },
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_prod_electronics"]})

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "inventory",
            "dimension": "product",
            "element": "electronics",
            "evidence_ids": ["run-1:E1", "run-1:E2_product"],
        }),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "ACTION_SCHEMA_INVALID"
    assert "element=2" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0


def test_data_tool_missing_evidence_id_is_typed_error() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)

    result = middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": []}),
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "EVIDENCE_MISSING"
    assert context.failed is True
    assert writer.steps[-1]["action"] == "detect_anomaly"


def test_recoverable_tool_precondition_error_does_not_fail_run() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    middleware = GuardMiddleware(context)

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "campaign",
            "dimension": "channel",
            "element": "paid_ads",
            "evidence_ids": ["run-1:E2"],
        }),
        lambda request: _message(
            request,
            {
                "observation": {
                    "action_name": "fetch_related_signal",
                    "ok": False,
                    "error_code": "EVIDENCE_MISSING",
                    "message": "guard-passed current-run evidence is required",
                },
                "evidence_ids": [],
            },
        ),
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "EVIDENCE_MISSING"
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0
    assert writer.steps[-1]["error_code"] == "EVIDENCE_MISSING"


def test_no_anomaly_e1_blocks_downstream_tools_before_handler() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.repository = _Repository(
        [
            {
                "evidence_id": "run-1:E1",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {"is_anomaly": False},
            }
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E2"]})

    result = middleware.wrap_tool_call(
        _request("drilldown_dimension", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "dimension": "channel",
            "evidence_ids": ["run-1:E1"],
        }),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "NO_ANOMALY_CONTRACT_VIOLATED"
    assert "stop without drilldown" in payload["observation"]["message"]
    assert called is False
    assert context.failed is True
    assert context.error_code == "NO_ANOMALY_CONTRACT_VIOLATED"
    assert context.step_count == 0
    assert context.query_count == 0
    assert writer.steps[-1]["error_code"] == "NO_ANOMALY_CONTRACT_VIOLATED"


def test_existing_e3_blocks_additional_signal_fetch_before_e4_without_budget() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_channel", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_category", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E3_ch_paid_ads", "run_id": "run-1", "guard_status": "passed"},
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E3_ch_social"]})

    result = middleware.wrap_tool_call(
        _request("fetch_related_signal", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "signal_type": "campaign",
            "dimension": "channel",
            "element": "social",
            "evidence_ids": ["run-1:E1", "run-1:E2_channel"],
        }),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "E3_ALREADY_EXISTS"
    message = payload["observation"]["message"]
    assert "run-1:E3_ch_paid_ads already exists" in message
    assert "calculate_contribution" in message
    assert "['run-1:E1', 'run-1:E2_channel', 'run-1:E3_ch_paid_ads']" in message
    assert "E2_category" not in message
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0


def test_placeholder_evidence_ids_do_not_trip_budget_before_typed_precondition_error() -> None:
    writer = _TraceWriter()
    context = _context(writer, max_steps=0)
    middleware = GuardMiddleware(context)

    result = middleware.wrap_tool_call(
        _request("drilldown_dimension", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "dimension": "channel",
            "evidence_ids": ["E1"],
        }),
        lambda request: _message(
            request,
            {
                "observation": {
                    "action_name": "drilldown_dimension",
                    "ok": False,
                    "error_code": "EVIDENCE_MISSING",
                    "message": "guard-passed current-run evidence is required",
                },
                "evidence_ids": [],
            },
        ),
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "EVIDENCE_MISSING"
    assert context.failed is False
    assert context.budget_exhausted_once is False


def test_calculate_contribution_rejects_dimension_that_does_not_match_e3() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_category", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
            {
                "evidence_id": "run-1:E3_prod_2",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {"dimension": "product", "element": "2"},
            },
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E4"]})

    result = middleware.wrap_tool_call(
        _request("calculate_contribution", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "dimension": "category",
            "element": "electronics",
            "evidence_ids": ["run-1:E1", "run-1:E2_category", "run-1:E3_prod_2"],
        }),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "EVIDENCE_MISSING"
    assert "dimension=product" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0


def test_calculate_contribution_requires_e2_alias_matching_e3_family() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    context.repository = _Repository(
        [
            {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_category", "run_id": "run-1", "guard_status": "passed"},
            {"evidence_id": "run-1:E2_product", "run_id": "run-1", "guard_status": "passed"},
            {
                "evidence_id": "run-1:E3_prod_2",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {"dimension": "product", "element": "2"},
            },
        ]
    )
    middleware = GuardMiddleware(context)
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E4"]})

    result = middleware.wrap_tool_call(
        _request("calculate_contribution", {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "dimension": "product",
            "element": "2",
            "evidence_ids": ["run-1:E1", "run-1:E2_category", "run-1:E3_prod_2"],
        }),
        handler,
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "EVIDENCE_MISSING"
    assert "E2_product" in payload["observation"]["message"]
    assert called is False
    assert context.failed is False
    assert context.step_count == 0
    assert context.query_count == 0


def test_trace_step_persists_token_usage() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    MetricRCATokenUsageCallback(context).on_llm_end(
        LLMResult(
            generations=[],
            llm_output={
                "token_usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 1},
                    "provider_payload": "x" * 1000,
                }
            },
        )
    )
    middleware = GuardMiddleware(context)

    middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        lambda request: _message(request, {"observation": {"ok": True, "evidence_ids": ["run-1:E1"]}, "evidence_ids": ["run-1:E1"]}),
    )

    assert writer.steps[-1]["token_usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def test_token_usage_normalizes_openai_compatible_input_output_names() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    MetricRCATokenUsageCallback(context).on_llm_end(
        LLMResult(
            generations=[],
            llm_output={
                "usage": {
                    "input_tokens": 8,
                    "output_tokens": 4,
                    "provider_payload": {"ignored": "x" * 1000},
                }
            },
        )
    )
    middleware = GuardMiddleware(context)

    middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        lambda request: _message(request, {"observation": {"ok": True, "evidence_ids": ["run-1:E1"]}, "evidence_ids": ["run-1:E1"]}),
    )

    assert writer.steps[-1]["token_usage"] == {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}


def _context(
    writer: "_TraceWriter",
    *,
    max_query: int = 12,
    max_steps: int = 8,
    max_drilldown_depth: int = 2,
) -> RunGuardContext:
    return RunGuardContext(
        run_id="run-1",
        settings=SimpleNamespace(
            max_steps=max_steps,
            max_query=max_query,
            max_drilldown_depth=max_drilldown_depth,
        ),
        trace_writer=writer,
        tool_arg_schemas={
            "detect_anomaly": DetectAnomalyIn,
            "drilldown_dimension": DrilldownDimensionIn,
            "fetch_related_signal": FetchRelatedSignalIn,
            "calculate_contribution": CalculateContributionIn,
            "rank_root_causes": RankRootCausesIn,
        },
    )


def _request(name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": "call-1"})


def _message(request, payload: dict) -> ToolMessage:
    return ToolMessage(content=json.dumps(payload), tool_call_id=request.tool_call["id"], name=request.tool_call["name"])


class _TraceWriter:
    def __init__(self) -> None:
        self.steps = []

    def write_step(self, **kwargs) -> None:
        self.steps.append(kwargs)


class _Repository:
    def __init__(self, evidences: list[dict]) -> None:
        self.evidences = {row["evidence_id"]: dict(row) for row in evidences}

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict | None:
        row = self.evidences.get(evidence_id)
        if row and row.get("run_id") == run_id:
            return row
        return None

    def get_evidences(self, run_id: str) -> list[dict]:
        return [row for row in self.evidences.values() if row.get("run_id") == run_id]

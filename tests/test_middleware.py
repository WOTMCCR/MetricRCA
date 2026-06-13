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
from metric_rca.agent.middleware import GuardMiddleware, MetricRCATokenUsageCallback, RunGuardContext


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


def test_budget_exhausted_then_data_tool_attempt_fails_run() -> None:
    writer = _TraceWriter()
    context = _context(writer, max_query=0)
    middleware = GuardMiddleware(context)

    result = middleware.wrap_tool_call(
        _request("drilldown_dimension", {"metric_id": "gmv", "target_date": "2026-06-05", "dimension": "channel", "evidence_ids": ["run-1:E1"]}),
        lambda request: _message(request, {"observation": {"ok": True}, "evidence_ids": ["run-1:E2"]}),
    )

    payload = json.loads(result.content)
    assert payload["observation"]["error_code"] == "BUDGET_EXCEEDED"
    assert context.failed is True
    assert context.error_code == "BUDGET_EXCEEDED"


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

    assert json.loads(rank.content)["observation"]["ok"] is True
    assert json.loads(plan.content)["observation"]["ok"] is True
    assert json.loads(rejected.content)["observation"]["error_code"] == "BUDGET_EXCEEDED"
    assert context.failed is True
    assert context.error_code == "BUDGET_EXCEEDED"


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


def test_trace_step_persists_token_usage() -> None:
    writer = _TraceWriter()
    context = _context(writer)
    MetricRCATokenUsageCallback(context).on_llm_end(
        LLMResult(generations=[], llm_output={"token_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
    )
    middleware = GuardMiddleware(context)

    middleware.wrap_tool_call(
        _request("detect_anomaly", {"metric_id": "gmv", "target_date": "2026-06-05"}),
        lambda request: _message(request, {"observation": {"ok": True, "evidence_ids": ["run-1:E1"]}, "evidence_ids": ["run-1:E1"]}),
    )

    assert writer.steps[-1]["token_usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def _context(writer: "_TraceWriter", *, max_query: int = 12, max_steps: int = 8) -> RunGuardContext:
    return RunGuardContext(
        run_id="run-1",
        settings=SimpleNamespace(max_steps=max_steps, max_query=max_query, max_drilldown_depth=2),
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

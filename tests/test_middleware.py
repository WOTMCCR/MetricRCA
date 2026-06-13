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
    assert context.failed is True
    assert writer.steps[-1]["error_code"] == "ACTION_SCHEMA_INVALID"


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
    assert context.failed is True


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


def _context(writer: "_TraceWriter", *, max_query: int = 12) -> RunGuardContext:
    return RunGuardContext(
        run_id="run-1",
        settings=SimpleNamespace(max_steps=8, max_query=max_query, max_drilldown_depth=2),
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

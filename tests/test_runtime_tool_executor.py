from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import Field

from metric_rca.domain.models import Observation, StrictModel
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_models import RcaAction
from metric_rca.runtime.run_context import RunContext
from metric_rca.runtime.sdk_tools import (
    RCA_TOOL_NAMES,
    MetricRCAToolHandler,
    ToolExecutionResult,
    ToolExecutor,
    build_default_tool_handlers,
)


class _DrilldownArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date
    dimension: str
    evidence_ids: list[str]
    filters: dict[str, str] = Field(default_factory=dict)


class _FetchArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date
    signal_type: str
    dimension: str
    element: str
    evidence_ids: list[str]
    filters: dict[str, str] = Field(default_factory=dict)


class _CalculateArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date
    dimension: str
    element: str
    evidence_ids: list[str]
    filters: dict[str, str] = Field(default_factory=dict)


class _RankArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date


def test_default_tool_registry_covers_full_rca_action_space() -> None:
    assert RCA_TOOL_NAMES == {
        "detect_anomaly",
        "drilldown_dimension",
        "fetch_related_signal",
        "calculate_contribution",
        "rank_root_causes",
    }
    assert set(build_default_tool_handlers()) == RCA_TOOL_NAMES


def test_tool_executor_injects_run_id_and_evidence_ids() -> None:
    captured: dict[str, Any] = {}

    def handler(args: _DrilldownArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="drilldown_dimension", ok=True, evidence_ids=["run-1:E2_channel"]),
            evidence_ids=["run-1:E2_channel"],
        )

    executor = ToolExecutor(
        dependencies=object(),
        handlers={
            "drilldown_dimension": MetricRCAToolHandler(args_model=_DrilldownArgs, call=handler),
        },
    )
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A2",
        kind="drilldown_dimension",
        args={
            "run_id": "attacker-run",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "dimension": "channel",
        },
        requires=["E1"],
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured["run_id"] == "run-1"
    assert captured["evidence_ids"] == ["run-1:E1"]


def test_tool_executor_does_not_inject_evidence_ids_into_rank_action() -> None:
    captured: dict[str, Any] = {}

    def handler(args: _RankArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="rank_root_causes", ok=True, evidence_ids=["run-1:E_rank"]),
            evidence_ids=["run-1:E_rank"],
        )

    executor = ToolExecutor(
        dependencies=object(),
        handlers={
            "rank_root_causes": MetricRCAToolHandler(args_model=_RankArgs, call=handler),
        },
    )
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A5",
        kind="rank_root_causes",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5)},
        requires=["E1", "E2_channel", "E3", "E4"],
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_paid_ads", "run-1:E4"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured == {
        "run_id": "run-1",
        "metric_id": "gmv",
        "target_date": "2026-06-05",
    }


def test_tool_executor_does_not_swallow_untyped_handler_exception() -> None:
    def handler(args: _RankArgs, dependencies: object) -> ToolExecutionResult:
        raise RuntimeError("repository unavailable")

    executor = ToolExecutor(
        dependencies=object(),
        handlers={
            "rank_root_causes": MetricRCAToolHandler(args_model=_RankArgs, call=handler),
        },
    )
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A5",
        kind="rank_root_causes",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5)},
    )

    with pytest.raises(RuntimeError, match="repository unavailable"):
        executor.execute(ctx, action, EvidenceGraph(run_id="run-1"))


def test_tool_executor_resolves_dynamic_element_from_top_drilldown_candidate() -> None:
    captured: dict[str, Any] = {}

    def handler(args: _FetchArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="fetch_related_signal", ok=True, evidence_ids=["run-1:E3_ch_organic"]),
            evidence_ids=["run-1:E3_ch_organic"],
        )

    repository = _Repository(
        {
            "run-1:E2_channel": {
                "evidence_id": "run-1:E2_channel",
                "guard_status": "passed",
                "result_summary": {"candidates": [{"element": "organic"}, {"element": "paid_ads"}]},
            }
        }
    )
    executor = ToolExecutor(
        dependencies=object(),
        handlers={
            "fetch_related_signal": MetricRCAToolHandler(args_model=_FetchArgs, call=handler),
        },
    )
    ctx = RunContext(
        run_id="run-1",
        metric_id="uv",
        target_date=date(2026, 6, 5),
        repository=repository,
    )
    action = RcaAction(
        action_id="A3",
        kind="fetch_related_signal",
        args={
            "metric_id": "uv",
            "target_date": date(2026, 6, 5),
            "signal_type": "campaign",
            "dimension": "channel",
            "element": None,
        },
        requires=["E1", "E2_channel"],
        dynamic=True,
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_channel"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured["element"] == "organic"
    assert captured["evidence_ids"] == ["run-1:E1", "run-1:E2_channel"]


def test_tool_executor_resolves_signal_first_dynamic_element_from_signal_anomaly() -> None:
    captured: dict[str, Any] = {}

    def handler(args: _FetchArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="fetch_related_signal", ok=True, evidence_ids=["run-1:E3_ch_b"]),
            evidence_ids=["run-1:E3_ch_b"],
        )

    repository = _SignalRepository(
        {
            "run-1:E2_channel": {
                "evidence_id": "run-1:E2_channel",
                "guard_status": "passed",
                "result_summary": {
                    "candidates": [
                        {"element": "a"},
                        {"element": "b"},
                    ]
                },
            }
        }
    )
    executor = ToolExecutor(
        dependencies=SimpleNamespace(
            repository=repository,
            renderer=SQLRenderer(),
            metric_service=_MetricService(),
            settings=SimpleNamespace(signal_metric_by_type={"campaign": "gmv"}),
        ),
        handlers={
            "fetch_related_signal": MetricRCAToolHandler(args_model=_FetchArgs, call=handler),
        },
    )
    ctx = RunContext(
        run_id="run-1",
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        repository=repository,
    )
    action = RcaAction(
        action_id="A3",
        kind="fetch_related_signal",
        args={
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "signal_type": "campaign",
            "dimension": "channel",
            "element": None,
            "element_selection": "signal_anomaly",
        },
        requires=["E1", "E2_channel"],
        dynamic=True,
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_channel"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured["element"] == "b"
    assert "element_selection" not in captured
    assert repository.executed_signal_queries == 4


def test_tool_executor_resolves_refund_quality_dynamic_element_from_signal_level() -> None:
    captured: dict[str, Any] = {}

    def handler(args: _FetchArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="fetch_related_signal", ok=True, evidence_ids=["run-1:E3_prod_1"]),
            evidence_ids=["run-1:E3_prod_1"],
        )

    repository = _SignalLevelRepository(
        {
            "run-1:E2_product": {
                "evidence_id": "run-1:E2_product",
                "guard_status": "passed",
                "result_summary": {
                    "candidates": [
                        {"element": "5"},
                        {"element": "6"},
                        {"element": "1"},
                    ]
                },
            }
        }
    )
    executor = ToolExecutor(
        dependencies=SimpleNamespace(
            repository=repository,
            renderer=SQLRenderer(),
            settings=SimpleNamespace(signal_metric_by_type={"refund_quality": "complaint_rate"}),
        ),
        handlers={
            "fetch_related_signal": MetricRCAToolHandler(args_model=_FetchArgs, call=handler),
        },
    )
    ctx = RunContext(
        run_id="run-1",
        metric_id="refund_rate",
        target_date=date(2026, 6, 5),
        repository=repository,
    )
    action = RcaAction(
        action_id="A3",
        kind="fetch_related_signal",
        args={
            "metric_id": "refund_rate",
            "target_date": date(2026, 6, 5),
            "signal_type": "refund_quality",
            "dimension": "product",
            "element": None,
            "element_selection": "signal_level",
        },
        requires=["E1", "E2_product"],
        dynamic=True,
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_product"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured["element"] == "1"
    assert "element_selection" not in captured
    assert repository.executed_signal_query is True


def test_tool_executor_resolves_dynamic_contribution_element_from_e3_signal() -> None:
    captured: dict[str, Any] = {}

    def handler(args: _CalculateArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="calculate_contribution", ok=True, evidence_ids=["run-1:E4"]),
            evidence_ids=["run-1:E4"],
        )

    repository = _Repository(
        {
            "run-1:E2_product": {
                "evidence_id": "run-1:E2_product",
                "guard_status": "passed",
                "result_summary": {"candidates": [{"element": "5"}, {"element": "1"}]},
            },
            "run-1:E3_prod_1": {
                "evidence_id": "run-1:E3_prod_1",
                "guard_status": "passed",
                "result_summary": {"dimension": "product", "element": "1"},
            },
        }
    )
    executor = ToolExecutor(
        dependencies=object(),
        handlers={
            "calculate_contribution": MetricRCAToolHandler(args_model=_CalculateArgs, call=handler),
        },
    )
    ctx = RunContext(
        run_id="run-1",
        metric_id="refund_rate",
        target_date=date(2026, 6, 5),
        repository=repository,
    )
    action = RcaAction(
        action_id="A4",
        kind="calculate_contribution",
        args={
            "metric_id": "refund_rate",
            "target_date": date(2026, 6, 5),
            "dimension": "product",
            "element": None,
            "element_selection": "signal_anomaly",
        },
        requires=["E1", "E2_product", "E3"],
        dynamic=True,
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_product", "run-1:E3_prod_1"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured["element"] == "1"
    assert "element_selection" not in captured


def test_tool_executor_fails_fast_when_dynamic_element_cannot_be_resolved() -> None:
    executor = ToolExecutor(
        dependencies=object(),
        handlers={
            "fetch_related_signal": MetricRCAToolHandler(
                args_model=_FetchArgs,
                call=lambda args, dependencies: ToolExecutionResult(
                    observation=Observation(action_name="fetch_related_signal", ok=True)
                ),
            ),
        },
    )
    ctx = RunContext(run_id="run-1", metric_id="uv", target_date=date(2026, 6, 5), repository=_Repository({}))
    action = RcaAction(
        action_id="A3",
        kind="fetch_related_signal",
        args={
            "metric_id": "uv",
            "target_date": date(2026, 6, 5),
            "signal_type": "campaign",
            "dimension": "channel",
            "element": None,
        },
        requires=["E1", "E2_channel"],
        dynamic=True,
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_channel"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is False
    assert result.observation.error_code == "DYNAMIC_ACTION_UNRESOLVED"


class _Repository:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self._rows = rows

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, object] | None:
        row = self._rows.get(evidence_id)
        if row is None or not str(row.get("evidence_id", "")).startswith(f"{run_id}:"):
            return None
        return row

    def get_evidences(self, run_id: str) -> list[dict[str, object]]:
        return [
            row
            for row in self._rows.values()
            if str(row.get("evidence_id", "")).startswith(f"{run_id}:")
        ]


class _SignalRepository(_Repository):
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        super().__init__(rows)
        self.executed_signal_queries = 0

    def execute_plan(self, plan, *, run_id: str):
        assert plan.guard_status == "passed"
        assert run_id == "run-1"
        self.executed_signal_queries += 1
        params = getattr(plan, "params", {})
        element = str(params.get("filter_channel") or params.get("filter_product") or "")
        if "baseline_d0" in params:
            if element == "b":
                value = 100.0
            else:
                value = 100.0
            return SimpleNamespace(
                rows=[
                    {"business_date": date(2026, 5, 29), "metric_value": value},
                    {"business_date": date(2026, 5, 22), "metric_value": value},
                    {"business_date": date(2026, 5, 15), "metric_value": value},
                    {"business_date": date(2026, 5, 8), "metric_value": value},
                ],
                row_count=4,
                latency_ms=1,
            )
        value = 40.0 if element == "b" else 95.0
        return SimpleNamespace(
            rows=[{"metric_value": value}],
            row_count=3,
            latency_ms=1,
        )


class _SignalLevelRepository(_Repository):
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        super().__init__(rows)
        self.executed_signal_query = False

    def execute_plan(self, plan, *, run_id: str):
        assert plan.guard_status == "passed"
        assert run_id == "run-1"
        self.executed_signal_query = True
        return SimpleNamespace(
            rows=[
                {"product": "5", "metric_value": 0.6667},
                {"product": "6", "metric_value": 0.6667},
                {"product": "1", "metric_value": 0.8571},
            ],
            row_count=3,
            latency_ms=1,
        )


class _MetricService:
    def get_metric_definition(self, metric_id: str):
        from metric_rca.domain.models import MetricDefinition

        return MetricDefinition(
            metric_id=metric_id,
            display_name=metric_id,
            formula="metric_value",
            source_table="fact_order",
            allowed_dimensions=["channel", "product"],
            higher_is_better=True,
        )

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
from metric_rca.agent.tools.schemas import SelectSignalElementArgs
from metric_rca.agent.tools.select_signal_element import select_signal_element


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


class _MergeArgs(StrictModel):
    run_id: str
    metric_id: str
    target_date: date
    source_evidence_aliases: list[str]


def test_default_tool_registry_covers_full_rca_action_space() -> None:
    assert RCA_TOOL_NAMES == {
        "detect_anomaly",
        "drilldown_dimension",
        "select_signal_element",
        "fetch_related_signal",
        "calculate_contribution",
        "merge_contribution_sets",
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


def test_tool_executor_passes_merge_source_aliases_without_evidence_id_injection() -> None:
    captured: dict[str, Any] = {}

    def handler(args: _MergeArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="merge_contribution_sets", ok=True, evidence_ids=["run-1:E4"]),
            evidence_ids=["run-1:E4"],
        )

    executor = ToolExecutor(
        dependencies=object(),
        handlers={
            "merge_contribution_sets": MetricRCAToolHandler(args_model=_MergeArgs, call=handler),
        },
    )
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A7",
        kind="merge_contribution_sets",
        args={
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "source_evidence_aliases": ["E4_channel", "E4_category"],
        },
        requires=["E4_channel", "E4_category"],
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E4_channel", "run-1:E4_category"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured == {
        "run_id": "run-1",
        "metric_id": "gmv",
        "target_date": "2026-06-05",
        "source_evidence_aliases": ["E4_channel", "E4_category"],
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


def test_tool_executor_resolves_top_candidate_dynamic_element_from_selection_evidence() -> None:
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
            },
            "run-1:E_select_channel": {
                "evidence_id": "run-1:E_select_channel",
                "guard_status": "passed",
                "result_summary": {"dimension": "channel", "selected_element": "organic"},
            },
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
        requires=["E1", "E2_channel", "E_select_channel"],
        dynamic=True,
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_channel", "run-1:E_select_channel"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured["element"] == "organic"
    assert captured["evidence_ids"] == ["run-1:E1", "run-1:E2_channel", "run-1:E_select_channel"]


def test_tool_executor_injects_only_requested_lane_scoped_e3_evidence() -> None:
    captured: dict[str, Any] = {}

    def handler(args: _CalculateArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="calculate_contribution", ok=True, evidence_ids=["run-1:E4_channel_conversion"]),
            evidence_ids=["run-1:E4_channel_conversion"],
        )

    executor = ToolExecutor(
        dependencies=object(),
        handlers={
            "calculate_contribution": MetricRCAToolHandler(args_model=_CalculateArgs, call=handler),
        },
    )
    ctx = RunContext(run_id="run-1", metric_id="net_gmv", target_date=date(2026, 5, 29))
    action = RcaAction(
        action_id="A10",
        kind="calculate_contribution",
        args={
            "metric_id": "net_gmv",
            "target_date": date(2026, 5, 29),
            "dimension": "channel",
            "element": "paid_ads",
        },
        requires=["E1", "E2_channel", "E3_ch_conversion"],
    )
    graph = EvidenceGraph(
        run_id="run-1",
        evidence_ids=[
            "run-1:E1",
            "run-1:E2_channel",
            "run-1:E3_ch_campaign_paid_ads",
            "run-1:E3_ch_conversion_paid_ads",
        ],
    )

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured["evidence_ids"] == [
        "run-1:E1",
        "run-1:E2_channel",
        "run-1:E3_ch_conversion_paid_ads",
    ]


def test_tool_executor_runs_select_action_without_element_resolution_and_preserves_selection_mode() -> None:
    captured: dict[str, Any] = {}

    def handler(args: SelectSignalElementArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="select_signal_element", ok=True, evidence_ids=["run-1:E_select_channel"]),
            evidence_ids=["run-1:E_select_channel"],
        )

    executor = ToolExecutor(
        dependencies=object(),
        handlers={
            "select_signal_element": MetricRCAToolHandler(args_model=SelectSignalElementArgs, call=handler),
        },
    )
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A3",
        kind="select_signal_element",
        args={
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "signal_type": "campaign",
            "dimension": "channel",
            "element_selection": "signal_anomaly",
        },
        requires=["E1", "E2_channel"],
        dynamic=True,
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_channel"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured["run_id"] == "run-1"
    assert captured["evidence_ids"] == ["run-1:E1", "run-1:E2_channel"]
    assert captured["element_selection"] == "signal_anomaly"


def test_tool_executor_strips_gate_only_scope_policy_before_tool_schema_validation() -> None:
    captured: dict[str, dict[str, Any]] = {}

    def select_handler(args: SelectSignalElementArgs, dependencies: object) -> ToolExecutionResult:
        captured["select_signal_element"] = args.model_dump(mode="json")
        return ToolExecutionResult(
            observation=Observation(action_name="select_signal_element", ok=True, evidence_ids=["run-1:E_select_channel_conv"]),
            evidence_ids=["run-1:E_select_channel_conv"],
        )

    def fetch_handler(args: _FetchArgs, dependencies: object) -> ToolExecutionResult:
        captured["fetch_related_signal"] = args.model_dump(mode="json")
        return ToolExecutionResult(
            observation=Observation(action_name="fetch_related_signal", ok=True, evidence_ids=["run-1:E3_ch_conversion"]),
            evidence_ids=["run-1:E3_ch_conversion"],
        )

    def calculate_handler(args: _CalculateArgs, dependencies: object) -> ToolExecutionResult:
        captured["calculate_contribution"] = args.model_dump(mode="json")
        return ToolExecutionResult(
            observation=Observation(action_name="calculate_contribution", ok=True, evidence_ids=["run-1:E4_channel_conversion"]),
            evidence_ids=["run-1:E4_channel_conversion"],
        )

    executor = ToolExecutor(
        dependencies=object(),
        handlers={
            "select_signal_element": MetricRCAToolHandler(args_model=SelectSignalElementArgs, call=select_handler),
            "fetch_related_signal": MetricRCAToolHandler(args_model=_FetchArgs, call=fetch_handler),
            "calculate_contribution": MetricRCAToolHandler(args_model=_CalculateArgs, call=calculate_handler),
        },
    )
    ctx = RunContext(run_id="run-1", metric_id="net_gmv", target_date=date(2026, 6, 5))
    graph = EvidenceGraph(
        run_id="run-1",
        evidence_ids=["run-1:E1", "run-1:E2_channel", "run-1:E3_ch_conversion"],
    )
    common_args = {
        "metric_id": "net_gmv",
        "target_date": date(2026, 6, 5),
        "dimension": "channel",
        "explicit_scope_policy": "global_explanatory",
    }

    results = [
        executor.execute(
            ctx,
            RcaAction(
                action_id="A9",
                kind="select_signal_element",
                args={
                    **common_args,
                    "signal_type": "conversion",
                    "element_selection": "signal_anomaly",
                    "evidence_alias": "E_select_channel_conv",
                },
                requires=["E1", "E2_channel"],
            ),
            graph,
        ),
        executor.execute(
            ctx,
            RcaAction(
                action_id="A10",
                kind="fetch_related_signal",
                args={
                    **common_args,
                    "signal_type": "conversion",
                    "element": "affiliate",
                },
                requires=["E1", "E2_channel"],
            ),
            graph,
        ),
        executor.execute(
            ctx,
            RcaAction(
                action_id="A11",
                kind="calculate_contribution",
                args={
                    **common_args,
                    "element": "affiliate",
                },
                requires=["E1", "E2_channel", "E3_ch_conversion"],
            ),
            graph,
        ),
    ]

    assert [result.observation.ok for result in results] == [True, True, True]
    assert set(captured) == {"select_signal_element", "fetch_related_signal", "calculate_contribution"}
    for args in captured.values():
        assert "explicit_scope_policy" not in args


def test_action_gate_rejects_unknown_explicit_scope_policy_before_tool_boundary() -> None:
    from metric_rca.runtime.action_gate import ActionGate

    ctx = RunContext(
        run_id="run-1",
        metric_id="net_gmv",
        target_date=date(2026, 6, 5),
        explicit_scope={"channel": "paid_ads"},
        scope_mode="explicit_multi_driver",
    )
    action = RcaAction(
        action_id="A9",
        kind="select_signal_element",
        args={
            "metric_id": "net_gmv",
            "target_date": date(2026, 6, 5),
            "signal_type": "conversion",
            "dimension": "channel",
            "element_selection": "signal_anomaly",
            "evidence_alias": "E_select_channel_conv",
            "explicit_scope_policy": "loose",
        },
        requires=["E1", "E2_channel"],
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_channel"])

    decision = ActionGate().validate(ctx, action, graph)

    assert decision.allowed is False
    assert decision.error_code == "ACTION_SCHEMA_INVALID"
    assert "element=paid_ads" in (decision.message or "")


def test_tool_executor_resolves_dynamic_element_from_selection_evidence() -> None:
    captured: dict[str, Any] = {}

    def handler(args: _FetchArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="fetch_related_signal", ok=True, evidence_ids=["run-1:E3_ch_b"]),
            evidence_ids=["run-1:E3_ch_b"],
        )

    repository = _Repository(
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
            },
            "run-1:E_select_channel": {
                "evidence_id": "run-1:E_select_channel",
                "guard_status": "passed",
                "result_summary": {"dimension": "channel", "selected_element": "b"},
            },
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
        requires=["E1", "E2_channel", "E_select_channel"],
        dynamic=True,
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_channel", "run-1:E_select_channel"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured["element"] == "b"
    assert "element_selection" not in captured
    assert captured["evidence_ids"] == ["run-1:E1", "run-1:E2_channel", "run-1:E_select_channel"]


def test_tool_executor_resolves_refund_quality_dynamic_element_from_selection_evidence() -> None:
    captured: dict[str, Any] = {}

    def handler(args: _FetchArgs, dependencies: object) -> ToolExecutionResult:
        captured.update(args.model_dump(mode="json"))
        return ToolExecutionResult(
            observation=Observation(action_name="fetch_related_signal", ok=True, evidence_ids=["run-1:E3_prod_1"]),
            evidence_ids=["run-1:E3_prod_1"],
        )

    repository = _Repository(
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
            },
            "run-1:E_select_product": {
                "evidence_id": "run-1:E_select_product",
                "guard_status": "passed",
                "result_summary": {"dimension": "product", "selected_element": "1"},
            },
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
        requires=["E1", "E2_product", "E_select_product"],
        dynamic=True,
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_product", "run-1:E_select_product"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is True
    assert captured["element"] == "1"
    assert "element_selection" not in captured
    assert captured["evidence_ids"] == ["run-1:E1", "run-1:E2_product", "run-1:E_select_product"]


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
            "run-1:E_select_product": {
                "evidence_id": "run-1:E_select_product",
                "guard_status": "passed",
                "result_summary": {"dimension": "product", "selected_element": "1"},
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
        requires=["E1", "E2_product", "E_select_product", "E3"],
        dynamic=True,
    )
    graph = EvidenceGraph(
        run_id="run-1",
        evidence_ids=["run-1:E1", "run-1:E2_product", "run-1:E_select_product", "run-1:E3_prod_1"],
    )

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
        requires=["E1", "E2_channel", "E_select_channel"],
        dynamic=True,
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1", "run-1:E2_channel", "run-1:E_select_channel"])

    result = executor.execute(ctx, action, graph)

    assert result.observation.ok is False
    assert result.observation.error_code == "DYNAMIC_ACTION_UNRESOLVED"


def test_select_signal_element_uses_grouped_queries_not_per_candidate_queries() -> None:
    repository = _GroupedSelectionRepository(
        {
            "run-1:E1": {
                "evidence_id": "run-1:E1",
                "guard_status": "passed",
                "result_summary": {"is_anomaly": True},
            },
            "run-1:E2_channel": {
                "evidence_id": "run-1:E2_channel",
                "guard_status": "passed",
                "result_summary": {
                    "candidates": [{"element": f"ch_{index}"} for index in range(30)]
                },
            },
        }
    )

    result = select_signal_element(
        SelectSignalElementArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            signal_type="campaign",
            dimension="channel",
            evidence_ids=["run-1:E1", "run-1:E2_channel"],
            element_selection="signal_anomaly",
        ),
        repository=repository,
        metric_service=_MetricService(),
        renderer=SQLRenderer(),
        settings=SimpleNamespace(signal_metric_by_type={"campaign": "gmv"}),
    )

    assert result.observation.ok is True
    assert result.observation.payload["selected_element"] == "ch_17"
    assert result.evidences[0].evidence_id == "run-1:E_select_channel"
    assert result.sql_count == 2
    assert repository.executed_signal_queries == 2
    assert repository.persisted_evidence["result_summary"]["candidate_count"] == 30


def test_select_signal_element_persists_policy_alias_for_parallel_same_dimension_lanes() -> None:
    repository = _ParallelSelectionRepository(
        {
            "run-1:E1": {
                "evidence_id": "run-1:E1",
                "guard_status": "passed",
                "result_summary": {"is_anomaly": True},
            },
            "run-1:E2_channel": {
                "evidence_id": "run-1:E2_channel",
                "guard_status": "passed",
                "result_summary": {
                    "candidates": [{"element": "paid_ads"}, {"element": "affiliate"}],
                },
            },
            "run-1:E_select_channel": {
                "evidence_id": "run-1:E_select_channel",
                "run_id": "run-1",
                "guard_status": "passed",
                "result_summary": {
                    "metric_id": "gmv",
                    "signal_type": "campaign",
                    "dimension": "channel",
                    "filters": {},
                    "input_evidence_ids": ["run-1:E1", "run-1:E2_channel"],
                    "selected_element": "paid_ads",
                },
            },
        }
    )

    result = select_signal_element(
        SelectSignalElementArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            signal_type="conversion",
            dimension="channel",
            evidence_ids=["run-1:E1", "run-1:E2_channel"],
            element_selection="signal_anomaly",
            evidence_alias="E_select_channel_conv",
        ),
        repository=repository,
        metric_service=_MetricService(),
        renderer=SQLRenderer(),
        settings=SimpleNamespace(signal_metric_by_type={"conversion": "pay_cvr"}),
    )

    assert result.observation.ok is True
    assert result.evidence_alias == "E_select_channel_conv"
    assert result.evidences[0].evidence_id == "run-1:E_select_channel_conv"
    assert repository.persisted_by_id["run-1:E_select_channel_conv"]["result_summary"]["signal_type"] == "conversion"
    assert repository.persisted_by_id["run-1:E_select_channel"]["result_summary"]["signal_type"] == "campaign"
    assert result.sql_count == 2


def test_select_signal_element_preserves_sql_count_when_persistence_fails_after_queries() -> None:
    repository = _FailingSelectionPersistenceRepository(
        {
            "run-1:E1": {
                "evidence_id": "run-1:E1",
                "guard_status": "passed",
                "result_summary": {"is_anomaly": True},
            },
            "run-1:E2_channel": {
                "evidence_id": "run-1:E2_channel",
                "guard_status": "passed",
                "result_summary": {
                    "candidates": [{"element": "paid_ads"}, {"element": "affiliate"}],
                },
            },
        }
    )

    result = select_signal_element(
        SelectSignalElementArgs(
            run_id="run-1",
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            signal_type="interaction",
            dimension="channel",
            evidence_ids=["run-1:E1", "run-1:E2_channel"],
            element_selection="signal_anomaly",
            evidence_alias="E_select_channel_int",
        ),
        repository=repository,
        metric_service=_MetricService(),
        renderer=SQLRenderer(),
        settings=SimpleNamespace(signal_metric_by_type={"interaction": "gmv"}),
    )

    assert result.observation.ok is False
    assert result.observation.error_code == "SYSTEM_TABLE_WRITE_FAILED"
    assert result.sql_count == 2
    assert repository.executed_signal_queries == 2


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
            metric_family="gmv_family",
            source_table="fact_order",
            allowed_dimensions=["channel", "product"],
            higher_is_better=True,
        )


class _GroupedSelectionRepository(_Repository):
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        super().__init__(rows)
        self.executed_signal_queries = 0
        self.persisted_evidence: dict[str, Any] = {}

    def get_agent_run(self, run_id: str) -> dict[str, object]:
        return {"run_id": run_id, "status": "running", "metric_id": "gmv", "target_date": date(2026, 6, 5)}

    def execute_plan(self, plan, *, run_id: str):
        assert plan.guard_status == "passed"
        self.executed_signal_queries += 1
        if "baseline_d0" in getattr(plan, "params", {}):
            return SimpleNamespace(
                rows=[
                    {
                        "business_date": date(2026, 5, 29),
                        "channel": f"ch_{index}",
                        "metric_value": 100.0,
                    }
                    for index in range(30)
                ],
                row_count=30,
                latency_ms=1,
            )
        return SimpleNamespace(
            rows=[
                {
                    "channel": f"ch_{index}",
                    "metric_value": 30.0 if index == 17 else 95.0,
                }
                for index in range(30)
            ],
            row_count=30,
            latency_ms=1,
        )

    def create_evidence(self, row: dict[str, Any]) -> None:
        self.persisted_evidence = row


class _ParallelSelectionRepository(_GroupedSelectionRepository):
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        super().__init__(rows)
        self.persisted_by_id: dict[str, dict[str, Any]] = {
            str(row["evidence_id"]): dict(row)
            for row in rows.values()
            if row.get("evidence_id")
        }

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, object] | None:
        row = self.persisted_by_id.get(evidence_id)
        if row is None or not str(row.get("evidence_id", "")).startswith(f"{run_id}:"):
            return None
        return row

    def get_evidences(self, run_id: str) -> list[dict[str, object]]:
        return [
            row
            for row in self.persisted_by_id.values()
            if str(row.get("evidence_id", "")).startswith(f"{run_id}:")
        ]

    def execute_plan(self, plan, *, run_id: str):
        assert plan.guard_status == "passed"
        self.executed_signal_queries += 1
        if "baseline_d0" in getattr(plan, "params", {}):
            return SimpleNamespace(
                rows=[
                    {"business_date": date(2026, 5, 29), "channel": "paid_ads", "metric_value": 100.0},
                    {"business_date": date(2026, 5, 29), "channel": "affiliate", "metric_value": 100.0},
                ],
                row_count=2,
                latency_ms=1,
            )
        return SimpleNamespace(
            rows=[
                {"channel": "paid_ads", "metric_value": 96.0},
                {"channel": "affiliate", "metric_value": 60.0},
            ],
            row_count=2,
            latency_ms=1,
        )

    def create_evidence(self, row: dict[str, Any]) -> None:
        evidence_id = str(row["evidence_id"])
        if evidence_id in self.persisted_by_id:
            raise RuntimeError("SYSTEM_TABLE_WRITE_FAILED: duplicate evidence")
        self.persisted_by_id[evidence_id] = row
        self.persisted_evidence = row


class _FailingSelectionPersistenceRepository(_ParallelSelectionRepository):
    def create_evidence(self, row: dict[str, Any]) -> None:
        raise RuntimeError("SYSTEM_TABLE_WRITE_FAILED: injected persistence failure")

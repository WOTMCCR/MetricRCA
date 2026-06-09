from __future__ import annotations

import importlib
import inspect
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import create_engine, text

from metric_rca.config.settings import Settings, get_settings
from metric_rca.data.seed_data import main as seed_main
from metric_rca.domain.models import AgentAction, Evidence, Observation, ReflectionIssue, ReflectionResult, RootCauseCandidate
from metric_rca.repositories.metadata_repository import MetadataRepository
from metric_rca.repositories.metric_repository import MetricRepository
from metric_rca.services.metric_service import MetricService


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_NODE_NAMES = [
    "parse_question",
    "read_memory",
    "plan_init",
    "react_step",
    "execute_tool",
    "attribute_rank",
    "reflection_verify",
    "generate_report",
    "create_tasks",
    "write_memory",
    "error_return",
]


def _settings(**overrides) -> Settings:
    base = get_settings().model_dump()
    base.update({"memory_enabled": False, "memory_required": False})
    base.update(overrides)
    return Settings(**base)


def test_graph_contains_real_stategraph_start_end_nodes_and_conditional_edges() -> None:
    from metric_rca.agent.graph import build_state_graph
    from metric_rca.agent.state import RCAState

    builder = build_state_graph(dependencies=_Dependencies())

    assert isinstance(builder, StateGraph)
    assert builder.state_schema is RCAState
    assert START in {source for source, _ in builder.edges}
    assert END in {target for _, target in builder.edges}
    assert set(REQUIRED_NODE_NAMES).issubset(builder.nodes)
    assert "react_step" in builder.branches
    assert "reflection_verify" in builder.branches


def test_run_rca_invokes_compiled_graph_not_sequential_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    import metric_rca.agent.graph as graph_module

    class Compiled:
        def __init__(self) -> None:
            self.invoked = False

        def invoke(self, state: dict, config: dict | None = None) -> dict:
            self.invoked = True
            return {"run_id": state["run_id"], "status": "succeeded", "compiled_invoked": True}

    compiled = Compiled()
    monkeypatch.setattr(graph_module, "compile_graph", lambda **kwargs: compiled)

    result = graph_module.run_rca(
        "Why did yesterday GMV drop?",
        run_id="run-compiled",
        settings=_settings(),
        dependencies=_Dependencies(),
    )

    assert compiled.invoked is True
    assert result["compiled_invoked"] is True


def test_required_node_files_are_not_reexport_shells() -> None:
    for node_name in REQUIRED_NODE_NAMES:
        module = importlib.import_module(f"metric_rca.agent.nodes.{node_name}")
        node_fn = getattr(module, node_name)
        source = inspect.getsource(module)
        assert node_fn.__module__ == module.__name__
        assert f"def {node_name}" in source
        assert "from metric_rca.agent.graph import" not in source
        assert len([line for line in source.splitlines() if line.strip() and not line.strip().startswith("#")]) >= 8


def test_reducers_accumulate_actions_observations_evidences() -> None:
    from metric_rca.agent.state import RCAState

    annotations = RCAState.__annotations__
    for field in ["actions", "observations", "evidences"]:
        annotation = annotations[field]
        assert getattr(annotation, "__metadata__", ()) != ()
        assert annotation.__metadata__[0].__name__ == "add"


@pytest.mark.integration
def test_gmv_paid_ads_drop_e2e_through_graph_with_E1_to_E4() -> None:
    seed_main()
    settings = _settings(memory_enabled=False)
    repository, metric_service = _real_dependencies(settings)
    try:
        result = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3a-gmv-paid-ads-drop",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
        )
        evidence_ids = [evidence.evidence_id for evidence in result["evidences"]]

        assert result["status"] == "succeeded"
        assert evidence_ids == [
            "p3a-gmv-paid-ads-drop:E1",
            "p3a-gmv-paid-ads-drop:E2",
            "p3a-gmv-paid-ads-drop:E3",
            "p3a-gmv-paid-ads-drop:E4",
        ]
        assert result["candidates"][0].root_cause_type == "campaign_traffic_drop"
        assert result["candidates"][0].evidence_ids == evidence_ids
        assert result["reflection"].passed is True
        assert _observation_payload(result, "fetch_related_signal")["signal_type"] == "campaign"
        assert result["query_count"] <= settings.max_query
        assert metric_service.parse_calls == 1
        persisted = _agent_run(settings, "p3a-gmv-paid-ads-drop")
        assert persisted["status"] == "succeeded"
        assert persisted["error_code"] is None
        assert persisted["finished_at"] is not None
    finally:
        repository.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("question", "run_id", "expected_signal_type", "expected_signal_metric_id", "expected_root_cause_type"),
    [
        (
            "Why did yesterday category=electronics GMV drop?",
            "p3a-gmv-stockout-electronics",
            "inventory",
            "stockout_rate",
            "stockout",
        ),
        (
            "Why did yesterday device=mobile pay conversion rate drop?",
            "p3a-pay-cvr-mobile",
            "conversion",
            "pay_cvr",
            "conversion_drop",
        ),
        (
            "Why did yesterday product=1 refund rate increase?",
            "p3a-refund-quality-product",
            "refund_quality",
            "complaint_rate",
            "complaint_or_quality_issue",
        ),
        (
            "Why did yesterday channel=paid_ads net GMV drop?",
            "p3a-net-gmv-paid-ads",
            "campaign",
            "gmv",
            "campaign_traffic_drop",
        ),
    ],
)
def test_graph_e2e_selects_signal_policy_from_candidate_metric_and_dimension(
    question: str,
    run_id: str,
    expected_signal_type: str,
    expected_signal_metric_id: str,
    expected_root_cause_type: str,
) -> None:
    seed_main()
    settings = _settings(memory_enabled=False)
    repository, metric_service = _real_dependencies(settings)
    try:
        result = _run_live_graph(
            question,
            run_id=run_id,
            settings=settings,
            repository=repository,
            metric_service=metric_service,
        )
        payload = _observation_payload(result, "fetch_related_signal")

        assert result["status"] == "succeeded"
        assert [evidence.evidence_id for evidence in result["evidences"]] == [
            f"{run_id}:E1",
            f"{run_id}:E2",
            f"{run_id}:E3",
            f"{run_id}:E4",
        ]
        assert payload["signal_type"] == expected_signal_type
        assert payload["signal_metric_id"] == expected_signal_metric_id
        assert result["candidates"][0].root_cause_type == expected_root_cause_type
        assert result["query_count"] <= settings.max_query
    finally:
        repository.close()


@pytest.mark.integration
def test_no_anomaly_generates_no_anomaly_report_skips_attribute_rank_and_create_tasks() -> None:
    seed_main()
    settings = _settings(business_today=date(2026, 6, 5), target_date=date(2026, 6, 4), memory_enabled=False)
    repository, metric_service = _real_dependencies(settings)
    try:
        result = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3a-gmv-no-anomaly",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
        )
        trace_nodes = _trace_nodes(settings, "p3a-gmv-no-anomaly")
        task_count = _count(settings, "operation_task", "run_id", "p3a-gmv-no-anomaly")

        assert result["status"] == "no_anomaly"
        assert [e.evidence_id for e in result["evidences"]] == ["p3a-gmv-no-anomaly:E1"]
        assert result.get("candidates", []) == []
        assert result["report"]["status"] == "no_anomaly"
        assert "attribute_rank" not in trace_nodes
        assert "create_tasks" not in trace_nodes
        assert task_count == 0
        persisted = _agent_run(settings, "p3a-gmv-no-anomaly")
        assert persisted["status"] == "no_anomaly"
        assert persisted["error_code"] is None
        assert persisted["finished_at"] is not None
    finally:
        repository.close()


@pytest.mark.integration
def test_memory_enabled_graph_lifecycle_uses_real_repo_and_finishes() -> None:
    seed_main()
    settings = _settings(memory_enabled=True)
    repository, metric_service = _real_dependencies(settings)
    try:
        result = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3b-memory-enabled-real-repo",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
        )
        persisted = _agent_run(settings, "p3b-memory-enabled-real-repo")

        assert result["status"] == "succeeded"
        assert result["error_code"] is None
        assert persisted["status"] == "succeeded"
        assert persisted["error_code"] is None
        assert persisted["finished_at"] is not None
    finally:
        repository.close()


def test_failed_reflection_routes_error_return_and_no_report() -> None:
    from metric_rca.agent.nodes.attribute_rank import attribute_rank
    from metric_rca.agent.nodes.reflection_verify import reflection_verify
    from metric_rca.agent.graph import route_after_reflection

    state = {
        "run_id": "run-1",
        "status": "running",
        "evidences": [],
        "candidates": [
            RootCauseCandidate(
                root_cause_type="campaign_traffic_drop",
                dimension="channel",
                element="paid_ads",
                contribution_pct=0.9,
                signal_severity=3.0,
                evidence_support=0.9,
                eng_confidence=0.8,
                verdict="confirmed",
                evidence_ids=["other-run:E1"],
            )
        ],
        "repair_count": 1,
    }
    ranked = attribute_rank(state, dependencies=_Dependencies())
    verified = reflection_verify({**state, **ranked}, dependencies=_Dependencies())

    assert verified["reflection"].passed is False
    assert verified["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert route_after_reflection({**state, **ranked, **verified}, dependencies=_Dependencies()) == "error_return"
    assert "report" not in verified


@pytest.mark.integration
def test_trace_step_contiguous_seq_for_every_visited_node() -> None:
    seed_main()
    settings = _settings(memory_enabled=False)
    repository, metric_service = _real_dependencies(settings)
    try:
        _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3a-trace-contiguous",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
        )
        seqs = _trace_seqs(settings, "p3a-trace-contiguous")
        assert seqs == list(range(1, len(seqs) + 1))
    finally:
        repository.close()


@pytest.mark.integration
def test_each_evidence_step_has_sql_audit_row() -> None:
    seed_main()
    settings = _settings(memory_enabled=False)
    repository, metric_service = _real_dependencies(settings)
    try:
        result = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3a-sql-audit",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
        )
        audit_hashes = _audit_hashes(settings, "p3a-sql-audit")
        for evidence in result["evidences"]:
            assert evidence.sql_hash in audit_hashes
    finally:
        repository.close()


def test_tiny_max_steps_stops_by_business_limit_not_GraphRecursionError() -> None:
    from metric_rca.agent.react import next_action

    action = next_action(
        {
            "run_id": "run-1",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "parsed_spec": {"filters": {}},
            "step_count": 1,
            "actions": [],
            "observations": [],
            "evidences": [],
        },
        settings=Settings.model_construct(max_steps=1, llm_required=False, llm_enabled=False),
        metric_service=_MetricService(["channel"]),
    )

    assert action.action == "finish"
    assert action.args["error_code"] == "MAX_STEPS_EXCEEDED"


def test_query_budget_counts_real_execute_plan_calls_and_blocks_report() -> None:
    from metric_rca.agent.nodes.execute_tool import execute_tool

    repo = _CountingBudgetRepository()
    deps = _Dependencies(settings=Settings.model_construct(max_query=3), repository=repo)
    state = {
        "run_id": "run-1",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "actions": [
            {
                "action": "calculate_contribution",
                "args": {
                    "run_id": "run-1",
                    "metric_id": "gmv",
                    "target_date": "2026-06-05",
                    "dimension": "channel",
                    "element": "paid_ads",
                    "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3"],
                    "filters": {},
                },
            }
        ],
        "query_count": 0,
        "evidences": [_evidence("run-1:E1"), _evidence("run-1:E2"), _evidence("run-1:E3")],
    }

    result = execute_tool(state, dependencies=deps)

    assert result["status"] == "failed"
    assert result["error_code"] == "QUERY_BUDGET_EXCEEDED"
    assert result["query_count"] == 3
    assert repo.executed == 3
    assert result["evidences"] == []


def test_runtime_graph_code_does_not_read_anomaly_ground_truth() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in [ROOT / "metric_rca" / "agent" / "graph.py", *list((ROOT / "metric_rca" / "agent" / "nodes").glob("*.py"))]
        if "anomaly_ground_truth" in path.read_text()
    ]
    assert offenders == []


def test_empty_result_does_not_enter_attribute_rank() -> None:
    from metric_rca.agent.graph import route_after_execute_tool

    state = {
        "observations": [Observation(action_name="detect_anomaly", ok=False, error_code="NO_CURRENT_DATA")],
        "error_code": "NO_CURRENT_DATA",
    }

    assert route_after_execute_tool(state, dependencies=_Dependencies()) == "error_return"


def test_graph_and_node_modules_have_no_hardcoded_metric_metadata() -> None:
    forbidden = [
        "MetricDefinition(",
        "METRIC_DEFINITIONS",
        "SCHEMA_CONTEXT",
        "_CHANNELS",
        "_CATEGORIES",
        "paid_ads",
        "organic",
        "affiliate",
        "electronics",
        "fashion",
        "home",
    ]
    offenders: list[str] = []
    paths = [ROOT / "metric_rca" / "agent" / "graph.py", *list((ROOT / "metric_rca" / "agent" / "nodes").glob("*.py"))]
    for path in paths:
        source = path.read_text()
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(ROOT)}:{token}")
    assert offenders == []


def test_graph_e2e_uses_real_metric_service_parse_question_no_mock_planner() -> None:
    graph_source = (ROOT / "metric_rca" / "agent" / "graph.py").read_text()
    node_source = (ROOT / "metric_rca" / "agent" / "nodes" / "parse_question.py").read_text()
    test_source = (ROOT / "tests" / "test_graph.py").read_text()
    forbidden = ["Mock" + "IntentPlanner", "keyword " + "parser", "Parsed" + "Intent("]

    assert "MetricService(" in graph_source
    assert ".parse_question(" in node_source
    assert [token for token in forbidden if token in graph_source or token in node_source or token in test_source] == []


def test_attribute_rank_uses_current_state_evidence_only() -> None:
    from metric_rca.agent.nodes.attribute_rank import attribute_rank

    state = {
        "run_id": "run-1",
        "evidences": [
            _evidence("run-1:E1"),
            _evidence("run-1:E2"),
            _evidence("run-1:E3"),
        ],
        "candidates": [
            RootCauseCandidate(
                root_cause_type="campaign_traffic_drop",
                dimension="channel",
                element="paid_ads",
                contribution_pct=0.9,
                signal_severity=3.0,
                evidence_support=0.9,
                eng_confidence=0.8,
                verdict="confirmed",
                evidence_ids=["run-1:E1", "foreign:E2", "run-1:E3"],
            )
        ],
    }

    result = attribute_rank(state, dependencies=_Dependencies())

    assert result["error_code"] == "EVIDENCE_MISSING"
    assert result.get("candidates", []) == []


def test_memory_enabled_true_without_repo_fails_typed_and_false_does_not_call() -> None:
    from metric_rca.agent.nodes.read_memory import read_memory
    from metric_rca.agent.nodes.write_memory import write_memory

    false_deps = _Dependencies(settings=Settings.model_construct(memory_enabled=False))
    assert read_memory({"run_id": "run-1", "metric_id": "gmv"}, dependencies=false_deps) == {"memory_hits": []}
    assert write_memory({"run_id": "run-1", "status": "succeeded"}, dependencies=false_deps) == {}

    true_deps = _Dependencies(settings=Settings.model_construct(memory_enabled=True, memory_required=True))
    assert read_memory({"run_id": "run-1", "metric_id": "gmv"}, dependencies=true_deps)["error_code"] == "MEMORY_READ_FAILED"
    assert write_memory({"run_id": "run-1", "status": "succeeded"}, dependencies=true_deps)["error_code"] == "MEMORY_WRITE_FAILED"


@pytest.mark.integration
def test_graph_with_memory_hit_reorders_drilldown_but_conclusion_requires_E1_to_E4() -> None:
    from metric_rca.memory.memory_repo import MemoryRepository

    seed_main()
    settings = _settings(memory_enabled=True, memory_required=True)
    _set_metric_dimensions(settings, "gmv", ["category", "channel", "device", "product"])
    repository, metric_service = _real_dependencies(settings)
    memory_repo = MemoryRepository.from_settings(settings)
    try:
        memory_repo.write(
            {
                "layer": "case",
                "mem_key": "gmv|channel",
                "payload": {"dimension": "channel", "root_cause_type": "campaign_traffic_drop"},
                "confidence": 0.95,
                "source": "reflection_verified",
                "version": 1,
                "ttl_days": 30,
                "created_at": datetime(2026, 6, 5, 0, 0, 0),
            }
        )
        result = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3b-memory-category-hit",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
            memory_repo=memory_repo,
        )

        assert result["status"] == "succeeded"
        assert _action_args(result, "drilldown_dimension")["dimension"] == "channel"
        assert result["candidates"][0].root_cause_type == "campaign_traffic_drop"
        assert result["candidates"][0].evidence_ids == [f"p3b-memory-category-hit:E{i}" for i in range(1, 5)]
        assert all(evidence.evidence_id.startswith("p3b-memory-category-hit:") for evidence in result["evidences"])
        assert all(not evidence_id.startswith("memory:") for evidence_id in result["candidates"][0].evidence_ids)
    finally:
        repository.close()
        memory_repo.close()


@pytest.mark.integration
def test_graph_low_confidence_memory_does_not_change_plan() -> None:
    from metric_rca.memory.memory_repo import MemoryRepository

    seed_main()
    settings = _settings(memory_enabled=True, memory_required=True)
    repository, metric_service = _real_dependencies(settings)
    memory_repo = MemoryRepository.from_settings(settings)
    try:
        memory_repo.write(
            {
                "layer": "case",
                "mem_key": "gmv|category",
                "payload": {"dimension": "category"},
                "confidence": 0.10,
                "source": "reflection_verified",
                "version": 1,
                "ttl_days": 30,
                "created_at": datetime(2026, 6, 5, 0, 0, 0),
            }
        )
        result = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3b-memory-low-confidence",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
            memory_repo=memory_repo,
        )

        assert result["status"] == "succeeded"
        assert _action_args(result, "drilldown_dimension")["dimension"] == "channel"
        assert result["candidates"][0].root_cause_type == "campaign_traffic_drop"
    finally:
        repository.close()
        memory_repo.close()


@pytest.mark.integration
def test_graph_expired_memory_does_not_change_plan() -> None:
    from metric_rca.memory.memory_repo import MemoryRepository

    seed_main()
    settings = _settings(memory_enabled=True, memory_required=True)
    repository, metric_service = _real_dependencies(settings)
    memory_repo = MemoryRepository.from_settings(settings)
    try:
        memory_repo.write(
            {
                "layer": "case",
                "mem_key": "gmv|category",
                "payload": {"dimension": "category"},
                "confidence": 0.95,
                "source": "reflection_verified",
                "version": 1,
                "ttl_days": 1,
                "created_at": datetime(2026, 5, 1, 0, 0, 0),
            }
        )
        result = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3b-memory-expired",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
            memory_repo=memory_repo,
        )

        assert result["status"] == "succeeded"
        assert _action_args(result, "drilldown_dimension")["dimension"] == "channel"
        assert result["candidates"][0].root_cause_type == "campaign_traffic_drop"
    finally:
        repository.close()
        memory_repo.close()


@pytest.mark.integration
def test_graph_untrusted_memory_does_not_change_plan() -> None:
    from metric_rca.memory.memory_repo import MemoryRepository

    seed_main()
    settings = _settings(memory_enabled=True, memory_required=True)
    repository, metric_service = _real_dependencies(settings)
    memory_repo = MemoryRepository.from_settings(settings)
    try:
        engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO memory_record (
                          memory_id, layer, mem_key, payload, confidence, source,
                          version, ttl_days, created_at
                        )
                        VALUES (
                          :memory_id, :layer, :mem_key, :payload, :confidence, :source,
                          :version, :ttl_days, :created_at
                        )
                        """
                    ),
                    {
                        "memory_id": "p3b-untrusted-memory",
                        "layer": "case",
                        "mem_key": "gmv|category",
                        "payload": json.dumps({"dimension": "category"}),
                        "confidence": 0.95,
                        "source": "untrusted",
                        "version": 1,
                        "ttl_days": 30,
                        "created_at": datetime(2026, 6, 5, 0, 0, 0),
                    },
                )
        finally:
            engine.dispose()
        result = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3b-memory-untrusted",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
            memory_repo=memory_repo,
        )

        assert result["status"] == "succeeded"
        assert _action_args(result, "drilldown_dimension")["dimension"] == "channel"
        assert result["memory_hits"] == []
    finally:
        repository.close()
        memory_repo.close()


@pytest.mark.integration
def test_repair_new_evidence_is_persisted_and_has_sql_audit() -> None:
    from metric_rca.agent.graph import build_dependencies
    from metric_rca.agent.nodes.attribute_rank import attribute_rank
    from metric_rca.agent.nodes.execute_tool import execute_tool
    from metric_rca.agent.nodes.react_step import react_step
    from metric_rca.agent.nodes.reflection_verify import reflection_verify

    seed_main()
    settings = _settings(memory_enabled=False, max_steps=8)
    repository, metric_service = _real_dependencies(settings)
    deps = build_dependencies(settings=settings, repository=repository, metric_service=metric_service)
    run_id = "p3b-reflection-repair-success"
    try:
        deps.trace_writer.start_run(run_id=run_id, question="Why did yesterday GMV drop?", target_date=settings.target_date)
        deps.trace_writer.set_run_context(run_id=run_id, metric_id="gmv", target_date=settings.target_date)
        state = _initial_running_state(run_id=run_id, settings=settings)
        state = _apply_update(
            state,
            execute_tool(
                {
                    **state,
                    "actions": [
                        AgentAction(
                            action="detect_anomaly",
                            args={"run_id": run_id, "metric_id": "gmv", "target_date": settings.target_date, "filters": {}},
                        )
                    ],
                },
                dependencies=deps,
            ),
        )
        state = _apply_update(
            state,
            execute_tool(
                {
                    **state,
                    "actions": [
                        AgentAction(
                            action="drilldown_dimension",
                            args={
                                "run_id": run_id,
                                "metric_id": "gmv",
                                "target_date": settings.target_date,
                                "dimension": "channel",
                                "evidence_ids": [f"{run_id}:E1"],
                                "filters": {},
                            },
                        )
                    ],
                },
                dependencies=deps,
            ),
        )
        top = state["candidates"][0]
        state = _apply_update(
            state,
            execute_tool(
                {
                    **state,
                    "actions": [
                        AgentAction(
                            action="fetch_related_signal",
                            args={
                                "run_id": run_id,
                                "metric_id": "gmv",
                                "target_date": settings.target_date,
                                "signal_type": "campaign",
                                "dimension": top.dimension,
                                "element": top.element,
                                "evidence_ids": [f"{run_id}:E1", f"{run_id}:E2"],
                            },
                        )
                    ],
                },
                dependencies=deps,
            ),
        )
        state = _apply_update(state, attribute_rank(state, dependencies=deps))
        verified = reflection_verify(state, dependencies=deps)
        state = _apply_update(state, verified)
        assert state["repair_pending"] is True

        state = _apply_update(state, react_step(state, dependencies=deps))
        state = _apply_update(state, execute_tool(state, dependencies=deps))
        state = _apply_update(state, reflection_verify(state, dependencies=deps))

        e4 = next(evidence for evidence in state["evidences"] if evidence.evidence_id == f"{run_id}:E4")
        persisted_e4 = repository.get_evidence(run_id=run_id, evidence_id=e4.evidence_id)
        assert persisted_e4 is not None
        assert persisted_e4["sql_hash"] == e4.sql_hash
        assert isinstance(persisted_e4["query_spec"], dict)
        assert persisted_e4["query_spec"]["metric_id"] == e4.query_spec.metric_id
        assert isinstance(persisted_e4["result_summary"], dict)
        assert persisted_e4["result_summary"]["selected_candidate"]["element"] == top.element
        assert e4.sql_hash in _audit_hashes(settings, run_id)
        assert state["repair_count"] == 1
        assert state["reflection"].passed is True
        assert state["reflection"].repaired is True
    finally:
        repository.close()


def test_repair_pending_does_not_bypass_max_steps() -> None:
    from metric_rca.agent.nodes.react_step import react_step
    from metric_rca.agent.graph import route_after_react

    state = {
        "run_id": "run-1",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "parsed_spec": {"filters": {}},
        "step_count": 3,
        "query_count": 6,
        "repair_pending": True,
        "actions": [],
        "observations": [Observation(action_name="fetch_related_signal", ok=True, evidence_ids=["run-1:E3"])],
        "evidences": [_evidence("run-1:E1"), _evidence("run-1:E2"), _evidence("run-1:E3")],
        "candidates": [
            RootCauseCandidate(
                root_cause_type="campaign_traffic_drop",
                dimension="channel",
                element="paid_ads",
                contribution_pct=0.90,
                signal_severity=0.90,
                evidence_support=1.0,
                eng_confidence=0.90,
                verdict="confirmed",
                evidence_ids=["run-1:E1", "run-1:E2", "run-1:E3"],
            )
        ],
        "reflection": ReflectionResult(
            passed=False,
            issues=[
                ReflectionIssue(
                    check="required_evidence_present",
                    severity="error",
                    by="rule",
                    message="missing E4",
                    suggested_action=AgentAction(
                        action="calculate_contribution",
                        args={
                            "run_id": "run-1",
                            "metric_id": "gmv",
                            "target_date": date(2026, 6, 5),
                            "dimension": "channel",
                            "element": "paid_ads",
                            "evidence_ids": ["run-1:E1", "run-1:E2", "run-1:E3"],
                            "filters": {},
                        },
                    ),
                )
            ],
            repaired=False,
            repair_count=1,
        ),
    }
    deps = _Dependencies(settings=Settings.model_construct(max_steps=3, max_query=12, llm_enabled=False, llm_required=False))

    update = react_step(state, dependencies=deps)
    next_state = _apply_update(state, update)

    assert update["actions"][0].action == "finish"
    assert update["error_code"] == "MAX_STEPS_EXCEEDED"
    assert route_after_react(next_state, dependencies=deps) == "error_return"


@pytest.mark.integration
def test_graph_reflection_repair_failed_no_report_no_task() -> None:
    seed_main()
    settings = _settings(memory_enabled=False, max_steps=2)
    repository, metric_service = _real_dependencies(settings)
    try:
        result = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3b-reflection-repair-failed",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
        )

        assert result["status"] == "failed"
        assert result["error_code"] == "MAX_STEPS_EXCEEDED"
        assert result.get("report") is None
        assert _count(settings, "operation_task", "run_id", "p3b-reflection-repair-failed") == 0
    finally:
        repository.close()


@pytest.mark.integration
def test_graph_no_anomaly_still_has_only_E1_no_task_no_attribute_rank() -> None:
    seed_main()
    settings = _settings(business_today=date(2026, 6, 5), target_date=date(2026, 6, 4), memory_enabled=False)
    repository, metric_service = _real_dependencies(settings)
    try:
        result = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3b-no-anomaly-regression",
            settings=settings,
            repository=repository,
            metric_service=metric_service,
        )
        trace_nodes = _trace_nodes(settings, "p3b-no-anomaly-regression")

        assert result["status"] == "no_anomaly"
        assert [e.evidence_id for e in result["evidences"]] == ["p3b-no-anomaly-regression:E1"]
        assert result.get("candidates", []) == []
        assert "attribute_rank" not in trace_nodes
        assert "create_tasks" not in trace_nodes
        assert _count(settings, "operation_task", "run_id", "p3b-no-anomaly-regression") == 0
    finally:
        repository.close()


@pytest.mark.integration
def test_p3_complete_runs_all_p3a_and_p3b_paths_without_known_shortcuts() -> None:
    seed_main()
    success_settings = _settings(memory_enabled=False)
    no_anomaly_settings = _settings(business_today=date(2026, 6, 5), target_date=date(2026, 6, 4), memory_enabled=False)
    success_repo, success_metric_service = _real_dependencies(success_settings)
    no_anomaly_repo, no_anomaly_metric_service = _real_dependencies(no_anomaly_settings)
    try:
        success = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3b-complete-success",
            settings=success_settings,
            repository=success_repo,
            metric_service=success_metric_service,
        )
        no_anomaly = _run_live_graph(
            "Why did yesterday GMV drop?",
            run_id="p3b-complete-no-anomaly",
            settings=no_anomaly_settings,
            repository=no_anomaly_repo,
            metric_service=no_anomaly_metric_service,
        )

        assert success["status"] == "succeeded"
        assert success["reflection"].passed is True
        assert len(success["evidences"]) == 4
        assert no_anomaly["status"] == "no_anomaly"
        assert len(no_anomaly["evidences"]) == 1
        assert "known_shortcuts" not in success
        assert "known_shortcuts" not in no_anomaly
    finally:
        success_repo.close()
        no_anomaly_repo.close()


def _run_live_graph(
    question: str,
    *,
    run_id: str,
    settings: Settings,
    repository: MetricRepository,
    metric_service: MetricService,
    memory_repo: Any | None = None,
) -> dict:
    from metric_rca.agent.graph import run_rca

    return run_rca(
        question,
        run_id=run_id,
        settings=settings,
        repository=repository,
        metric_service=metric_service,
        memory_repo=memory_repo,
    )


def _initial_running_state(*, run_id: str, settings: Settings) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "question": "Why did yesterday GMV drop?",
        "target_date": settings.target_date,
        "metric_id": "gmv",
        "parsed_spec": {
            "metric_id": "gmv",
            "target_date": str(settings.target_date),
            "dimension": None,
            "element": None,
            "filters": {},
        },
        "memory_hits": [],
        "actions": [],
        "observations": [],
        "evidences": [],
        "candidates": [],
        "step_count": 0,
        "query_count": 0,
        "drilldown_depth": 0,
        "repair_count": 0,
        "repair_pending": False,
        "status": "running",
        "error_code": None,
    }


def _apply_update(state: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(state)
    for key in ["actions", "observations", "evidences"]:
        if key in update:
            merged[key] = [*merged.get(key, []), *update[key]]
    for key, value in update.items():
        if key not in {"actions", "observations", "evidences"}:
            merged[key] = value
    return merged


def _real_dependencies(settings: Settings) -> tuple[MetricRepository, "_CountingMetricService"]:
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    repository = MetricRepository.from_settings(settings)
    metric_service = _CountingMetricService(MetadataRepository(engine), settings=settings)
    return repository, metric_service


def _trace_nodes(settings: Settings, run_id: str) -> list[str]:
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT node FROM trace_step WHERE run_id = :run_id ORDER BY seq"),
                {"run_id": run_id},
            ).mappings().all()
        return [row["node"] for row in rows]
    finally:
        engine.dispose()


def _trace_seqs(settings: Settings, run_id: str) -> list[int]:
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT seq FROM trace_step WHERE run_id = :run_id ORDER BY seq"),
                {"run_id": run_id},
            ).mappings().all()
        return [int(row["seq"]) for row in rows]
    finally:
        engine.dispose()


def _observation_payload(result: dict[str, Any], action_name: str) -> dict[str, Any]:
    for observation in result["observations"]:
        if observation.action_name == action_name:
            return observation.payload
    raise AssertionError(f"observation not found: {action_name}")


def _action_args(result: dict[str, Any], action_name: str) -> dict[str, Any]:
    for action in result["actions"]:
        if action.action == action_name:
            return action.args
    raise AssertionError(f"action not found: {action_name}")


def _audit_hashes(settings: Settings, run_id: str) -> set[str]:
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT sql_hash FROM sql_audit WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).mappings().all()
        return {str(row["sql_hash"]) for row in rows}
    finally:
        engine.dispose()


def _count(settings: Settings, table: str, column: str, value: str) -> int:
    allowed = {
        "operation_task": {"run_id"},
        "sql_audit": {"run_id"},
    }
    assert table in allowed and column in allowed[table]
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = :value"),
                {"value": value},
            ).one()
        return int(row.n)
    finally:
        engine.dispose()


def _agent_run(settings: Settings, run_id: str) -> dict[str, Any]:
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, error_code, finished_at FROM agent_run WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).mappings().one()
        return dict(row)
    finally:
        engine.dispose()


def _set_metric_dimensions(settings: Settings, metric_id: str, dimensions: list[str]) -> None:
    engine = create_engine(str(settings.db_dsn), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE metric_definition
                    SET allowed_dimensions = :allowed_dimensions
                    WHERE metric_id = :metric_id
                    """
                ),
                {"metric_id": metric_id, "allowed_dimensions": json.dumps(dimensions)},
            )
    finally:
        engine.dispose()


def _evidence(evidence_id: str) -> Evidence:
    from metric_rca.guardrails.query_spec import build_query_spec

    return Evidence(
        evidence_id=evidence_id,
        query_spec=build_query_spec(
            metric_id="gmv",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 5),
            purpose="current",
        ),
        sql="SELECT 1",
        sql_hash="0" * 64,
        guard_status="passed",
        result_summary={},
        data_source="fact_order",
        created_at=datetime(2026, 6, 5),
    )


class _CountingMetricService(MetricService):
    def __init__(self, metadata_repo: MetadataRepository, settings: Settings) -> None:
        super().__init__(metadata_repo, settings=settings)
        self.parse_calls = 0

    def parse_question(self, question: str, *, business_today: date):
        self.parse_calls += 1
        return super().parse_question(question, business_today=business_today)


class _MetricService:
    def __init__(self, dimensions: list[str]) -> None:
        self.dimensions = dimensions

    def get_metric_definition(self, metric_id: str):
        return type(
            "Definition",
            (),
            {
                "metric_id": metric_id,
                "higher_is_better": True,
                "allowed_dimensions": self.dimensions,
                "source_table": "fact_order",
            },
        )()


class _Dependencies:
    def __init__(self, settings: Any | None = None, repository: Any | None = None) -> None:
        self.settings = settings or Settings.model_construct(
            max_steps=8,
            max_query=12,
            max_drilldown_depth=2,
            max_repair=1,
            memory_enabled=False,
            llm_enabled=False,
            llm_required=False,
            signal_metric_by_type={"campaign": "gmv"},
        )
        self.metric_service = _MetricService(["channel"])
        self.repository = repository or _GraphRepository()
        self.renderer = None
        self.trace_writer = _InMemoryTraceWriter()
        self.memory_repo = None


class _GraphRepository:
    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.persisted_evidence = {
            f"run-1:E{idx}": {
                "evidence_id": f"run-1:E{idx}",
                "run_id": "run-1",
                "guard_status": "passed",
                "sql_hash": "0" * 64,
            }
            for idx in range(1, 5)
        }

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, Any] | None:
        row = self.persisted_evidence.get(evidence_id)
        if row and row["run_id"] == run_id:
            return row
        return None

    def create_operation_task(self, row: dict[str, Any]) -> None:
        self.tasks.append(row)


class _InMemoryTraceWriter:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []

    def start_run(self, **kwargs) -> None:
        self.started.append(kwargs)

    def set_run_context(self, **kwargs) -> None:
        return None

    def write_step(self, **kwargs) -> None:
        self.steps.append(kwargs)

    def finish_run(self, **kwargs) -> None:
        self.finished.append(kwargs)


class _CountingBudgetRepository:
    def __init__(self) -> None:
        self.executed = 0
        self.persisted_evidence = {
            "run-1:E1": {"evidence_id": "run-1:E1", "run_id": "run-1", "guard_status": "passed"},
            "run-1:E2": {"evidence_id": "run-1:E2", "run_id": "run-1", "guard_status": "passed"},
            "run-1:E3": {"evidence_id": "run-1:E3", "run_id": "run-1", "guard_status": "passed"},
        }

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        return {
            "run_id": run_id,
            "status": "running",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
        }

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, Any] | None:
        row = self.persisted_evidence.get(evidence_id)
        if row and row["run_id"] == run_id:
            return row
        return None

    def execute_plan(self, plan, *, run_id: str):
        self.executed += 1
        if "GROUP BY fact_order.channel" in plan.sql and "business_date IN" in plan.sql:
            rows = [
                {"business_date": date(2026, 5, 29), "channel": "paid_ads", "metric_value": 100.0},
                {"business_date": date(2026, 5, 22), "channel": "paid_ads", "metric_value": 100.0},
                {"business_date": date(2026, 5, 15), "channel": "paid_ads", "metric_value": 100.0},
                {"business_date": date(2026, 5, 8), "channel": "paid_ads", "metric_value": 100.0},
            ]
        elif "GROUP BY fact_order.channel" in plan.sql:
            rows = [{"channel": "paid_ads", "metric_value": 20.0}]
        elif "business_date IN" in plan.sql:
            rows = [
                {"business_date": date(2026, 5, 29), "metric_value": 100.0},
                {"business_date": date(2026, 5, 22), "metric_value": 100.0},
                {"business_date": date(2026, 5, 15), "metric_value": 100.0},
                {"business_date": date(2026, 5, 8), "metric_value": 100.0},
            ]
        else:
            rows = [{"metric_value": 20.0}]
        return type("QueryResult", (), {"rows": rows, "row_count": len(rows), "latency_ms": 1})()

    def create_evidence(self, row: dict[str, Any]) -> None:
        self.persisted_evidence[row["evidence_id"]] = {
            "evidence_id": row["evidence_id"],
            "run_id": row["run_id"],
            "guard_status": row["guard_status"],
            "sql_hash": row["sql_hash"],
        }

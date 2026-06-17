from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from metric_rca.agent.factory import AgentFactoryError, create_metric_rca_agent
from metric_rca.domain.models import Observation, StrictModel
from metric_rca.runtime.action_gate import ActionGate
from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_models import RcaAction
from metric_rca.runtime.run_context import RunContext
from metric_rca.runtime.sdk_tools import MetricRCAToolHandler, ToolExecutor


def test_missing_llm_config_fails_fast_without_runtime_agent() -> None:
    with _raises_code(AgentFactoryError, "LLM_REQUIRED_UNAVAILABLE"):
        create_metric_rca_agent(dependencies=_deps(llm_api_key=None), run_id="run-1")


def test_illegal_tool_args_return_typed_schema_error_without_handler_execution() -> None:
    called = False

    class Args(StrictModel):
        run_id: str
        metric_id: str
        target_date: date
        dimension: str

    def handler(args: Args, dependencies: object):
        nonlocal called
        called = True
        return Observation(action_name="drilldown_dimension", ok=True)

    executor = ToolExecutor(
        dependencies=object(),
        handlers={"drilldown_dimension": MetricRCAToolHandler(args_model=Args, call=handler)},
    )
    result = executor.execute(
        RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5)),
        RcaAction(
            action_id="A2",
            kind="drilldown_dimension",
            args={"metric_id": "gmv", "target_date": date(2026, 6, 5)},
        ),
        EvidenceGraph(run_id="run-1"),
    )

    assert called is False
    assert result.observation.ok is False
    assert result.observation.error_code == "ACTION_SCHEMA_INVALID"


def test_budget_exhaustion_is_typed_and_does_not_mutate_context() -> None:
    ctx = RunContext(
        run_id="run-1",
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        budget={"max_steps": 0, "max_query": 12, "max_drilldown_depth": 3},
    )
    action = RcaAction(
        action_id="A1",
        kind="detect_anomaly",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "filters": {}},
    )

    decision = ActionGate().validate(ctx, action, EvidenceGraph(run_id="run-1"))

    assert decision.allowed is False
    assert decision.error_code == "BUDGET_EXCEEDED"
    assert ctx.step_count == 0


def test_no_anomaly_contract_blocks_downstream_actions() -> None:
    ctx = RunContext(
        run_id="run-1",
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        repository=_Repository(
            {
                "run-1:E1": {
                    "evidence_id": "run-1:E1",
                    "guard_status": "passed",
                    "result_summary": {"is_anomaly": False},
                }
            }
        ),
    )
    action = RcaAction(
        action_id="A2",
        kind="rank_root_causes",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5)},
        requires=["E1"],
    )

    decision = ActionGate().validate(ctx, action, EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"]))

    assert decision.allowed is False
    assert decision.error_code == "NO_ANOMALY_CONTRACT_VIOLATED"


class _raises_code:
    def __init__(self, exc_type: type[BaseException], code: str) -> None:
        self.exc_type = exc_type
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is self.exc_type
        assert getattr(exc, "code", None) == self.code
        return True


class _Repository:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self._rows = rows

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, object] | None:
        row = self._rows.get(evidence_id)
        if row is None or not str(row.get("evidence_id", "")).startswith(f"{run_id}:"):
            return None
        return row


def _deps(*, llm_api_key: str | None = "key") -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(
            llm_provider="openai",
            llm_model="gpt-test",
            llm_api_key=llm_api_key,
            business_today=date(2026, 6, 6),
            target_date=date(2026, 6, 5),
        ),
        repository=SimpleNamespace(),
        metric_service=SimpleNamespace(),
        renderer=SimpleNamespace(),
        trace_writer=SimpleNamespace(),
    )

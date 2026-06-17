from __future__ import annotations

from datetime import date

from metric_rca.runtime.action_gate import ActionGate
from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_models import RcaAction
from metric_rca.runtime.run_context import RunContext


def test_action_gate_allows_matching_detect_action() -> None:
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A1",
        kind="detect_anomaly",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "filters": {}},
    )

    decision = ActionGate().validate(ctx, action, EvidenceGraph(run_id="run-1"))

    assert decision.allowed is True
    assert decision.error_code is None


def test_action_gate_rejects_metric_scope_switch() -> None:
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A2",
        kind="drilldown_dimension",
        args={"metric_id": "uv", "target_date": date(2026, 6, 5), "dimension": "channel"},
        requires=["E1"],
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"])

    decision = ActionGate().validate(ctx, action, graph)

    assert decision.allowed is False
    assert decision.error_code == "METRIC_SCOPE_VIOLATION"


def test_action_gate_rejects_missing_required_evidence_alias() -> None:
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A3",
        kind="fetch_related_signal",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "dimension": "channel", "element": "paid_ads"},
        requires=["E1", "E2_channel"],
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"])

    decision = ActionGate().validate(ctx, action, graph)

    assert decision.allowed is False
    assert decision.error_code == "EVIDENCE_MISSING"
    assert "E2_channel" in (decision.message or "")


def test_action_gate_rejects_downstream_action_after_no_anomaly() -> None:
    repository = _Repository(
        {
            "run-1:E1": {
                "evidence_id": "run-1:E1",
                "guard_status": "passed",
                "result_summary": {"is_anomaly": False},
            }
        }
    )
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5), repository=repository)
    action = RcaAction(
        action_id="A2",
        kind="drilldown_dimension",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "dimension": "channel"},
        requires=["E1"],
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"])

    decision = ActionGate().validate(ctx, action, graph)

    assert decision.allowed is False
    assert decision.error_code == "NO_ANOMALY_CONTRACT_VIOLATED"


def test_action_gate_rejects_explicit_scope_dimension_switch() -> None:
    ctx = RunContext(
        run_id="run-1",
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        explicit_scope={"category": "electronics"},
    )
    action = RcaAction(
        action_id="A2",
        kind="drilldown_dimension",
        args={
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "dimension": "channel",
            "filters": {"category": "electronics"},
        },
        requires=["E1"],
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"])

    decision = ActionGate().validate(ctx, action, graph)

    assert decision.allowed is False
    assert decision.error_code == "ACTION_SCHEMA_INVALID"
    assert "dimension=category" in (decision.message or "")


def test_action_gate_rejects_step_budget_exhaustion() -> None:
    ctx = RunContext(
        run_id="run-1",
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        budget={"max_steps": 1, "max_query": 12, "max_drilldown_depth": 3},
        step_count=1,
    )
    action = RcaAction(
        action_id="A2",
        kind="drilldown_dimension",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "dimension": "channel"},
        requires=["E1"],
    )
    graph = EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"])

    decision = ActionGate().validate(ctx, action, graph)

    assert decision.allowed is False
    assert decision.error_code == "BUDGET_EXCEEDED"


class _Repository:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self._rows = rows

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, object] | None:
        row = self._rows.get(evidence_id)
        if row is None or not str(row.get("evidence_id", "")).startswith(f"{run_id}:"):
            return None
        return row

from __future__ import annotations

from datetime import date

from metric_rca.runtime.action_gate import ActionGate
from metric_rca.runtime.evidence_graph import EvidenceGraph
from metric_rca.runtime.plan_models import RcaAction
from metric_rca.runtime.run_context import RunContext


def test_action_gate_metric_scope_violation_short_circuits() -> None:
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A2",
        kind="drilldown_dimension",
        args={"metric_id": "uv", "target_date": date(2026, 6, 5), "dimension": "channel"},
        requires=["E1"],
    )

    decision = ActionGate().validate(ctx, action, EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"]))

    assert decision.allowed is False
    assert decision.error_code == "METRIC_SCOPE_VIOLATION"
    assert ctx.step_count == 0


def test_action_gate_target_date_violation_short_circuits() -> None:
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A2",
        kind="drilldown_dimension",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 4), "dimension": "channel"},
        requires=["E1"],
    )

    decision = ActionGate().validate(ctx, action, EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"]))

    assert decision.allowed is False
    assert decision.error_code == "METRIC_SCOPE_VIOLATION"
    assert ctx.step_count == 0


def test_action_gate_evidence_chain_requires_current_run_aliases() -> None:
    ctx = RunContext(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    action = RcaAction(
        action_id="A3",
        kind="fetch_related_signal",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "dimension": "channel", "element": "paid_ads"},
        requires=["E1", "E2_channel"],
    )

    decision = ActionGate().validate(ctx, action, EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"]))

    assert decision.allowed is False
    assert decision.error_code == "EVIDENCE_MISSING"


def test_action_gate_blocks_no_anomaly_downstream_actions() -> None:
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
        kind="drilldown_dimension",
        args={"metric_id": "gmv", "target_date": date(2026, 6, 5), "dimension": "channel"},
        requires=["E1"],
    )

    decision = ActionGate().validate(ctx, action, EvidenceGraph(run_id="run-1", evidence_ids=["run-1:E1"]))

    assert decision.allowed is False
    assert decision.error_code == "NO_ANOMALY_CONTRACT_VIOLATED"


class _Repository:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self._rows = rows

    def get_evidence(self, *, run_id: str, evidence_id: str) -> dict[str, object] | None:
        row = self._rows.get(evidence_id)
        if row is None or not str(row.get("evidence_id", "")).startswith(f"{run_id}:"):
            return None
        return row

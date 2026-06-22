from __future__ import annotations

from datetime import date

import pytest

from metric_rca.runtime.plan_models import RcaAction
from metric_rca.runtime.plan_validator import PlanValidationError, validate_plan_actions


def test_plan_validator_accepts_topological_plan() -> None:
    actions = [
        RcaAction(
            action_id="A1",
            kind="detect_anomaly",
            args={"metric_id": "gmv", "target_date": date(2026, 6, 20)},
            produces=["E1"],
        ),
        RcaAction(
            action_id="A2",
            kind="drilldown_dimension",
            args={"dimension": "channel"},
            requires=["E1"],
            produces=["E2_channel"],
        ),
    ]

    validate_plan_actions(run_id="run-1", actions=actions)


def test_plan_validator_rejects_forward_dependency() -> None:
    actions = [
        RcaAction(
            action_id="A1",
            kind="drilldown_dimension",
            args={"dimension": "channel"},
            requires=["E1"],
            produces=["E2_channel"],
        )
    ]

    with pytest.raises(PlanValidationError) as excinfo:
        validate_plan_actions(run_id="run-1", actions=actions)

    assert excinfo.value.code == "PLAN_DEPENDENCY_INVALID"


def test_plan_validator_rejects_alias_contract_drift() -> None:
    actions = [
        RcaAction(
            action_id="A1",
            kind="detect_anomaly",
            produces=["E1"],
        ),
        RcaAction(
            action_id="A2",
            kind="select_signal_element",
            args={"evidence_alias": "E_select_channel_int"},
            requires=["E1"],
            produces=["E_select_channel_conv"],
        ),
    ]

    with pytest.raises(PlanValidationError) as excinfo:
        validate_plan_actions(run_id="run-1", actions=actions)

    assert excinfo.value.code == "PLAN_ACTION_CONTRACT_INVALID"


def test_plan_validator_rejects_duplicate_producer() -> None:
    actions = [
        RcaAction(action_id="A1", kind="detect_anomaly", produces=["E1"]),
        RcaAction(action_id="A2", kind="detect_anomaly", produces=["E1"]),
    ]

    with pytest.raises(PlanValidationError) as excinfo:
        validate_plan_actions(run_id="run-1", actions=actions)

    assert excinfo.value.code == "PLAN_ALIAS_CONFLICT"

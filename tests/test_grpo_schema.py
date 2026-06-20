from __future__ import annotations

import pytest

from metric_rca.evals.grpo_schema import (
    GrpoSchemaError,
    RewardComponent,
    RewardRecord,
    TrajectoryLayer,
    TrajectoryRecord,
    validate_record_dict,
)


def test_controller_record_round_trips_strict_schema() -> None:
    record = TrajectoryRecord(
        trajectory_id="grpo-01-example",
        layer=TrajectoryLayer.CONTROLLER,
        cycle_id="cycle-20260620-1200",
        round=1,
        source={"eval_code_commit": "a" * 40},
        input={"optimization_context": {}},
        trajectory={"controller_rules": {}},
        output={"decision": {"selected_fix_category": "FIX-D"}},
        reward=RewardRecord(
            total=1.0,
            components=(RewardComponent(name="rules", value=1.0),),
            eligible_for_positive=True,
        ),
    )
    payload = record.as_dict()
    validate_record_dict(payload)
    assert payload["layer"] == "layer1_controller"


def test_coding_record_requires_fix_assessment_flags() -> None:
    record = TrajectoryRecord(
        trajectory_id="grpo-02-example",
        layer=TrajectoryLayer.CODING_FIX,
        cycle_id="cycle-20260620-1200",
        round=2,
        source={},
        input={"diagnosis": [], "before": {}},
        trajectory={"git_diff": "diff --git", "changed_files": ["x.py"]},
        output={"after": {}, "fix_assessment": {}},
        reward=RewardRecord(
            total=0.5,
            components=(RewardComponent(name="effective", value=1.0),),
            eligible_for_positive=True,
        ),
    )
    with pytest.raises(GrpoSchemaError) as exc_info:
        record.validate()
    assert exc_info.value.code == "GRPO_REWARD_INVALID"


def test_validate_record_dict_rejects_string_booleans_and_extra_reward_keys() -> None:
    record = TrajectoryRecord(
        trajectory_id="grpo-01-example",
        layer=TrajectoryLayer.CONTROLLER,
        cycle_id="cycle-20260620-1200",
        round=1,
        source={"eval_code_commit": "a" * 40},
        input={"optimization_context": {}},
        trajectory={"controller_rules": {}},
        output={"decision": {"selected_fix_category": "FIX-D"}},
        reward=RewardRecord(
            total=1.0,
            components=(RewardComponent(name="rules", value=1.0),),
            eligible_for_positive=True,
        ),
    )
    payload = record.as_dict()
    payload["round"] = "1"
    payload["reward"]["eligible_for_positive"] = "false"
    payload["reward"]["extra"] = "ignored by old implementation"

    with pytest.raises(GrpoSchemaError) as exc_info:
        validate_record_dict(payload)

    assert exc_info.value.code == "GRPO_TRAJECTORY_INVALID"


def test_validate_record_dict_rejects_non_mapping_reward_components() -> None:
    record = TrajectoryRecord(
        trajectory_id="grpo-01-example",
        layer=TrajectoryLayer.CONTROLLER,
        cycle_id="cycle-20260620-1200",
        round=1,
        source={"eval_code_commit": "a" * 40},
        input={"optimization_context": {}},
        trajectory={"controller_rules": {}},
        output={"decision": {"selected_fix_category": "FIX-D"}},
        reward=RewardRecord(
            total=1.0,
            components=(RewardComponent(name="rules", value=1.0),),
            eligible_for_positive=True,
        ),
    )
    payload = record.as_dict()
    payload["reward"]["components"] = list(payload["reward"]["components"]) + ["not-a-component"]

    with pytest.raises(GrpoSchemaError) as exc_info:
        validate_record_dict(payload)

    assert exc_info.value.code == "GRPO_REWARD_INVALID"

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metric_rca.evals.grpo_manifest import GrpoManifestError, build_manifest, validate_manifest
from metric_rca.evals.grpo_schema import RewardComponent, RewardRecord, TrajectoryLayer, TrajectoryRecord
from metric_rca.evals.ptv_artifacts import write_jsonl_atomic


def test_validate_manifest_rejects_string_counts_and_extra_keys(tmp_path: Path) -> None:
    record = _record().as_dict()
    data_path = tmp_path / "layer1_controller.jsonl"
    write_jsonl_atomic(data_path, [record])
    manifest = build_manifest(
        cycle_id="cycle-20260620-1200",
        records_by_layer={TrajectoryLayer.CONTROLLER.value: [record]},
        output_files={"layer1_controller": data_path},
        redaction_count=0,
    )
    manifest["record_count"] = "1"
    manifest["extra"] = "not allowed"

    with pytest.raises(GrpoManifestError) as exc_info:
        validate_manifest(manifest, [record])

    assert exc_info.value.code == "GRPO_MANIFEST_INVALID"


def test_validate_manifest_verifies_file_hashes(tmp_path: Path) -> None:
    record = _record().as_dict()
    data_path = tmp_path / "layer1_controller.jsonl"
    write_jsonl_atomic(data_path, [record])
    manifest = build_manifest(
        cycle_id="cycle-20260620-1200",
        records_by_layer={TrajectoryLayer.CONTROLLER.value: [record]},
        output_files={"layer1_controller": data_path},
        redaction_count=0,
    )
    data_path.write_text(json.dumps({"tampered": True}) + "\n", encoding="utf-8")

    with pytest.raises(GrpoManifestError) as exc_info:
        validate_manifest(manifest, [record])

    assert exc_info.value.code == "GRPO_MANIFEST_FILE_INVALID"


def test_validate_manifest_accepts_declared_zero_count_layers(tmp_path: Path) -> None:
    record = _record().as_dict()
    data_path = tmp_path / "layer1_controller.jsonl"
    layer2_path = tmp_path / "layer2_sub_agent.jsonl"
    write_jsonl_atomic(data_path, [record])
    write_jsonl_atomic(layer2_path, [])
    manifest = build_manifest(
        cycle_id="cycle-20260620-1200",
        records_by_layer={
            TrajectoryLayer.CONTROLLER.value: [record],
            TrajectoryLayer.SUB_AGENT.value: [],
        },
        output_files={"layer1_controller": data_path, "layer2_sub_agent": layer2_path},
        redaction_count=0,
    )

    validate_manifest(manifest, [record])


def test_validate_manifest_rejects_unknown_zero_count_layers(tmp_path: Path) -> None:
    record = _record().as_dict()
    data_path = tmp_path / "layer1_controller.jsonl"
    write_jsonl_atomic(data_path, [record])
    manifest = build_manifest(
        cycle_id="cycle-20260620-1200",
        records_by_layer={TrajectoryLayer.CONTROLLER.value: [record]},
        output_files={"layer1_controller": data_path},
        redaction_count=0,
    )
    manifest["layer_counts"]["bogus_layer"] = 0

    with pytest.raises(GrpoManifestError) as exc_info:
        validate_manifest(manifest, [record])

    assert exc_info.value.code == "GRPO_MANIFEST_INVALID"


def _record() -> TrajectoryRecord:
    return TrajectoryRecord(
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

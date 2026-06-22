"""Manifest generation and validation for three-layer GRPO datasets."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from metric_rca.evals.grpo_schema import SCHEMA_VERSION, TrajectoryLayer, validate_record_dict


MANIFEST_VERSION = "metricrca-grpo-manifest-v2"
_MANIFEST_KEYS = {
    "schema_version",
    "record_schema_version",
    "cycle_id",
    "record_count",
    "positive_record_count",
    "overfit_positive_count",
    "redaction_count",
    "layer_counts",
    "reward_histogram",
    "files",
}
_FILE_KEYS = {"path", "bytes", "sha256"}


class GrpoManifestError(ValueError):
    def __init__(self, code: str, message: str, *, context: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.context = dict(context or {})
        super().__init__(f"{code}: {message}")


def build_manifest(
    *,
    cycle_id: str,
    records_by_layer: Mapping[str, list[dict[str, Any]]],
    output_files: Mapping[str, Path],
    redaction_count: int,
) -> dict[str, Any]:
    all_records = [record for records in records_by_layer.values() for record in records]
    for record in all_records:
        validate_record_dict(record)
    reward_histogram = Counter(_reward_bucket(float(record["reward"]["total"])) for record in all_records)
    positive_count = sum(
        1
        for record in all_records
        if bool(record["reward"]["eligible_for_positive"]) and float(record["reward"]["total"]) > 0.0
    )
    overfit_positive_count = sum(
        1
        for record in all_records
        if record.get("metadata", {}).get("prediction_divergence") == "overfit"
        and bool(record["reward"]["eligible_for_positive"])
    )
    if overfit_positive_count:
        raise GrpoManifestError("GRPO_OVERFIT_POSITIVE_FORBIDDEN", "overfit predictions cannot be positive records")
    return {
        "schema_version": MANIFEST_VERSION,
        "record_schema_version": SCHEMA_VERSION,
        "cycle_id": cycle_id,
        "record_count": len(all_records),
        "positive_record_count": positive_count,
        "overfit_positive_count": overfit_positive_count,
        "redaction_count": redaction_count,
        "layer_counts": {
            layer.value: len(records_by_layer.get(layer.value, []))
            for layer in TrajectoryLayer
        },
        "reward_histogram": dict(sorted(reward_histogram.items())),
        "files": {
            name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": _file_hash(path),
            }
            for name, path in sorted(output_files.items())
        },
    }


def validate_manifest(manifest: Mapping[str, Any], records: Iterable[Mapping[str, Any]]) -> None:
    rows = list(records)
    missing = sorted(_MANIFEST_KEYS - set(manifest))
    extra = sorted(set(manifest) - _MANIFEST_KEYS)
    if missing or extra:
        raise GrpoManifestError(
            "GRPO_MANIFEST_INVALID",
            "manifest keys do not match the strict schema",
            context={"missing": missing, "extra": extra},
        )
    if manifest.get("schema_version") != MANIFEST_VERSION or manifest.get("record_schema_version") != SCHEMA_VERSION:
        raise GrpoManifestError("GRPO_MANIFEST_INVALID", "manifest schema version is invalid")
    for row in rows:
        validate_record_dict(row)
    expected_layer_counts = Counter(str(row["layer"]) for row in rows)
    expected_reward_histogram = Counter(_reward_bucket(float(row["reward"]["total"])) for row in rows)
    expected_positive_count = sum(
        1
        for row in rows
        if row["reward"]["eligible_for_positive"] is True and float(row["reward"]["total"]) > 0.0
    )
    _require_int(manifest, "record_count")
    _require_int(manifest, "positive_record_count")
    _require_int(manifest, "overfit_positive_count")
    _require_int(manifest, "redaction_count")
    if manifest["record_count"] != len(rows):
        raise GrpoManifestError("GRPO_MANIFEST_COUNT_MISMATCH", "manifest record_count does not match records")
    if manifest["positive_record_count"] != expected_positive_count:
        raise GrpoManifestError("GRPO_MANIFEST_COUNT_MISMATCH", "manifest positive_record_count does not match records")
    layer_counts = manifest.get("layer_counts")
    allowed_layers = {layer.value for layer in TrajectoryLayer}
    if not isinstance(layer_counts, Mapping) or set(layer_counts) != allowed_layers or any(
        not isinstance(layer, str) or not isinstance(count, int) or isinstance(count, bool) or count < 0
        for layer, count in layer_counts.items()
    ):
        raise GrpoManifestError(
            "GRPO_MANIFEST_INVALID",
            "manifest layer_counts must exactly cover known trajectory layers",
            context={"expected_layers": sorted(allowed_layers), "actual_layers": sorted(layer_counts) if isinstance(layer_counts, Mapping) else None},
        )
    expected_layer_payload = {str(layer): expected_layer_counts.get(str(layer), 0) for layer in layer_counts}
    if any(layer not in expected_layer_payload for layer in expected_layer_counts) or layer_counts != expected_layer_payload:
        raise GrpoManifestError("GRPO_MANIFEST_COUNT_MISMATCH", "manifest layer_counts do not match records")
    if manifest.get("reward_histogram") != dict(sorted(expected_reward_histogram.items())):
        raise GrpoManifestError("GRPO_MANIFEST_COUNT_MISMATCH", "manifest reward_histogram does not match records")
    if any(
        row.get("metadata", {}).get("prediction_divergence") == "overfit"
        and row.get("reward", {}).get("eligible_for_positive") is True
        for row in rows
    ):
        raise GrpoManifestError("GRPO_OVERFIT_POSITIVE_FORBIDDEN", "overfit predictions cannot be positive records")
    if manifest["overfit_positive_count"] != 0:
        raise GrpoManifestError("GRPO_OVERFIT_POSITIVE_FORBIDDEN", "manifest overfit_positive_count must be zero")
    _validate_files(manifest["files"])


def _require_int(manifest: Mapping[str, Any], field: str) -> None:
    value = manifest.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GrpoManifestError(
            "GRPO_MANIFEST_INVALID",
            "manifest count fields must be non-negative integers",
            context={"field": field, "value": value},
        )


def _validate_files(files: Any) -> None:
    if not isinstance(files, Mapping) or not files:
        raise GrpoManifestError("GRPO_MANIFEST_INVALID", "manifest files must be a non-empty object")
    for name, payload in files.items():
        if not isinstance(name, str) or not isinstance(payload, Mapping):
            raise GrpoManifestError("GRPO_MANIFEST_FILE_INVALID", "manifest file entries must be objects")
        missing = sorted(_FILE_KEYS - set(payload))
        extra = sorted(set(payload) - _FILE_KEYS)
        if missing or extra:
            raise GrpoManifestError(
                "GRPO_MANIFEST_FILE_INVALID",
                "manifest file entry keys do not match the strict schema",
                context={"name": name, "missing": missing, "extra": extra},
            )
        path = payload.get("path")
        expected_bytes = payload.get("bytes")
        expected_hash = payload.get("sha256")
        if not isinstance(path, str) or not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool) or not isinstance(expected_hash, str):
            raise GrpoManifestError(
                "GRPO_MANIFEST_FILE_INVALID",
                "manifest file path/bytes/hash fields are malformed",
                context={"name": name},
            )
        file_path = Path(path)
        if not file_path.exists():
            raise GrpoManifestError(
                "GRPO_MANIFEST_FILE_INVALID",
                "manifest file path does not exist",
                context={"name": name, "path": path},
            )
        actual_bytes = file_path.stat().st_size
        actual_hash = _file_hash(file_path)
        if actual_bytes != expected_bytes or actual_hash != expected_hash:
            raise GrpoManifestError(
                "GRPO_MANIFEST_FILE_INVALID",
                "manifest file hash or byte count does not match",
                context={"name": name, "path": path},
            )


def _reward_bucket(value: float) -> str:
    if value > 0.0:
        return "positive"
    if value < 0.0:
        return "negative"
    return "zero"


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

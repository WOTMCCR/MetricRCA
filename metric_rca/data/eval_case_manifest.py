"""Generate answer-free public eval cases and separate private ground truth."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Iterable, Mapping

from metric_rca.data.scenario_spec import ScenarioSpec


_FORBIDDEN_PUBLIC_KEYS = {
    "expected_anomaly",
    "anomaly_direction",
    "root_cause_type",
    "root_causes",
    "dimension",
    "element",
    "weight",
    "expected_evidence_chain",
    "negative_controls",
    "baseline_comparison",
    "shock_manifest",
}
_FORBIDDEN_PUBLIC_TERMS = (
    "budget",
    "stockout",
    "inventory",
    "price_increase",
    "price increase",
    "promotion_end",
    "promotion end",
    "delivery",
    "simultaneous",
    "multi_cause",
    "residual",
    "aov",
    "campaign_budget_cut",
)


def public_case_id(index: int) -> str:
    if index < 1:
        raise ValueError("PUBLIC_CASE_INDEX_INVALID")
    return f"GC{index:03d}"


def build_public_cases(scenarios: Iterable[ScenarioSpec]) -> list[dict[str, Any]]:
    rows = [
        {
            "case_id": public_case_id(index),
            "question": f"Analyze {scenario.metric_id} on {scenario.target_date.isoformat()} for generated case {public_case_id(index)}.",
            "tags": ["generated", "scenario"],
        }
        for index, scenario in enumerate(scenarios, start=1)
    ]
    validate_public_cases(rows)
    return rows


def build_case_manifest(
    *,
    scenario_set_id: str,
    public_cases: list[dict[str, Any]],
    private_ground_truth: list[dict[str, Any]],
) -> dict[str, Any]:
    validate_public_cases(public_cases)
    public_ids = [str(row.get("case_id")) for row in public_cases]
    private_ids = [str(row.get("case_id")) for row in private_ground_truth]
    if len(public_ids) != len(set(public_ids)) or len(private_ids) != len(set(private_ids)):
        raise ValueError("EVAL_CASE_ID_DUPLICATE")
    if set(public_ids) != set(private_ids):
        raise ValueError("EVAL_CASE_PRIVATE_PUBLIC_MISMATCH")
    return {
        "schema_version": "metricrca-generated-eval-manifest-v1",
        "scenario_set_id": scenario_set_id,
        "case_count": len(public_cases),
        "public_case_ids": sorted(public_ids),
        "private_case_ids": sorted(private_ids),
        "public_cases_sha256": _stable_hash(public_cases),
        "private_ground_truth_sha256": _stable_hash(private_ground_truth),
        "ground_truth_separated": True,
    }


def validate_public_cases(rows: Iterable[Mapping[str, Any]]) -> None:
    for index, row in enumerate(rows):
        keys = set(row)
        forbidden = sorted(keys & _FORBIDDEN_PUBLIC_KEYS)
        if forbidden:
            raise ValueError(f"PUBLIC_CASE_GROUND_TRUTH_LEAK: index={index} keys={forbidden}")
        if keys != {"case_id", "question", "tags"}:
            raise ValueError(f"PUBLIC_CASE_SCHEMA_INVALID: index={index} keys={sorted(keys)}")
        if not isinstance(row.get("case_id"), str) or not isinstance(row.get("question"), str):
            raise ValueError(f"PUBLIC_CASE_SCHEMA_INVALID: index={index}")
        tags = row.get("tags")
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError(f"PUBLIC_CASE_SCHEMA_INVALID: index={index} tags")
        serialized = json.dumps(dict(row), ensure_ascii=False, sort_keys=True).lower()
        leaked = [term for term in _FORBIDDEN_PUBLIC_TERMS if term in serialized]
        if leaked:
            raise ValueError(f"PUBLIC_CASE_GROUND_TRUTH_LEAK: index={index} terms={leaked}")


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()

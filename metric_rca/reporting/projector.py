"""Deterministic report projection from persisted RCA artifacts.

The projector is intentionally pure: it reads no fact tables, calls no graph,
and does not consult ground truth. It turns already-persisted artifacts into the
same safe report shape used by the P3B graph boundary.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


IDENTITY_FIELDS = ("root_cause_type", "dimension", "element", "verdict")
NUMERIC_CLAIM_FIELDS = ("contribution_pct", "explanatory_power", "surprise_js")


def build_report_from_persisted_artifacts(
    *,
    agent_run: dict[str, Any],
    evidences: list[dict[str, Any]],
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build the external report from persisted system-table artifacts only."""
    status = str(agent_run.get("status") or "")
    run_id = str(agent_run.get("run_id") or "")
    if not run_id:
        return None
    if status == "failed":
        return None
    if status == "no_anomaly":
        return _no_anomaly_report(agent_run=agent_run, evidences=evidences, tasks=tasks or [])
    if status != "succeeded":
        return None

    by_alias = evidence_by_alias(evidences, run_id=run_id)
    e4 = by_alias.get("E4")
    if e4 is None or e4.get("guard_status") != "passed":
        return None
    summary = e4.get("result_summary") or {}
    if not isinstance(summary, dict):
        return None

    top_candidate = project_candidate_from_e4(summary)
    numeric_claims = numeric_claims_from_e4(summary, str(e4.get("evidence_id") or ""))
    selected = _selected_candidate(summary)
    evidence_ids = _evidence_ids(selected)
    if (
        top_candidate is None
        or not numeric_claims
        or not _candidate_evidence_ids_are_current_run_passed(
            evidence_ids=evidence_ids,
            evidences=evidences,
            run_id=run_id,
        )
    ):
        return None

    return {
        "status": "succeeded",
        "metric_id": agent_run.get("metric_id"),
        "target_date": _date_string(agent_run.get("target_date")),
        "top_candidate": top_candidate,
        "evidence_ids": evidence_ids,
        "numeric_claims": numeric_claims,
    }


def project_candidate_from_e4(e4_result_summary: dict[str, Any]) -> dict[str, Any] | None:
    selected = _selected_candidate(e4_result_summary)
    if selected is None:
        return None
    projected = {field: selected.get(field) for field in IDENTITY_FIELDS}
    if any(projected[field] in (None, "") for field in IDENTITY_FIELDS):
        return None
    return projected


def project_candidates_from_e4(e4_result_summary: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = e4_result_summary.get("candidates")
    if not isinstance(candidates, list):
        return []

    projected: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            return []
        candidate = {field: item.get(field) for field in IDENTITY_FIELDS}
        if any(candidate[field] in (None, "") for field in IDENTITY_FIELDS):
            return []
        projected.append(candidate)
    return projected


def numeric_claims_from_e4(e4_result_summary: dict[str, Any], e4_id: str) -> list[dict[str, Any]]:
    selected = _selected_candidate(e4_result_summary)
    if selected is None or not e4_id:
        return []
    claims: list[dict[str, Any]] = []
    for field in NUMERIC_CLAIM_FIELDS:
        value = selected.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            return []
        claims.append({"name": field, "value": float(value), "evidence_id": e4_id})
    return claims


def evidence_by_alias(evidences: list[dict[str, Any]], run_id: str) -> dict[str, dict[str, Any]]:
    prefix = f"{run_id}:"
    by_alias: dict[str, dict[str, Any]] = {}
    for evidence in evidences:
        evidence_id = evidence.get("evidence_id")
        if isinstance(evidence_id, str) and evidence_id.startswith(prefix):
            alias = evidence_id.removeprefix(prefix)
            by_alias[alias] = evidence
    return by_alias


def _no_anomaly_report(
    *,
    agent_run: dict[str, Any],
    evidences: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if tasks:
        return None
    by_alias = evidence_by_alias(evidences, run_id=str(agent_run.get("run_id") or ""))
    if set(by_alias) != {"E1"}:
        return None
    e1 = by_alias["E1"]
    if e1.get("guard_status") != "passed":
        return None
    return {
        "status": "no_anomaly",
        "metric_id": agent_run.get("metric_id"),
        "target_date": _date_string(agent_run.get("target_date")),
        "evidence_ids": [str(e1["evidence_id"])],
    }


def _selected_candidate(e4_result_summary: dict[str, Any]) -> dict[str, Any] | None:
    selected = e4_result_summary.get("selected_candidate")
    if not isinstance(selected, dict):
        return None
    return selected


def _evidence_ids(candidate: dict[str, Any] | None) -> list[str]:
    if candidate is None:
        return []
    evidence_ids = candidate.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return []
    return [str(evidence_id) for evidence_id in evidence_ids if evidence_id is not None]


def _candidate_evidence_ids_are_current_run_passed(
    *,
    evidence_ids: list[str],
    evidences: list[dict[str, Any]],
    run_id: str,
) -> bool:
    required_aliases = {"E1", "E2", "E3", "E4", "E_rank"}
    actual = set(evidence_ids)
    if not all(
        any(_evidence_id_matches_alias(evidence_id, run_id=run_id, alias=alias) for evidence_id in actual)
        for alias in required_aliases
    ):
        return False
    by_id = {str(row.get("evidence_id")): row for row in evidences}
    for evidence_id in actual:
        if not evidence_id.startswith(f"{run_id}:"):
            return False
        row = by_id.get(evidence_id)
        if row is None:
            return False
        if row.get("run_id") != run_id:
            return False
        if row.get("guard_status") != "passed":
            return False
    return True


def _evidence_id_matches_alias(evidence_id: str, *, run_id: str, alias: str) -> bool:
    if not evidence_id.startswith(f"{run_id}:"):
        return False
    actual_alias = evidence_id.removeprefix(f"{run_id}:")
    return actual_alias == alias or actual_alias.startswith(f"{alias}_")


def _date_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)

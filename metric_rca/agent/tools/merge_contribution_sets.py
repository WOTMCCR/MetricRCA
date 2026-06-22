"""merge_contribution_sets tool: canonical E4 merge from per-chain contribution evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from metric_rca.agent.tools.runtime import ToolRuntimeError, evidence_row, persist_evidence, runtime_error, tool_error
from metric_rca.agent.tools.schemas import MergeContributionSetsArgs, ToolResult
from metric_rca.domain.models import ContributionSet, Evidence, Observation, QuerySpec, RootCauseCandidate
from metric_rca.runtime.contribution_set_builder import COMPOSITION_STRATEGY, ContributionSetBuilder


def merge_contribution_sets(args: MergeContributionSetsArgs, *, repository: Any) -> ToolResult:
    action = "merge_contribution_sets"
    if len(args.source_evidence_aliases) != len(set(args.source_evidence_aliases)):
        return tool_error(action, "ACTION_SCHEMA_INVALID", "source_evidence_aliases must be unique")
    if not args.source_evidence_aliases:
        return tool_error(action, "CONTRIBUTION_SET_MISSING", "at least one source contribution set is required")

    source_sets: list[tuple[str, ContributionSet]] = []
    source_rows: list[dict[str, Any]] = []
    for alias in args.source_evidence_aliases:
        evidence_id = f"{args.run_id}:{alias}"
        row = repository.get_evidence(run_id=args.run_id, evidence_id=evidence_id)
        if row is None or row.get("guard_status") != "passed":
            return tool_error(action, "EVIDENCE_MISSING", f"guard-passed source evidence is required: {evidence_id}")
        summary = row.get("result_summary")
        if not isinstance(summary, dict) or not isinstance(summary.get("contribution_set"), dict):
            return tool_error(action, "CONTRIBUTION_SET_MISSING", f"source evidence lacks contribution_set: {evidence_id}")
        source_sets.append((evidence_id, ContributionSet.model_validate(summary["contribution_set"])))
        source_rows.append(row)

    existing = repository.get_evidence(run_id=args.run_id, evidence_id=f"{args.run_id}:E4")
    if existing is not None:
        return tool_error(action, "E4_ALREADY_EXISTS", "canonical E4 already exists for this run")

    try:
        contribution_set = ContributionSetBuilder().merge(run_id=args.run_id, source_sets=source_sets)
    except ValueError as exc:
        return tool_error(action, str(exc), "contribution set merge failed")
    e4_id = f"{args.run_id}:E4"
    contribution_set = _with_canonical_e4(contribution_set, e4_id)

    first = source_rows[0]
    try:
        query_spec = _query_spec(first.get("query_spec"))
    except ValueError:
        return tool_error(action, "QUERY_SPEC_INVALID", "source evidence query_spec is invalid")
    source_metadata = _source_metadata(first)
    if isinstance(source_metadata, str):
        return tool_error(action, source_metadata, "source evidence is missing SQL metadata")
    sql_text, sql_hash, data_source = source_metadata
    selected_source_summary = _source_summary_for_selected(
        selected_candidate=contribution_set.selected_candidate,
        source_sets=source_sets,
        source_rows=source_rows,
    )
    if isinstance(selected_source_summary, str):
        return tool_error(action, selected_source_summary, "selected source contribution summary is missing")
    result_summary = {
        "metric_id": args.metric_id,
        "source_evidence_ids": [evidence_id for evidence_id, _ in source_sets],
        "contribution_set": contribution_set.model_dump(mode="json"),
        "selected_candidate": contribution_set.selected_candidate.model_dump(mode="json"),
        "candidates": [candidate.model_dump(mode="json") for candidate in contribution_set.candidates],
        "merge_strategy": "cross_chain_contribution_set_builder",
        "candidate_composition_strategy": COMPOSITION_STRATEGY,
    }
    if args.experience_advice is not None:
        result_summary["experience_advice"] = args.experience_advice.model_dump(mode="json")
    decomposition = selected_source_summary.get("decomposition")
    if isinstance(decomposition, dict):
        result_summary["decomposition"] = decomposition
    evidence = Evidence(
        evidence_id=e4_id,
        query_spec=query_spec,
        sql=sql_text,
        sql_hash=sql_hash,
        guard_status=str(first["guard_status"]),
        result_summary=result_summary,
        data_source=data_source,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    try:
        persist_evidence(repository=repository, row=evidence_row(args.run_id, evidence))
    except ToolRuntimeError as exc:
        return runtime_error(action, exc)
    return ToolResult(
        observation=Observation(
            action_name=action,
            ok=True,
            payload=result_summary,
            evidence_ids=[evidence.evidence_id],
        ),
        evidences=[evidence],
        evidence_alias="E4",
        candidates=contribution_set.candidates,
        sql_count=0,
    )


def _query_spec(raw: Any) -> QuerySpec:
    if not isinstance(raw, dict):
        raise ValueError("QUERY_SPEC_INVALID")
    return QuerySpec.model_validate(raw)


def _source_metadata(row: dict[str, Any]) -> tuple[str, str, str] | str:
    sql_text = row.get("sql_text")
    sql_hash = row.get("sql_hash")
    data_source = row.get("data_source")
    if not isinstance(sql_text, str) or not sql_text.strip():
        return "EVIDENCE_SQL_MISSING"
    if not isinstance(sql_hash, str) or not sql_hash.strip():
        return "EVIDENCE_SQL_HASH_MISSING"
    if not isinstance(data_source, str) or not data_source.strip():
        return "EVIDENCE_DATA_SOURCE_MISSING"
    return sql_text, sql_hash, data_source


def _with_canonical_e4(contribution_set: ContributionSet, e4_id: str) -> ContributionSet:
    candidates = [_candidate_with_evidence(candidate, e4_id) for candidate in contribution_set.candidates]
    selected_candidate = _candidate_with_evidence(contribution_set.selected_candidate, e4_id)
    evidence_ids = [*contribution_set.evidence_ids]
    if e4_id not in evidence_ids:
        evidence_ids.append(e4_id)
    return contribution_set.model_copy(
        update={
            "selected_candidate": selected_candidate,
            "candidates": candidates,
            "evidence_ids": evidence_ids,
        }
    )


def _candidate_with_evidence(candidate: RootCauseCandidate, evidence_id: str) -> RootCauseCandidate:
    evidence_ids = [*candidate.evidence_ids]
    if evidence_id not in evidence_ids:
        evidence_ids.append(evidence_id)
    return candidate.model_copy(update={"evidence_ids": evidence_ids})


def _source_summary_for_selected(
    *,
    selected_candidate: RootCauseCandidate,
    source_sets: list[tuple[str, ContributionSet]],
    source_rows: list[dict[str, Any]],
) -> dict[str, Any] | str:
    selected_key = _candidate_key(selected_candidate)
    for (_, source_set), source_row in zip(source_sets, source_rows, strict=True):
        source_candidate_keys = {_candidate_key(candidate) for candidate in source_set.candidates}
        if selected_key not in source_candidate_keys:
            continue
        summary = source_row.get("result_summary")
        if not isinstance(summary, dict):
            return "CONTRIBUTION_SET_MISSING"
        return summary
    return "CONTRIBUTION_SET_SELECTED_MISSING"


def _candidate_key(candidate: RootCauseCandidate) -> tuple[str, str | None, str | None]:
    return (candidate.root_cause_type, candidate.dimension, candidate.element)

"""attribute_rank node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import fail, start_timer, trace
from metric_rca.domain.models import Evidence, RootCauseCandidate


def attribute_rank(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    candidates = [_candidate(item) for item in state.get("candidates", [])]
    evidences = [_evidence(item) for item in state.get("evidences", [])]
    evidence_by_id = {evidence.evidence_id: evidence for evidence in evidences}
    if not candidates:
        update = fail("ATTRIBUTION_COVERAGE_LOW")
        return trace(
            dependencies=dependencies,
            state=state,
            node="attribute_rank",
            action="attribute_rank",
            input_summary={"candidate_count": 0},
            output_summary={"error_code": "ATTRIBUTION_COVERAGE_LOW"},
            error_code="ATTRIBUTION_COVERAGE_LOW",
            started_at=started,
        ) or update
    for candidate in candidates:
        if not candidate.evidence_ids:
            update = fail("EVIDENCE_MISSING")
            return trace(
                dependencies=dependencies,
                state=state,
                node="attribute_rank",
                action="attribute_rank",
                input_summary={"candidate_count": len(candidates)},
                output_summary={"error_code": "EVIDENCE_MISSING"},
                error_code="EVIDENCE_MISSING",
                started_at=started,
            ) or update
        for evidence_id in candidate.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None or not evidence.evidence_id.startswith(f"{state['run_id']}:"):
                update = fail("EVIDENCE_MISSING")
                return trace(
                    dependencies=dependencies,
                    state=state,
                    node="attribute_rank",
                    action="attribute_rank",
                    input_summary={"candidate_count": len(candidates)},
                    output_summary={"error_code": "EVIDENCE_MISSING"},
                    error_code="EVIDENCE_MISSING",
                    started_at=started,
                ) or update
            if evidence.guard_status != "passed":
                update = fail("SQL_GUARD_REJECTED")
                return trace(
                    dependencies=dependencies,
                    state=state,
                    node="attribute_rank",
                    action="attribute_rank",
                    input_summary={"candidate_count": len(candidates)},
                    output_summary={"error_code": "SQL_GUARD_REJECTED"},
                    error_code="SQL_GUARD_REJECTED",
                    started_at=started,
                ) or update
    ranked = sorted(candidates, key=lambda item: item.eng_confidence, reverse=True)
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="attribute_rank",
        action="attribute_rank",
        input_summary={"candidate_count": len(candidates)},
        output_summary={"top_root_cause_type": ranked[0].root_cause_type},
        started_at=started,
    )
    return trace_error or {"candidates": ranked}


def _candidate(item: Any) -> RootCauseCandidate:
    if isinstance(item, RootCauseCandidate):
        return item
    return RootCauseCandidate.model_validate(item)


def _evidence(item: Any) -> Evidence:
    if isinstance(item, Evidence):
        return item
    return Evidence.model_validate(item)

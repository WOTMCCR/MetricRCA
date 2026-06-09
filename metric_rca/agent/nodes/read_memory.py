"""read_memory node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import fail, start_timer, trace


def read_memory(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    if not getattr(dependencies.settings, "memory_enabled", False):
        update: dict[str, Any] = {"memory_hits": []}
        trace_error = trace(
            dependencies=dependencies,
            state=state,
            node="read_memory",
            action="read_memory",
            input_summary={"metric_id": state.get("metric_id")},
            output_summary={"memory_hits": 0, "memory_enabled": False},
            started_at=started,
        )
        return trace_error or update
    repo = getattr(dependencies, "memory_repo", None)
    if repo is None:
        update = fail("MEMORY_READ_FAILED")
        trace_error = trace(
            dependencies=dependencies,
            state=state,
            node="read_memory",
            action="read_memory",
            input_summary={"metric_id": state.get("metric_id")},
            output_summary={"error_code": "MEMORY_READ_FAILED"},
            error_code="MEMORY_READ_FAILED",
            started_at=started,
        )
        return trace_error or update
    try:
        hits = repo.read(_memory_key(state))
    except RuntimeError:
        update = fail("MEMORY_READ_FAILED")
        trace_error = trace(
            dependencies=dependencies,
            state=state,
            node="read_memory",
            action="read_memory",
            input_summary={"metric_id": state.get("metric_id")},
            output_summary={"error_code": "MEMORY_READ_FAILED"},
            error_code="MEMORY_READ_FAILED",
            started_at=started,
        )
        return trace_error or update
    update = {"memory_hits": hits}
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="read_memory",
        action="read_memory",
        input_summary={"metric_id": state.get("metric_id")},
        output_summary={"memory_hits": len(hits)},
        started_at=started,
    )
    return trace_error or update


def _memory_key(state: dict[str, Any]) -> str:
    parsed = state.get("parsed_spec") or {}
    dimension = parsed.get("dimension")
    if not dimension:
        dimension = "metric"
    return f"{state.get('metric_id')}|{dimension}"

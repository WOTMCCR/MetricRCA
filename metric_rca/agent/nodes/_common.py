"""Shared node helpers that do not own business logic."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from metric_rca.observability.trace import TraceWriteError


def start_timer() -> float:
    return perf_counter()


def trace(
    *,
    dependencies: Any,
    state: dict[str, Any],
    node: str,
    action: str | None,
    input_summary: dict[str, Any],
    output_summary: dict[str, Any],
    error_code: str | None = None,
    started_at: float | None = None,
) -> dict[str, Any]:
    writer = getattr(dependencies, "trace_writer", None)
    if writer is None:
        return fail("SYSTEM_TABLE_WRITE_FAILED")
    try:
        writer.write_step(
            run_id=state["run_id"],
            node=node,
            action=action,
            input_summary=input_summary,
            output_summary=output_summary,
            error_code=error_code,
            started_at=started_at,
        )
    except TraceWriteError as exc:
        return fail(exc.code)
    return {}


def fail(error_code: str, *, status: str = "failed") -> dict[str, Any]:
    return {"error_code": error_code, "status": status}


def code_from_message(message: str, *, default: str) -> str:
    code = message.split(":", maxsplit=1)[0]
    return code if code and code.isupper() else default


def dump_model(item: Any) -> Any:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    return item

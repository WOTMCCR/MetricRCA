"""Shared trace and agent_run persistence boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class TraceWriteError(Exception):
    code: str
    message: str


class TraceWriter:
    """Owns trace_step seq, latency, error_code, and agent_run lifecycle writes."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository
        self._next_seq_by_run: dict[str, int] = {}

    def start_run(self, *, run_id: str, question: str, target_date: date) -> None:
        try:
            self._repository.create_agent_run(
                {
                    "run_id": run_id,
                    "question": question,
                    "metric_id": None,
                    "target_date": target_date,
                    "status": "running",
                    "error_code": None,
                    "created_at": _now(),
                    "finished_at": None,
                }
            )
        except RuntimeError as exc:
            raise _trace_error(exc) from exc
        self._next_seq_by_run[run_id] = 1

    def set_run_context(self, *, run_id: str, metric_id: str, target_date: date) -> None:
        try:
            self._repository.update_agent_run_context(
                run_id=run_id,
                metric_id=metric_id,
                target_date=target_date,
            )
        except RuntimeError as exc:
            raise _trace_error(exc) from exc

    def finish_run(self, *, run_id: str, status: str, error_code: str | None) -> None:
        try:
            self._repository.finish_agent_run(
                run_id=run_id,
                status=status,
                error_code=error_code,
                finished_at=_now(),
            )
        except RuntimeError as exc:
            raise _trace_error(exc) from exc

    def write_step(
        self,
        *,
        run_id: str,
        node: str,
        action: str | None,
        input_summary: dict[str, Any],
        output_summary: dict[str, Any],
        error_code: str | None = None,
        started_at: float | None = None,
    ) -> None:
        seq = self._next_seq_by_run.get(run_id, 1)
        latency_ms = 0
        if started_at is not None:
            latency_ms = max(0, int((_perf_counter() - started_at) * 1000))
        row = {
            "step_id": f"{run_id}:trace:{seq}:{uuid4().hex[:8]}",
            "run_id": run_id,
            "seq": seq,
            "node": node,
            "action": action,
            "input_summary": _jsonable(input_summary),
            "output_summary": _jsonable(output_summary),
            "error_code": error_code,
            "latency_ms": latency_ms,
            "created_at": _now(),
        }
        try:
            self._repository.create_trace_step(row)
        except RuntimeError as exc:
            raise _trace_error(exc) from exc
        self._next_seq_by_run[run_id] = seq + 1


def _trace_error(exc: RuntimeError) -> TraceWriteError:
    code = str(exc).split(":", maxsplit=1)[0]
    if code == "SYSTEM_TABLE_WRITE_FAILED":
        return TraceWriteError(code, "system table write failed")
    return TraceWriteError("SYSTEM_TABLE_WRITE_FAILED", "system table write failed")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _jsonable(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, default=str))


def _perf_counter() -> float:
    from time import perf_counter

    return perf_counter()

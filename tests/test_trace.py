from __future__ import annotations

from datetime import date

import pytest

from metric_rca.observability.trace import TraceWriteError, TraceWriter


def test_trace_writer_is_single_seq_error_latency_boundary() -> None:
    repo = _TraceRepository()
    writer = TraceWriter(repo)
    writer.start_run(run_id="run-1", question="q", target_date=date(2026, 6, 5))

    writer.write_step(
        run_id="run-1",
        node="parse_question",
        action="parse_question",
        input_summary={"question": "q"},
        output_summary={"metric_id": "gmv"},
    )
    writer.write_step(
        run_id="run-1",
        node="error_return",
        action="error_return",
        input_summary={},
        output_summary={"status": "failed"},
        error_code="PARSE_FAILED",
    )

    assert [row["seq"] for row in repo.trace_rows] == [1, 2]
    assert all(row["latency_ms"] >= 0 for row in repo.trace_rows)
    assert repo.trace_rows[-1]["error_code"] == "PARSE_FAILED"


def test_agent_run_lifecycle_persists_status_error_and_finished_at() -> None:
    repo = _TraceRepository()
    writer = TraceWriter(repo)

    writer.start_run(run_id="run-1", question="q", target_date=date(2026, 6, 5))
    writer.set_run_context(run_id="run-1", metric_id="gmv", target_date=date(2026, 6, 5))
    writer.finish_run(run_id="run-1", status="failed", error_code="REFLECTION_REPAIR_FAILED")

    assert repo.agent_rows[0]["status"] == "running"
    assert repo.context_updates[-1]["metric_id"] == "gmv"
    assert repo.finish_updates[-1]["status"] == "failed"
    assert repo.finish_updates[-1]["error_code"] == "REFLECTION_REPAIR_FAILED"
    assert repo.finish_updates[-1]["finished_at"] is not None


def test_system_table_write_failure_returns_typed_graph_error() -> None:
    writer = TraceWriter(_FailingTraceRepository())

    with pytest.raises(TraceWriteError) as exc_info:
        writer.write_step(
            run_id="run-1",
            node="parse_question",
            action="parse_question",
            input_summary={},
            output_summary={},
        )

    assert exc_info.value.code == "SYSTEM_TABLE_WRITE_FAILED"
    assert exc_info.value.message == "SYSTEM_TABLE_WRITE_FAILED"


class _TraceRepository:
    def __init__(self) -> None:
        self.agent_rows: list[dict] = []
        self.trace_rows: list[dict] = []
        self.context_updates: list[dict] = []
        self.finish_updates: list[dict] = []

    def create_agent_run(self, row: dict) -> None:
        self.agent_rows.append(row)

    def update_agent_run_context(self, *, run_id: str, metric_id: str, target_date) -> None:
        self.context_updates.append({"run_id": run_id, "metric_id": metric_id, "target_date": target_date})

    def finish_agent_run(
        self,
        *,
        run_id: str,
        status: str,
        error_code: str | None,
        finished_at,
        total_tokens=None,
        total_latency_ms=None,
        token_breakdown=None,
    ) -> None:
        self.finish_updates.append(
            {
                "run_id": run_id,
                "status": status,
                "error_code": error_code,
                "finished_at": finished_at,
                "total_tokens": total_tokens,
                "total_latency_ms": total_latency_ms,
                "token_breakdown": token_breakdown,
            }
        )

    def create_trace_step(self, row: dict) -> None:
        self.trace_rows.append(row)


class _FailingTraceRepository(_TraceRepository):
    def create_trace_step(self, row: dict) -> None:
        raise RuntimeError("SYSTEM_TABLE_WRITE_FAILED")

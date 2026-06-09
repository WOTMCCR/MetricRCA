"""parse_question node."""

from __future__ import annotations

from typing import Any

from metric_rca.agent.nodes._common import fail, start_timer, trace
from metric_rca.observability.trace import TraceWriteError
from metric_rca.services.metric_service import MetricServiceError


def parse_question(state: dict[str, Any], *, dependencies: Any) -> dict[str, Any]:
    started = start_timer()
    try:
        parsed = dependencies.metric_service.parse_question(
            state["question"],
            business_today=dependencies.settings.business_today,
        )
    except MetricServiceError as exc:
        update = fail(exc.code)
        trace_error = trace(
            dependencies=dependencies,
            state=state,
            node="parse_question",
            action="parse_question",
            input_summary={"question": state.get("question")},
            output_summary={"error_code": exc.code},
            error_code=exc.code,
            started_at=started,
        )
        return trace_error or update

    if getattr(dependencies, "trace_writer", None) is not None:
        try:
            dependencies.trace_writer.set_run_context(
                run_id=state["run_id"],
                metric_id=parsed.metric_id,
                target_date=parsed.target_date,
            )
        except TraceWriteError as exc:
            return fail(exc.code)
    update = {
        "metric_id": parsed.metric_id,
        "target_date": parsed.target_date,
        "parsed_spec": parsed.model_dump(mode="json"),
    }
    trace_error = trace(
        dependencies=dependencies,
        state=state,
        node="parse_question",
        action="parse_question",
        input_summary={"question": state.get("question")},
        output_summary={"metric_id": parsed.metric_id, "target_date": parsed.target_date.isoformat()},
        started_at=started,
    )
    return trace_error or update

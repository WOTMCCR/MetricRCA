"""deepagents middleware for MetricRCA guard semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from time import perf_counter
from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage
from langchain_core.outputs import LLMResult
from pydantic import BaseModel, ValidationError

from metric_rca.agent.deep_tools import DATA_FETCHING_TOOLS, EXPOSED_TOOL_NAMES, PLANNING_TOOL_NAME
from metric_rca.observability.trace import TraceWriteError

try:
    from langchain.agents.middleware import AgentMiddleware as _AgentMiddlewareBase
except ModuleNotFoundError:
    _AgentMiddlewareBase = object


class GuardMiddlewareError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class RunGuardContext:
    run_id: str
    settings: Any
    trace_writer: Any
    tool_arg_schemas: dict[str, type[BaseModel]]
    repository: Any | None = None
    step_count: int = 0
    query_count: int = 0
    drilldown_depth: int = 0
    budget_exhausted_once: bool = False
    failed: bool = False
    error_code: str | None = None
    token_usage_by_call: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_token_usage: list[dict[str, Any]] = field(default_factory=list)

    def mark_failed(self, code: str) -> None:
        self.failed = True
        self.error_code = code

    def record_token_usage(self, usage: dict[str, Any]) -> None:
        self.pending_token_usage.append(usage)

    def token_usage_for_call(self, tool_call_id: str) -> dict[str, Any] | None:
        return self.token_usage_by_call.get(tool_call_id) or (
            self.pending_token_usage.pop(0) if self.pending_token_usage else None
        )

    def drain_pending_token_usage(self) -> list[dict[str, Any]]:
        pending = [*self.pending_token_usage]
        self.pending_token_usage.clear()
        return pending


class MetricRCATokenUsageCallback(BaseCallbackHandler):
    """Capture model token usage for the next guarded tool trace."""

    def __init__(self, context: RunGuardContext) -> None:
        self.context = context

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        usage = _token_usage_from_llm_result(response)
        if usage:
            self.context.record_token_usage(usage)


class GuardMiddleware(_AgentMiddlewareBase):
    """Enforce P6 tool guards at the deepagents tool-call boundary."""

    def __init__(self, context: RunGuardContext) -> None:
        self.context = context

    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], ToolMessage],
    ) -> ToolMessage:
        tool_name = _tool_name(request)
        tool_call_id = _tool_call_id(request)
        args = _tool_args(request)
        started_at = perf_counter()

        if tool_name not in EXPOSED_TOOL_NAMES:
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="ACTION_SCHEMA_INVALID",
                message=f"tool is not registered: {tool_name}",
                args=args,
                started_at=started_at,
            )

        if tool_name != PLANNING_TOOL_NAME:
            schema = self.context.tool_arg_schemas.get(tool_name)
            if schema is None:
                return self._reject(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    code="ACTION_SCHEMA_INVALID",
                    message=f"tool schema is not registered: {tool_name}",
                    args=args,
                    started_at=started_at,
                )
            try:
                schema.model_validate(args)
            except ValidationError as exc:
                return self._reject(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    code="ACTION_SCHEMA_INVALID",
                    message=exc.errors()[0]["msg"],
                    args=args,
                    started_at=started_at,
                )

        budget_error = self._budget_error(tool_name)
        if budget_error is not None:
            self.context.budget_exhausted_once = True
            if tool_name in DATA_FETCHING_TOOLS:
                self.context.mark_failed("BUDGET_EXCEEDED")
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="BUDGET_EXCEEDED",
                message=budget_error,
                args=args,
                started_at=started_at,
            )

        self._increment_budget(tool_name)
        result = handler(request)
        payload = _message_payload(result)
        error_code = _payload_error_code(payload)
        if tool_name in DATA_FETCHING_TOOLS and not _payload_evidence_ids(payload):
            error_code = error_code or "EVIDENCE_MISSING"
            self.context.mark_failed(error_code)
            result = _tool_message(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                payload={
                    "observation": {
                        "action_name": tool_name,
                        "ok": False,
                        "error_code": error_code,
                        "message": "data tool returned no evidence_id",
                    },
                    "evidence_ids": [],
                },
            )
            payload = _message_payload(result)
        if error_code in {"ACTION_SCHEMA_INVALID", "BUDGET_EXCEEDED"}:
            self.context.mark_failed(error_code)
        self._trace(
            tool_name=tool_name,
            args=args,
            payload=payload,
            error_code=error_code,
            started_at=started_at,
            token_usage=self.context.token_usage_for_call(tool_call_id),
        )
        return result

    def _budget_error(self, tool_name: str) -> str | None:
        max_steps = int(getattr(self.context.settings, "max_steps", 8))
        max_query = int(getattr(self.context.settings, "max_query", 12))
        max_drilldown_depth = int(getattr(self.context.settings, "max_drilldown_depth", 2))
        if self.context.step_count >= max_steps:
            return "step budget exhausted; call rank_root_causes or stop"
        if tool_name in DATA_FETCHING_TOOLS and self.context.query_count >= max_query:
            return "query budget exhausted; call rank_root_causes or stop"
        if tool_name == "drilldown_dimension" and self.context.drilldown_depth >= max_drilldown_depth:
            return "drilldown depth exhausted; call rank_root_causes or stop"
        if self.context.budget_exhausted_once and tool_name in DATA_FETCHING_TOOLS:
            return "data tool attempted after budget exhaustion"
        return None

    def _increment_budget(self, tool_name: str) -> None:
        self.context.step_count += 1
        if tool_name in DATA_FETCHING_TOOLS:
            self.context.query_count += 1
        if tool_name == "drilldown_dimension":
            self.context.drilldown_depth += 1

    def _reject(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        code: str,
        message: str,
        args: dict[str, Any],
        started_at: float,
    ) -> ToolMessage:
        self.context.mark_failed(code)
        payload = {
            "observation": {
                "action_name": tool_name,
                "ok": False,
                "error_code": code,
                "message": message,
            },
            "evidence_ids": [],
        }
        self._trace(tool_name=tool_name, args=args, payload=payload, error_code=code, started_at=started_at)
        return _tool_message(tool_call_id=tool_call_id, tool_name=tool_name, payload=payload)

    def _trace(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        payload: dict[str, Any],
        error_code: str | None,
        started_at: float,
        token_usage: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.context.trace_writer.write_step(
                run_id=self.context.run_id,
                node="tool_call",
                action=tool_name,
                input_summary={"args": args},
                output_summary=payload,
                error_code=error_code,
                started_at=started_at,
                token_usage=token_usage,
            )
        except TraceWriteError as exc:
            self.context.mark_failed(exc.code)
            raise GuardMiddlewareError(exc.code, exc.message) from exc


def _tool_name(request: Any) -> str:
    tool_call = getattr(request, "tool_call", None) or {}
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", ""))


def _tool_call_id(request: Any) -> str:
    tool_call = getattr(request, "tool_call", None) or {}
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or tool_call.get("tool_call_id") or "tool-call")
    return str(getattr(tool_call, "id", "tool-call"))


def _tool_args(request: Any) -> dict[str, Any]:
    tool_call = getattr(request, "tool_call", None) or {}
    if isinstance(tool_call, dict):
        raw = tool_call.get("args") or {}
    else:
        raw = getattr(tool_call, "args", {}) or {}
    return dict(raw)


def _tool_message(*, tool_call_id: str, tool_name: str, payload: dict[str, Any]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload, default=str),
        tool_call_id=tool_call_id,
        name=tool_name,
    )


def _message_payload(message: ToolMessage) -> dict[str, Any]:
    content = getattr(message, "content", "")
    if isinstance(content, dict):
        return content
    try:
        parsed = json.loads(str(content))
    except json.JSONDecodeError:
        return {"raw": str(content)}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _payload_error_code(payload: dict[str, Any]) -> str | None:
    observation = payload.get("observation")
    if isinstance(observation, dict):
        return observation.get("error_code")
    return payload.get("error_code")


def _payload_evidence_ids(payload: dict[str, Any]) -> list[str]:
    evidence_ids = payload.get("evidence_ids")
    if isinstance(evidence_ids, list):
        return [str(item) for item in evidence_ids]
    observation = payload.get("observation")
    if isinstance(observation, dict) and isinstance(observation.get("evidence_ids"), list):
        return [str(item) for item in observation["evidence_ids"]]
    return []


def _token_usage_from_llm_result(response: LLMResult) -> dict[str, Any] | None:
    llm_output = response.llm_output or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage")
    if isinstance(usage, dict):
        return dict(usage)
    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            metadata = getattr(message, "usage_metadata", None)
            if isinstance(metadata, dict):
                return dict(metadata)
            response_metadata = getattr(message, "response_metadata", None)
            if isinstance(response_metadata, dict):
                token_usage = response_metadata.get("token_usage") or response_metadata.get("usage")
                if isinstance(token_usage, dict):
                    return dict(token_usage)
    return None

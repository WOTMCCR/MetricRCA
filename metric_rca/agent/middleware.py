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

from collections.abc import Mapping

from langchain.agents.middleware import AgentMiddleware as _AgentMiddlewareBase

from metric_rca.agent.deep_tools import DATA_FETCHING_TOOLS, EXPOSED_TOOL_NAMES, PLANNING_TOOL_NAME, RANK_TOOL_NAME
from metric_rca.agent.discovery_policy import DiscoveryPolicy
from metric_rca.agent.evidence_aliases import e2_alias_for_e3_id
from metric_rca.observability.trace import TraceWriteError

RECOVERABLE_TOOL_ERROR_CODES = frozenset(
    {
        "ACTION_SCHEMA_INVALID",
        "DIMENSION_NOT_ALLOWED",
        "EVIDENCE_MISSING",
        "METRIC_NOT_FOUND",
        "METRIC_SCOPE_VIOLATION",
        "QUERY_SPEC_INVALID",
        "ADTRIBUTOR_NOT_APPLICABLE",
        "E4_ALREADY_EXISTS",
        "E3_ALREADY_EXISTS",
        "E1_ALREADY_EXISTS",
    }
)


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
    last_schema_invalid_tool: str | None = None
    consecutive_schema_invalid_count: int = 0
    explicit_filters: dict[str, str] = field(default_factory=dict)
    target_metric_id: str | None = None
    target_date: Any | None = None
    discovery_policy: DiscoveryPolicy = field(default_factory=DiscoveryPolicy)
    required_repair_action: str | None = None

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
        started_at = perf_counter()
        try:
            args = _tool_args(request)
        except _InvalidToolArgsError as exc:
            return self._reject_schema_invalid(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                message=exc.message,
                args={},
                started_at=started_at,
            )

        if tool_name not in EXPOSED_TOOL_NAMES:
            return self._reject_schema_invalid(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                message=f"tool is not registered: {tool_name}",
                args=args,
                started_at=started_at,
            )

        if tool_name != PLANNING_TOOL_NAME:
            schema = self.context.tool_arg_schemas.get(tool_name)
            if schema is None:
                return self._reject_schema_invalid(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    message=f"tool schema is not registered: {tool_name}",
                    args=args,
                    started_at=started_at,
                )
            try:
                schema.model_validate(args)
            except ValidationError as exc:
                return self._reject_schema_invalid(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    message=exc.errors()[0]["msg"],
                    args=args,
                    started_at=started_at,
                )

        repair_action_error = self._repair_action_error(tool_name)
        if repair_action_error is not None:
            self._reset_schema_invalid_streak()
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="ACTION_SCHEMA_INVALID",
                message=repair_action_error,
                args=args,
                started_at=started_at,
                mark_failed=False,
            )

        metric_scope_error = self._metric_scope_error(tool_name, args)
        if metric_scope_error is not None:
            self._reset_schema_invalid_streak()
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="METRIC_SCOPE_VIOLATION",
                message=metric_scope_error,
                args=args,
                started_at=started_at,
                mark_failed=False,
            )

        target_date_error = self._target_date_scope_error(tool_name, args)
        if target_date_error is not None:
            self._reset_schema_invalid_streak()
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="METRIC_SCOPE_VIOLATION",
                message=target_date_error,
                args=args,
                started_at=started_at,
                mark_failed=False,
            )

        scope_error = self._explicit_scope_error(tool_name, args)
        if scope_error is not None:
            self._reset_schema_invalid_streak()
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="ACTION_SCHEMA_INVALID",
                message=scope_error,
                args=args,
                started_at=started_at,
                mark_failed=False,
            )

        evidence_error = self._evidence_id_prefix_error(tool_name, args)
        if evidence_error is not None:
            self._reset_schema_invalid_streak()
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="EVIDENCE_MISSING",
                message=evidence_error,
                args=args,
                started_at=started_at,
                mark_failed=False,
            )

        fetch_filter_error = self._fetch_filter_error(tool_name, args)
        if fetch_filter_error is not None:
            self._reset_schema_invalid_streak()
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="ACTION_SCHEMA_INVALID",
                message=fetch_filter_error,
                args=args,
                started_at=started_at,
                mark_failed=False,
            )

        existing_drilldown_payload = self._existing_drilldown_reuse_payload(tool_name, args)
        if existing_drilldown_payload is not None:
            self._reset_schema_invalid_streak()
            self._trace(
                tool_name=tool_name,
                args=args,
                payload=existing_drilldown_payload,
                error_code=None,
                started_at=started_at,
                token_usage=self.context.token_usage_for_call(tool_call_id),
            )
            return _tool_message(tool_call_id=tool_call_id, tool_name=tool_name, payload=existing_drilldown_payload)

        flow_error = self._evidence_flow_error(tool_name, args)
        if flow_error is not None:
            self._reset_schema_invalid_streak()
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="E3_ALREADY_EXISTS",
                message=flow_error,
                args=args,
                started_at=started_at,
                mark_failed=False,
            )

        first_signal_error = self._first_signal_policy_error(tool_name, args)
        if first_signal_error is not None:
            self._reset_schema_invalid_streak()
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="ACTION_SCHEMA_INVALID",
                message=first_signal_error,
                args=args,
                started_at=started_at,
                mark_failed=False,
            )

        discovery_error = self._discovery_drilldown_error(tool_name, args)
        if discovery_error is not None:
            self._reset_schema_invalid_streak()
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="EVIDENCE_MISSING",
                message=discovery_error,
                args=args,
                started_at=started_at,
                mark_failed=False,
            )

        self._reset_schema_invalid_streak()
        budget_error = None if _has_placeholder_evidence_ids(args) else self._budget_error(tool_name, args)
        if budget_error is not None:
            if budget_error != "drilldown depth exhausted; call rank_root_causes or stop":
                self.context.budget_exhausted_once = True
            mark_failed = budget_error == "data tool attempted after budget exhaustion"
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="BUDGET_EXCEEDED",
                message=budget_error,
                args=args,
                started_at=started_at,
                mark_failed=mark_failed,
            )

        contribution_scope_error = self._contribution_e3_scope_error(tool_name, args)
        if contribution_scope_error is not None:
            self._reset_schema_invalid_streak()
            return self._reject(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                code="EVIDENCE_MISSING",
                message=contribution_scope_error,
                args=args,
                started_at=started_at,
                mark_failed=False,
            )

        self._increment_budget(tool_name)
        result = handler(request)
        payload = _message_payload(result)
        error_code = _payload_error_code(payload)
        recoverable_missing_evidence = False
        if tool_name in DATA_FETCHING_TOOLS and not _payload_evidence_ids(payload):
            if _payload_observation_ok(payload) is False and error_code in RECOVERABLE_TOOL_ERROR_CODES:
                recoverable_missing_evidence = True
            else:
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
        if recoverable_missing_evidence:
            self._decrement_budget(tool_name)
        if tool_name == "detect_anomaly" and _payload_evidence_ids(payload) and not self.context.explicit_filters:
            filters = _string_filters(args.get("filters"))
            if len(filters) == 1:
                self.context.explicit_filters = filters
        self._trace(
            tool_name=tool_name,
            args=args,
            payload=payload,
            error_code=error_code,
            started_at=started_at,
            token_usage=self.context.token_usage_for_call(tool_call_id),
        )
        return result

    def _budget_error(self, tool_name: str, args: dict[str, Any]) -> str | None:
        max_steps = int(getattr(self.context.settings, "max_steps", 8))
        max_query = int(getattr(self.context.settings, "max_query", 12))
        max_drilldown_depth = int(getattr(self.context.settings, "max_drilldown_depth", 2))
        contribution_finalizer = self._is_matching_e4_contribution_finalizer(tool_name, args)
        if self.context.budget_exhausted_once and tool_name in DATA_FETCHING_TOOLS and not contribution_finalizer:
            return "data tool attempted after budget exhaustion"
        if (
            self.context.step_count >= max_steps
            and tool_name not in {RANK_TOOL_NAME, PLANNING_TOOL_NAME}
            and not contribution_finalizer
        ):
            return "step budget exhausted; call rank_root_causes or stop"
        if tool_name in DATA_FETCHING_TOOLS and self.context.query_count >= max_query:
            return "query budget exhausted; call rank_root_causes or stop"
        if tool_name == "drilldown_dimension" and self.context.drilldown_depth >= max_drilldown_depth:
            return "drilldown depth exhausted; call rank_root_causes or stop"
        return None

    def _existing_drilldown_reuse_payload(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        if tool_name != "drilldown_dimension" or self.context.repository is None:
            return None
        run_id = self.context.run_id
        dimension = str(args.get("dimension") or "")
        for alias in [f"E2_{dimension}", "E2"]:
            evidence_id = f"{run_id}:{alias}"
            row = self.context.repository.get_evidence(run_id=run_id, evidence_id=evidence_id)
            if row is None or row.get("guard_status") != "passed":
                continue
            summary = row.get("result_summary")
            if not isinstance(summary, dict):
                continue
            if (
                summary.get("metric_id") == args.get("metric_id")
                and summary.get("dimension") == dimension
                and _string_filters(summary.get("filters")) == _string_filters(args.get("filters"))
                and set(str(item) for item in summary.get("input_evidence_ids", []))
                .issubset({str(item) for item in args.get("evidence_ids", [])})
            ):
                return {
                    "observation": {
                        "action_name": tool_name,
                        "ok": True,
                        "payload": summary,
                        "evidence_ids": [evidence_id],
                    },
                    "evidence_ids": [evidence_id],
                    "candidates": summary.get("candidates") if isinstance(summary.get("candidates"), list) else [],
                }
        return None

    def _is_matching_e4_contribution_finalizer(self, tool_name: str, args: dict[str, Any]) -> bool:
        if tool_name != "calculate_contribution" or self.context.repository is None:
            return False
        run_id = self.context.run_id
        if self.context.repository.get_evidence(run_id=run_id, evidence_id=f"{run_id}:E4") is not None:
            return False
        rows = self.context.repository.get_evidences(run_id)
        e3_id = _first_alias_family_evidence_id(rows, run_id=run_id, alias="E3")
        if e3_id is None:
            return False
        e3_row = self.context.repository.get_evidence(run_id=run_id, evidence_id=e3_id)
        summary = e3_row.get("result_summary") if isinstance(e3_row, dict) else None
        if not isinstance(summary, dict):
            return False
        if summary.get("dimension") != args.get("dimension") or str(summary.get("element")) != str(args.get("element")):
            return False
        expected_chain = set(_contribution_chain_for_e3(rows, run_id=run_id, e3_id=e3_id))
        supplied = {str(item) for item in args.get("evidence_ids", [])}
        return expected_chain.issubset(supplied)

    def _increment_budget(self, tool_name: str) -> None:
        self.context.step_count += 1
        if tool_name in DATA_FETCHING_TOOLS:
            self.context.query_count += 1
        if tool_name == "drilldown_dimension":
            self.context.drilldown_depth += 1

    def _decrement_budget(self, tool_name: str) -> None:
        self.context.step_count = max(0, self.context.step_count - 1)
        if tool_name in DATA_FETCHING_TOOLS:
            self.context.query_count = max(0, self.context.query_count - 1)
        if tool_name == "drilldown_dimension":
            self.context.drilldown_depth = max(0, self.context.drilldown_depth - 1)

    def _explicit_scope_error(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name not in {"detect_anomaly", "drilldown_dimension", "fetch_related_signal", "calculate_contribution"}:
            return None
        if len(self.context.explicit_filters) != 1:
            return None
        dimension, element = next(iter(self.context.explicit_filters.items()))
        filters = _string_filters(args.get("filters"))
        if tool_name == "detect_anomaly":
            if filters.get(dimension) != element:
                return (
                    f"explicit question scope requires filters.{dimension}={element}; "
                    f"retry detect_anomaly with filters={{'{dimension}': '{element}'}}"
                )
            return None
        if args.get("dimension") != dimension:
            return (
                f"explicit question scope requires dimension={dimension}; "
                f"retry {tool_name} with dimension={dimension} and filters={{'{dimension}': '{element}'}}"
            )
        if tool_name in {"fetch_related_signal", "calculate_contribution"} and str(args.get("element")) != element:
            return f"explicit question scope requires element={element}; retry {tool_name} with element={element}"
        if tool_name in {"drilldown_dimension", "calculate_contribution"} and filters.get(dimension) != element:
            return (
                f"explicit question scope requires filters.{dimension}={element}; "
                f"retry {tool_name} with filters={{'{dimension}': '{element}'}}"
            )
        return None

    def _metric_scope_error(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name == PLANNING_TOOL_NAME or not self.context.target_metric_id:
            return None
        metric_id = args.get("metric_id")
        if metric_id is None or str(metric_id) == str(self.context.target_metric_id):
            return None
        return (
            f"run target metric is {self.context.target_metric_id}; "
            f"retry {tool_name} with metric_id={self.context.target_metric_id}"
        )

    def _target_date_scope_error(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name == PLANNING_TOOL_NAME or self.context.target_date is None:
            return None
        target_date = args.get("target_date")
        expected = _iso_text(self.context.target_date)
        if target_date is None or _iso_text(target_date) == expected:
            return None
        return (
            f"run target_date is {expected}; "
            f"retry {tool_name} with target_date={expected}"
        )

    def _fetch_filter_error(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name != "fetch_related_signal":
            return None
        filters = _string_filters(args.get("filters"))
        if not filters:
            return None
        expected = {str(args.get("dimension")): str(args.get("element"))}
        if filters == expected:
            return None
        return (
            "fetch_related_signal filters must be empty or exactly match the selected "
            f"dimension/element {expected}"
        )

    def _contribution_e3_scope_error(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name != "calculate_contribution" or self.context.repository is None:
            return None
        evidence_ids = args.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            return None
        run_id = self.context.run_id
        e3_ids = [
            str(evidence_id)
            for evidence_id in evidence_ids
            if _evidence_id_has_alias_family(str(evidence_id), run_id=run_id, alias="E3")
        ]
        if not e3_ids:
            return None
        e3_id = e3_ids[0]
        e3_row = self.context.repository.get_evidence(run_id=run_id, evidence_id=e3_id)
        if e3_row is None or e3_row.get("guard_status") != "passed":
            return f"calculate_contribution requires guard-passed E3 evidence; missing {e3_id}"
        summary = e3_row.get("result_summary")
        if not isinstance(summary, dict):
            return f"calculate_contribution requires structured E3 result_summary for {e3_id}"
        expected_dimension = summary.get("dimension")
        expected_element = summary.get("element")
        if expected_dimension is not None and str(args.get("dimension")) != str(expected_dimension):
            return (
                f"calculate_contribution dimension must match {e3_id}: dimension={expected_dimension}; "
                f"retry with dimension={expected_dimension}, element={expected_element}, and the matching E1/E2/E3 evidence_ids"
            )
        if expected_element is not None and str(args.get("element")) != str(expected_element):
            return (
                f"calculate_contribution element must match {e3_id}: element={expected_element}; "
                f"retry with dimension={expected_dimension}, element={expected_element}, and the matching E1/E2/E3 evidence_ids"
            )
        e2_alias = e2_alias_for_e3_id(e3_id, run_id=run_id)
        supplied = {str(item) for item in evidence_ids}
        if e2_alias is not None and not any(
            _evidence_id_has_alias_family(evidence_id, run_id=run_id, alias=e2_alias)
            for evidence_id in supplied
        ):
            return (
                f"calculate_contribution evidence_ids must include {e2_alias} for {e3_id}; "
                f"retry with the matching E1/{e2_alias}/E3 chain"
            )
        return None

    def _first_signal_policy_error(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name != "fetch_related_signal" or self.context.explicit_filters:
            return None
        policy = self.context.discovery_policy
        expected_dimension = policy.first_signal_dimension
        expected_signal_type = policy.first_signal_type
        expected_element = policy.first_signal_element
        if expected_dimension is None and expected_signal_type is None:
            return None
        if args.get("dimension") == expected_dimension and args.get("signal_type") == expected_signal_type:
            if expected_element is not None and str(args.get("element")) != expected_element:
                return (
                    "analysis policy requires the first related signal to use "
                    f"dimension={expected_dimension}, signal_type={expected_signal_type}, "
                    f"element={expected_element}; retry fetch_related_signal with "
                    f"dimension={expected_dimension}, signal_type={expected_signal_type}, "
                    f"element={expected_element}, and the exact E1/E2_{expected_dimension} evidence_ids"
                )
            if not policy.enforce_first_signal_top_candidate or expected_dimension is None:
                return None
            expected_element = self._top_drilldown_candidate_element(expected_dimension)
            if expected_element is None:
                return (
                    "analysis policy requires structured top-candidate drilldown evidence for "
                    f"dimension={expected_dimension}; retry drilldown_dimension for {expected_dimension} "
                    f"or provide guard-passed E2_{expected_dimension} with candidates before fetch_related_signal"
                )
            if str(args.get("element")) == expected_element:
                return None
            return (
                "analysis policy requires the first related signal to use the strongest "
                f"{expected_dimension} drilldown candidate element={expected_element}; "
                f"retry fetch_related_signal with dimension={expected_dimension}, "
                f"signal_type={expected_signal_type}, element={expected_element}, and the exact "
                f"E1/E2_{expected_dimension} evidence_ids"
            )
        if expected_dimension is None or expected_signal_type is None:
            return None
        return (
            "analysis policy requires the first related signal to use "
            f"dimension={expected_dimension}, signal_type={expected_signal_type}"
            f"{f', element={expected_element}' if expected_element is not None else ''}; "
            f"retry fetch_related_signal with dimension={expected_dimension}, "
            f"signal_type={expected_signal_type}"
            f"{f', element={expected_element}' if expected_element is not None else ''}, "
            f"and the exact E1/E2_{expected_dimension} evidence_ids"
        )

    def _discovery_drilldown_error(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name not in {"fetch_related_signal", RANK_TOOL_NAME}:
            return None
        if self.context.repository is None or self.context.explicit_filters:
            return None
        required_drilldowns = self.context.discovery_policy.required_drilldowns
        if not required_drilldowns:
            return None
        present = _passed_evidence_aliases(
            self.context.repository.get_evidences(self.context.run_id),
            run_id=self.context.run_id,
        )
        missing = [
            f"E2_{dimension}"
            for dimension in required_drilldowns
            if not _has_alias_family(present, f"E2_{dimension}")
        ]
        if not missing:
            return None
        dimensions = ", ".join(required_drilldowns)
        return (
            f"analysis policy requires drilldown_dimension for {dimensions} before {tool_name}; "
            f"missing drilldown evidence: {missing}"
        )

    def _repair_action_error(self, tool_name: str) -> str | None:
        required = self.context.required_repair_action
        if required is None or tool_name == required:
            return None
        return f"reflection repair requires {required}; do not call {tool_name} during this repair turn"

    def _top_drilldown_candidate_element(self, dimension: str) -> str | None:
        if self.context.repository is None:
            return None
        row = self.context.repository.get_evidence(
            run_id=self.context.run_id,
            evidence_id=f"{self.context.run_id}:E2_{dimension}",
        )
        summary = row.get("result_summary") if isinstance(row, dict) else None
        candidates = summary.get("candidates") if isinstance(summary, dict) else None
        if not isinstance(candidates, list) or not candidates:
            return None
        first = candidates[0]
        if not isinstance(first, dict) or first.get("element") is None:
            return None
        return str(first["element"])

    def _evidence_id_prefix_error(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name not in {"drilldown_dimension", "fetch_related_signal", "calculate_contribution"}:
            return None
        evidence_ids = args.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            return None
        prefix = f"{self.context.run_id}:"
        bad_ids = [str(evidence_id) for evidence_id in evidence_ids if not str(evidence_id).startswith(prefix)]
        if not bad_ids:
            return None
        return (
            f"evidence_ids must start with current run prefix {prefix}; "
            f"copy exact evidence_ids from prior tool output; invalid ids: {bad_ids}"
        )

    def _evidence_flow_error(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name != "fetch_related_signal" or self.context.repository is None:
            return None
        run_id = self.context.run_id
        if self.context.repository.get_evidence(run_id=run_id, evidence_id=f"{run_id}:E4") is not None:
            return None
        rows = self.context.repository.get_evidences(run_id)
        existing_e3_id = _first_alias_family_evidence_id(
            rows,
            run_id=run_id,
            alias="E3",
        )
        if existing_e3_id is None:
            return None
        contribution_ids = _contribution_chain_for_e3(rows, run_id=run_id, e3_id=existing_e3_id)
        return (
            f"{existing_e3_id} already exists for this run; do not fetch additional related signals before E4. "
            f"Call calculate_contribution next with evidence_ids {contribution_ids} for the selected E3 element, "
            "then call rank_root_causes."
        )

    def _reject(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        code: str,
        message: str,
        args: dict[str, Any],
        started_at: float,
        mark_failed: bool = True,
    ) -> ToolMessage:
        if mark_failed:
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

    def _reject_schema_invalid(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        message: str,
        args: dict[str, Any],
        started_at: float,
    ) -> ToolMessage:
        if self.context.last_schema_invalid_tool == tool_name:
            self.context.consecutive_schema_invalid_count += 1
        else:
            self.context.last_schema_invalid_tool = tool_name
            self.context.consecutive_schema_invalid_count = 1
        return self._reject(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            code="ACTION_SCHEMA_INVALID",
            message=message,
            args=args,
            started_at=started_at,
            mark_failed=self.context.consecutive_schema_invalid_count >= 2,
        )

    def _reset_schema_invalid_streak(self) -> None:
        self.context.last_schema_invalid_tool = None
        self.context.consecutive_schema_invalid_count = 0

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
    if not isinstance(raw, Mapping):
        raise _InvalidToolArgsError("tool args must be a JSON object")
    return dict(raw)


def _has_placeholder_evidence_ids(args: dict[str, Any]) -> bool:
    evidence_ids = args.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return False
    return any(":" not in str(evidence_id) for evidence_id in evidence_ids)


def _string_filters(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _iso_text(raw: Any) -> str:
    if hasattr(raw, "isoformat"):
        return str(raw.isoformat())
    return str(raw)


class _InvalidToolArgsError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


def _payload_observation_ok(payload: dict[str, Any]) -> bool | None:
    observation = payload.get("observation")
    if isinstance(observation, dict) and isinstance(observation.get("ok"), bool):
        return observation["ok"]
    if isinstance(payload.get("ok"), bool):
        return payload["ok"]
    return None


def _payload_evidence_ids(payload: dict[str, Any]) -> list[str]:
    evidence_ids = payload.get("evidence_ids")
    if isinstance(evidence_ids, list):
        return [str(item) for item in evidence_ids]
    observation = payload.get("observation")
    if isinstance(observation, dict) and isinstance(observation.get("evidence_ids"), list):
        return [str(item) for item in observation["evidence_ids"]]
    return []


def _first_alias_family_evidence_id(rows: list[dict[str, Any]], *, run_id: str, alias: str) -> str | None:
    prefix = f"{run_id}:"
    for row in rows:
        evidence_id = str(row.get("evidence_id") or "")
        if not evidence_id.startswith(prefix) or row.get("guard_status") != "passed":
            continue
        evidence_alias = evidence_id.removeprefix(prefix)
        if evidence_alias == alias or evidence_alias.startswith(f"{alias}_"):
            return evidence_id
    return None


def _evidence_id_has_alias_family(evidence_id: str, *, run_id: str, alias: str) -> bool:
    prefix = f"{run_id}:"
    if not evidence_id.startswith(prefix):
        return False
    evidence_alias = evidence_id.removeprefix(prefix)
    return evidence_alias == alias or evidence_alias.startswith(f"{alias}_")


def _contribution_chain_for_e3(rows: list[dict[str, Any]], *, run_id: str, e3_id: str) -> list[str]:
    chain: list[str] = []
    e1_id = _first_alias_family_evidence_id(rows, run_id=run_id, alias="E1")
    if e1_id is not None:
        chain.append(e1_id)
    e2_alias = e2_alias_for_e3_id(e3_id, run_id=run_id)
    if e2_alias is not None:
        e2_id = _first_alias_family_evidence_id(rows, run_id=run_id, alias=e2_alias)
        if e2_id is not None:
            chain.append(e2_id)
    else:
        e2_id = _first_alias_family_evidence_id(rows, run_id=run_id, alias="E2")
        if e2_id is not None:
            chain.append(e2_id)
    chain.append(e3_id)
    return chain


def _passed_evidence_aliases(rows: list[dict[str, Any]], *, run_id: str) -> set[str]:
    prefix = f"{run_id}:"
    return {
        str(row.get("evidence_id") or "").removeprefix(prefix)
        for row in rows
        if str(row.get("evidence_id") or "").startswith(prefix)
        and row.get("guard_status") == "passed"
    }


def _has_alias_family(aliases: set[str], required: str) -> bool:
    return any(alias == required or alias.startswith(f"{required}_") for alias in aliases)


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

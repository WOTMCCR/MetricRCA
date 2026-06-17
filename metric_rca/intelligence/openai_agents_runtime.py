"""OpenAI Agents SDK adapter for the provider-neutral runtime boundary."""

from __future__ import annotations

import json
from typing import Any

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.exceptions import AgentsException, ModelBehaviorError
from agents.models.openai_provider import OpenAIProvider
from openai import AsyncOpenAI, OpenAIError
from pydantic import TypeAdapter, ValidationError

from metric_rca.intelligence.agent_runtime import (
    AgentRuntimeConfig,
    AgentRuntimeConfigError,
    AgentRuntimeError,
    StructuredOutputT,
)


class OpenAIAgentsRuntime:
    """Structured-output adapter backed by the OpenAI Agents SDK."""

    def __init__(self, config: AgentRuntimeConfig, *, runner: type[Runner] = Runner) -> None:
        if config.structured_output_method not in {"json_schema", "json_mode"}:
            raise AgentRuntimeConfigError(
                "LLM_STRUCTURED_OUTPUT_UNSUPPORTED",
                "Agents SDK adapter supports json_schema and json_mode",
            )
        self._config = config
        self._runner = runner
        self._run_config = _build_run_config(config)

    def run_structured(
        self,
        *,
        name: str,
        instructions: str,
        user_input: str,
        output_type: type[StructuredOutputT],
        max_turns: int = 1,
    ) -> StructuredOutputT:
        agent_output_type: type[Any] = output_type
        agent_instructions = instructions
        if self._config.structured_output_method == "json_mode":
            agent_output_type = str
            agent_instructions = _instructions_with_json_schema(
                instructions=instructions,
                output_type=output_type,
            )

        agent = Agent(
            name=name,
            instructions=agent_instructions,
            output_type=agent_output_type,
        )
        try:
            result = self._runner.run_sync(
                agent,
                user_input,
                max_turns=max_turns,
                run_config=self._run_config,
            )
        except ModelBehaviorError as exc:
            raise AgentRuntimeError("MODEL_BEHAVIOR_ERROR", "agent returned invalid structured output") from exc
        except OpenAIError as exc:
            raise AgentRuntimeError("LLM_REQUIRED_UNAVAILABLE", "agent request failed") from exc
        except AgentsException as exc:
            raise AgentRuntimeError("LLM_REQUIRED_UNAVAILABLE", "agent runtime request failed") from exc
        if self._config.structured_output_method == "json_mode":
            return _validate_json_text(result.final_output, output_type)
        return result.final_output


def _build_run_config(config: AgentRuntimeConfig) -> RunConfig:
    client_kwargs: dict[str, Any] = {
        "api_key": config.api_key,
        "base_url": config.base_url,
    }
    if config.timeout is not None:
        client_kwargs["timeout"] = config.timeout
    if config.max_retries is not None:
        client_kwargs["max_retries"] = config.max_retries
    provider_config = OpenAIProvider(
        openai_client=AsyncOpenAI(**client_kwargs),
        use_responses=config.provider == "openai",
    )
    model_settings = ModelSettings(
        temperature=config.temperature,
        max_tokens=config.max_completion_tokens,
        parallel_tool_calls=config.parallel_tool_calls,
        extra_body=_json_mode_extra_body(config),
    )
    return RunConfig(
        model=config.model,
        model_provider=provider_config,
        model_settings=model_settings,
        tracing_disabled=not config.agent_tracing_enabled,
        trace_include_sensitive_data=False,
        workflow_name="MetricRCA",
        group_id=config.agent_trace_group_id,
        trace_metadata={
            "provider": str(config.provider),
            "model": str(config.model),
            "component": "intent_planner",
        },
    )


def _json_mode_extra_body(config: AgentRuntimeConfig) -> dict[str, object] | None:
    if config.structured_output_method != "json_mode":
        return None
    return {"response_format": {"type": "json_object"}}


def _instructions_with_json_schema(
    *,
    instructions: str,
    output_type: type[StructuredOutputT],
) -> str:
    schema = TypeAdapter(output_type).json_schema()
    schema_json = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    return (
        f"{instructions}\n\n"
        f"OUTPUT JSON SCHEMA:\n{schema_json}\n\n"
        "Return only one JSON object matching OUTPUT JSON SCHEMA. "
        "Do not wrap the JSON in markdown, prose, or an outer envelope."
    )


def _validate_json_text(
    payload: object,
    output_type: type[StructuredOutputT],
) -> StructuredOutputT:
    if not isinstance(payload, str):
        raise AgentRuntimeError("MODEL_BEHAVIOR_ERROR", "json_mode agent returned non-text output")
    try:
        return TypeAdapter(output_type).validate_json(payload)
    except ValidationError as exc:
        raise AgentRuntimeError("MODEL_BEHAVIOR_ERROR", "agent returned invalid structured output") from exc

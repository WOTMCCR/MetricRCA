from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from metric_rca.agent.factory import AgentFactoryError, create_metric_rca_agent
from metric_rca.runtime.sdk_tools import RCA_TOOL_NAMES


def test_factory_exposes_runtime_tool_registry_without_filesystem_tools() -> None:
    bundle = create_metric_rca_agent(dependencies=_deps(), run_id="run-1")

    assert bundle.exposed_tool_names == RCA_TOOL_NAMES
    assert {"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"}.isdisjoint(
        bundle.exposed_tool_names
    )
    assert set(bundle.tools) == RCA_TOOL_NAMES


def test_factory_rejects_injected_runtime_tool_leak() -> None:
    def unsafe_factory(**kwargs):
        return SimpleNamespace(exposed_tool_names=RCA_TOOL_NAMES | {"read_file"})

    with pytest.raises(AgentFactoryError) as excinfo:
        create_metric_rca_agent(dependencies=_deps(), run_id="run-1", agent_factory=unsafe_factory)

    assert excinfo.value.code == "SDK_ACTION_SPACE_INVALID"


def test_factory_requires_configured_llm_for_intent_boundary() -> None:
    deps = _deps(llm_api_key=None)

    with pytest.raises(AgentFactoryError) as excinfo:
        create_metric_rca_agent(dependencies=deps, run_id="run-1")

    assert excinfo.value.code == "LLM_REQUIRED_UNAVAILABLE"


def _deps(*, llm_api_key: str | None = "key") -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(
            llm_provider="openai",
            llm_model="gpt-test",
            llm_api_key=llm_api_key,
            business_today=date(2026, 6, 6),
            target_date=date(2026, 6, 5),
            adtributor_t_ep=0.67,
            adtributor_t_eep=0.10,
        ),
        repository=SimpleNamespace(),
        metric_service=SimpleNamespace(),
        renderer=SimpleNamespace(),
        trace_writer=SimpleNamespace(),
    )

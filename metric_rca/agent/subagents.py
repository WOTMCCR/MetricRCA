"""P9 subagent gate.

P6 keeps multi-agent disabled. Enabling it before P9 is a typed scope error so
the system does not silently run an unreviewed multi-agent path.
"""

from __future__ import annotations

from typing import Any


class SubagentScopeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def build_subagents(*, settings: Any, tools: list[Any], middleware: list[Any]) -> list[dict[str, Any]]:
    if getattr(settings, "multi_agent_enabled", False):
        raise SubagentScopeError("MULTI_AGENT_P9_SCOPE", "multi_agent_enabled is P9 scope")
    return []

"""Aggregations over persisted trace observability rows."""

from __future__ import annotations

from typing import Any


TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


def build_token_summary(trace_steps: list[dict[str, Any]]) -> dict[str, Any]:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    latency_ms = 0
    by_step: list[dict[str, Any]] = []

    for row in trace_steps:
        usage = _token_usage(row)
        row_latency = _int_or_zero(row.get("latency_ms"))
        latency_ms += row_latency
        if usage:
            prompt = _int_or_zero(usage.get("prompt_tokens"))
            completion = _int_or_zero(usage.get("completion_tokens"))
            total = _int_or_zero(usage.get("total_tokens")) or prompt + completion
            prompt_tokens += prompt
            completion_tokens += completion
            total_tokens += total
        by_step.append(
            {
                "seq": row.get("seq"),
                "node": row.get("node"),
                "action": row.get("action"),
                "latency_ms": row_latency,
                "token_usage": usage,
            }
        )

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
        "by_step": by_step,
    }


def _token_usage(row: dict[str, Any]) -> dict[str, Any] | None:
    usage = row.get("token_usage")
    if isinstance(usage, dict):
        return {key: usage.get(key) for key in TOKEN_FIELDS if usage.get(key) is not None}
    output_summary = row.get("output_summary")
    if isinstance(output_summary, dict) and isinstance(output_summary.get("token_usage"), dict):
        nested = output_summary["token_usage"]
        return {key: nested.get(key) for key in TOKEN_FIELDS if nested.get(key) is not None}
    return None


def _int_or_zero(value: Any) -> int:
    if value is None:
        return 0
    return int(value)

"""Configured OpenAI-compatible chat model construction."""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI


OPENAI_COMPATIBLE_PROVIDERS = frozenset({"openai", "openai-compatible", "deepseek"})


class LLMClientConfigError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def build_openai_compatible_chat_model(
    *,
    provider: str | None,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    temperature: float | None = None,
    timeout: float | int | None = None,
    max_retries: int | None = None,
    max_completion_tokens: int | None = None,
    model_kwargs: dict[str, Any] | None = None,
) -> ChatOpenAI:
    if provider not in OPENAI_COMPATIBLE_PROVIDERS:
        raise LLMClientConfigError("LLM_PROVIDER_UNSUPPORTED", "provider must be openai-compatible")
    if not model or not api_key:
        raise LLMClientConfigError("LLM_REQUIRED_UNAVAILABLE", "model and API key are required")
    if provider != "openai" and not base_url:
        raise LLMClientConfigError("LLM_BASE_URL_REQUIRED", "OpenAI-compatible providers require llm_base_url")

    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": max_retries,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_completion_tokens is not None:
        kwargs["max_completion_tokens"] = max_completion_tokens
    if model_kwargs is not None:
        kwargs["model_kwargs"] = model_kwargs
    if base_url is not None:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)

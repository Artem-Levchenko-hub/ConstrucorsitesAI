"""Single dispatch point for configured OpenAI-compatible LLM upstreams.

Gemini stays on llmgw.ru; MAX Studio's Sonnet 5 route uses AITunnel. This is a
thin façade over `providers/llmgw.py` plus the native `/v1/messages` adapter.

R-01 (deep module): callers (`routers/chat.py`, `services/streaming.py`,
`routers/messages_native.py`) see `acompletion` / `slug_to_omnia` /
`native_messages_route` and never touch a provider directly.
"""

from __future__ import annotations

from typing import Any

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.errors import ModelNotFoundError
from omnia_gateway.providers import llmgw
from omnia_gateway.services.pricing import PRICE_TABLE


def is_supported(model_id: str) -> bool:
    return model_id in PRICE_TABLE


def slug_to_omnia(slug: str) -> str | None:
    """Map an upstream model slug back to its Omnia ID. llmgw answers with
    vendor-prefixed slugs; an Omnia id
    passed through unchanged also maps to itself. None otherwise so the caller
    keeps req.model."""
    if not slug:
        return None
    if llmgw.is_llmgw_model(slug):
        return slug
    return llmgw.slug_to_omnia(slug)


def native_messages_route(model: str) -> tuple[str, str] | None:
    """(api_key, api_base) for the native-agent `/v1/messages` adapter."""
    settings = get_settings()
    if model == "claude-sonnet-5":
        if not settings.aitunnel_api_key:
            return None
        return settings.aitunnel_api_key.get_secret_value(), settings.aitunnel_base_url
    if not settings.llmgw_api_key:
        return None
    return settings.llmgw_api_key.get_secret_value(), settings.llmgw_base_url


async def acompletion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    user: str | None = None,  # noqa: ARG001 — kept for a stable caller signature
    temperature: float | None = None,
    max_tokens: int | None = None,
    **extra: Any,  # noqa: ARG001 — future OpenAI params
) -> dict[str, Any]:
    """Non-streaming completion in OpenAI chat-completion shape. `usage` always
    carries `prompt_tokens` / `completion_tokens` (callers price off these)."""
    if not llmgw.is_llmgw_model(model):
        raise ModelNotFoundError(f"Unknown model: {model}")
    return await llmgw.acompletion(
        model=model,
        messages=messages,
        temperature=0.5 if temperature is None else temperature,
        max_tokens=8192 if max_tokens is None else max_tokens,
    )

"""GET /v1/models — model catalog with RUB prices and per-key availability."""

from __future__ import annotations

from fastapi import APIRouter

from omnia_gateway.core.config import get_settings
from omnia_gateway.services.pricing import list_models

router = APIRouter(prefix="/v1", tags=["models"])


def _has(secret) -> bool:
    """SecretStr is not None AND has non-empty value (env vars often arrive empty)."""
    return secret is not None and bool(secret.get_secret_value())


# Single dispatch — no scattered conditionals when a new provider lands.
_PROVIDER_KEY_PRESENT = {
    "anthropic": lambda s: _has(s.anthropic_api_key),
    "openai": lambda s: _has(s.openai_api_key),
    "yandex": lambda s: _has(s.yandex_api_key) and bool(s.yandex_folder_id),
    "alibaba": lambda s: _has(s.openrouter_api_key),  # via OpenRouter
    "sber": lambda s: _has(s.gigachat_auth_key),
    "google": lambda s: _has(s.gemini_api_key),  # Gemini via Google AI Studio
}

# Models whose key lives outside the default per-provider mapping — e.g. an
# Anthropic-branded or OpenAI-branded model served via a 3rd-party proxy.
# Mirrors _PROXY_ROUTES in services/litellm_router.py; keep these in sync.
_MODEL_KEY_OVERRIDE = {
    "claude-haiku-4-5": lambda s: _has(s.proxyapi_api_key),
    # Sonnet 4.6 routed through proxyapi too — shares the same balance with
    # Haiku and the GPT-5 family.
    "claude-sonnet-4-6": lambda s: _has(s.proxyapi_api_key),
    # GPT-5 family lives on the same proxyapi balance as Haiku — both check
    # the same key here. If proxyapi credit is empty, ALL three flip to
    # `available: false` simultaneously, which is what users should see.
    "gpt-5": lambda s: _has(s.proxyapi_api_key),
    "gpt-5-nano": lambda s: _has(s.proxyapi_api_key),
    # Opus 4.7 + DeepSeek (V3/R1) share the same proxyapi balance as Haiku.
    "claude-opus-4-7": lambda s: _has(s.proxyapi_api_key),
    # Opus 4.8 served via the vsegpt provider — same key as the DeepSeek workers.
    "claude-opus-4-8": lambda s: _has(s.vsegpt_api_key),
    # Orchestrator + developer served via the vsegpt provider (same key).
    "gemini-3.5-flash-high": lambda s: _has(s.vsegpt_api_key),
    "minimax-m2.7": lambda s: _has(s.vsegpt_api_key),
    "deepseek-v4-pro-thinking": lambda s: _has(s.vsegpt_api_key),
    "deepseek-v4-pro": lambda s: _has(s.vsegpt_api_key),
    "gemini-3-flash-vision": lambda s: _has(s.vsegpt_api_key),
    "deepseek-chat": lambda s: _has(s.vsegpt_api_key),  # served via vsegpt provider now
    "deepseek-reasoner": lambda s: _has(s.proxyapi_api_key),
    # DeepSeek V4 Flash (Thinking) via vsegpt.ru — separate key/balance from
    # proxyapi; served by the direct vsegpt provider, not LiteLLM.
    "deepseek-v4-flash-thinking": lambda s: _has(s.vsegpt_api_key),
}


def _is_available(model_id: str, provider: str) -> bool:
    check = _MODEL_KEY_OVERRIDE.get(model_id) or _PROVIDER_KEY_PRESENT.get(provider)
    return bool(check and check(get_settings()))


@router.get("/models")
async def list_models_endpoint() -> dict:
    data: list[dict] = []
    for m in list_models():
        entry = dict(m)
        entry["available"] = _is_available(entry["id"], entry["provider"])
        data.append(entry)
    return {"object": "list", "data": data}

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
    # GPT-5 family lives on the same proxyapi balance as Haiku — both check
    # the same key here. If proxyapi credit is empty, ALL three flip to
    # `available: false` simultaneously, which is what users should see.
    "gpt-5": lambda s: _has(s.proxyapi_api_key),
    "gpt-5-nano": lambda s: _has(s.proxyapi_api_key),
}


def _is_available(model_id: str, provider: str) -> bool:
    check = _MODEL_KEY_OVERRIDE.get(model_id) or _PROVIDER_KEY_PRESENT.get(provider)
    return bool(check and check(get_settings()))


@router.get("/models")
async def list_models_endpoint() -> dict:
    settings = get_settings()
    data: list[dict] = []
    for m in list_models():
        # Master kill-switch for Sber: known TLS hang under long-lived uvicorn
        # (commits 6983db9, 7f9647a). Skip entirely until the upstream issue is
        # resolved — the UI won't see the model and can't accidentally pick it.
        if m["provider"] == "sber" and not settings.gigachat_enabled:
            continue
        entry = dict(m)
        entry["available"] = _is_available(entry["id"], entry["provider"])
        data.append(entry)
    return {"object": "list", "data": data}

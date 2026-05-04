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
}


def _is_available(provider: str) -> bool:
    check = _PROVIDER_KEY_PRESENT.get(provider)
    return bool(check and check(get_settings()))


@router.get("/models")
async def list_models_endpoint() -> dict:
    data: list[dict] = []
    for m in list_models():
        entry = dict(m)
        entry["available"] = _is_available(entry["provider"])
        data.append(entry)
    return {"object": "list", "data": data}

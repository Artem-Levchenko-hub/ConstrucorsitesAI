"""GET /v1/models — model catalog with RUB prices and per-key availability."""

from __future__ import annotations

from fastapi import APIRouter

from omnia_gateway.core.config import get_settings
from omnia_gateway.services.pricing import list_models

router = APIRouter(prefix="/v1", tags=["models"])

# Single dispatch — no scattered conditionals when a new provider lands.
_PROVIDER_KEY_PRESENT = {
    "anthropic": lambda s: s.anthropic_api_key is not None,
    "openai": lambda s: s.openai_api_key is not None,
    "yandex": lambda s: s.yandex_api_key is not None and s.yandex_folder_id is not None,
    "alibaba": lambda s: s.openrouter_api_key is not None,  # via OpenRouter
    "sber": lambda s: s.gigachat_auth_key is not None,
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

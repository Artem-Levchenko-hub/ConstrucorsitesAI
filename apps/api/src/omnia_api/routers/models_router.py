from __future__ import annotations

import json

import httpx
from fastapi import APIRouter

from omnia_api.core.config import get_settings
from omnia_api.core.redis import get_redis
from omnia_api.schemas.model import ModelInfo

router = APIRouter(prefix="/api", tags=["models"])

CACHE_KEY = "llm:models"
CACHE_TTL_SEC = 60

_MOCK_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="claude-sonnet-4-6",
        display_name="Claude Sonnet 4.6",
        provider="anthropic",
        price_rub_per_1k_in=0.3,
        price_rub_per_1k_out=1.5,
        context_window=200_000,
        recommended_for=["quality"],
    ),
    ModelInfo(
        id="gpt-4.1",
        display_name="GPT-4.1",
        provider="openai",
        price_rub_per_1k_in=0.5,
        price_rub_per_1k_out=2.0,
        context_window=128_000,
        recommended_for=["quality", "fast"],
    ),
    ModelInfo(
        id="yandexgpt-5",
        display_name="YandexGPT 5",
        provider="yandex",
        price_rub_per_1k_in=0.2,
        price_rub_per_1k_out=0.8,
        context_window=32_000,
        recommended_for=["budget"],
    ),
    ModelInfo(
        id="qwen-3-coder",
        display_name="Qwen 3 Coder",
        provider="alibaba",
        price_rub_per_1k_in=0.15,
        price_rub_per_1k_out=0.6,
        context_window=128_000,
        recommended_for=["budget", "fast"],
    ),
]


@router.get("/models", response_model=list[ModelInfo])
async def list_models() -> list[ModelInfo]:
    settings = get_settings()
    if settings.mock_llm:
        return _MOCK_MODELS

    cached = await get_redis().get(CACHE_KEY)
    if cached:
        try:
            return [ModelInfo.model_validate(m) for m in json.loads(cached)]
        except (json.JSONDecodeError, ValueError):
            pass

    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{settings.llm_gateway_url.rstrip('/')}/v1/models")
        resp.raise_for_status()
        data = resp.json().get("data", [])
    models = [ModelInfo.model_validate(m) for m in data]
    await get_redis().setex(
        CACHE_KEY, CACHE_TTL_SEC, json.dumps([m.model_dump() for m in models])
    )
    return models

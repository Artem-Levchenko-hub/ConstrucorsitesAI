"""YandexGPT provider — wraps the Foundation Models API into an OpenAI-shaped
chat-completion dict.

Why a custom wrapper instead of LiteLLM-native: LiteLLM's Yandex coverage has
historically been thin and version-fragile. A small httpx wrapper is cheaper to
maintain for MVP and easy to delete once LiteLLM fully supports Yandex.

R-01 (deep module): the public surface is a single async function;
authentication, payload shaping, and error translation live entirely inside.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import httpx

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.errors import UpstreamProviderError, ValidationFailedError
from omnia_gateway.core.http import get_http

YANDEX_COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# Map Omnia model IDs to Yandex model URI suffixes (folder ID is prefixed at runtime).
_YANDEX_MODEL_SUFFIX: dict[str, str] = {
    "yandexgpt-5": "yandexgpt/latest",
}


def is_yandex_model(model_id: str) -> bool:
    return model_id in _YANDEX_MODEL_SUFFIX


def _to_yandex_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    role_map = {"system": "system", "user": "user", "assistant": "assistant"}
    out: list[dict[str, str]] = []
    for m in messages:
        role = role_map.get(m["role"])
        if role is None:
            raise ValidationFailedError(f"unsupported role: {m['role']}")
        out.append({"role": role, "text": m["content"]})
    return out


def _approx_tokens(text: str) -> int:
    """~4 chars/token — coarse fallback when Yandex omits usage."""
    return max(1, len(text) // 4)


async def acompletion(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.6,
    max_tokens: int = 2000,
    timeout: float = 60.0,  # noqa: ASYNC109 — passed to httpx.AsyncClient, which enforces it
) -> dict[str, Any]:
    """Call YandexGPT, return an OpenAI-compatible response dict.

    Raises:
        ValidationFailedError on bad input (unknown model / role).
        UpstreamProviderError on transport, 4xx/5xx, or empty response.
    """
    settings = get_settings()
    if not settings.yandex_api_key or not settings.yandex_folder_id:
        raise UpstreamProviderError("YANDEX_API_KEY / YANDEX_FOLDER_ID not configured")

    suffix = _YANDEX_MODEL_SUFFIX.get(model)
    if suffix is None:
        raise ValidationFailedError(f"unsupported Yandex model: {model}")
    model_uri = f"gpt://{settings.yandex_folder_id}/{suffix}"

    payload = {
        "modelUri": model_uri,
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": str(max_tokens),
        },
        "messages": _to_yandex_messages(messages),
    }
    headers = {
        "Authorization": f"Api-Key {settings.yandex_api_key.get_secret_value()}",
        "x-folder-id": settings.yandex_folder_id,
    }

    try:
        client = get_http()
        resp = await client.post(
            YANDEX_COMPLETION_URL, json=payload, headers=headers, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise UpstreamProviderError(
            f"YandexGPT HTTP {exc.response.status_code}",
            details={"body": exc.response.text[:500]},
        ) from exc
    except httpx.HTTPError as exc:
        raise UpstreamProviderError(f"YandexGPT transport error: {exc}") from exc

    result = data.get("result") or {}
    alternatives = result.get("alternatives") or []
    if not alternatives:
        raise UpstreamProviderError("YandexGPT returned no alternatives")
    content = (alternatives[0].get("message") or {}).get("text", "")

    usage = result.get("usage") or {}
    tokens_in = int(
        usage.get("inputTextTokens") or _approx_tokens("".join(m["content"] for m in messages))
    )
    tokens_out = int(usage.get("completionTokens") or _approx_tokens(content))

    return {
        "id": f"yandex-{uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    }

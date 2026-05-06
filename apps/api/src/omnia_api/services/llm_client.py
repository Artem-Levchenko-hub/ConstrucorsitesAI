"""Async клиент к LLM Gateway (OpenAI-compatible SSE) с MOCK-режимом."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from omnia_api.core.config import get_settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


async def stream_chat_completion(
    messages: list[dict[str, str]],
    model: str,
    user_id: str,
    project_id: str,
    message_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """Yields events:
    - {"delta": "..."} — текстовый чанк
    - {"usage": {"tokens_in", "tokens_out", "cost_rub"}} — финальная статистика
    - {"error": "..."} — ошибка (после неё — конец)
    """
    settings = get_settings()
    if settings.mock_llm:
        async for event in _mock_stream(messages):
            yield event
        return

    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "user": user_id,
        "metadata": {"project_id": project_id, "message_id": message_id},
    }
    timeout = httpx.Timeout(120.0, connect=5.0, read=120.0)
    log.info("llm.stream.start url=%s model=%s msgs=%d", url, model, len(messages))
    line_count = 0
    delta_count = 0
    usage_seen = False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload) as resp:
                log.info("llm.stream.connected status=%s", resp.status_code)
                if resp.status_code >= 400:
                    body = await resp.aread()
                    log.error("llm.stream.http_error status=%s body=%s", resp.status_code, body[:300])
                    yield {"error": f"gateway {resp.status_code}: {body!r}"}
                    return
                async for line in resp.aiter_lines():
                    line_count += 1
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError as exc:
                        log.warning("llm.stream.json_decode_error chunk=%r err=%s", chunk[:100], exc)
                        continue
                    delta = (
                        data.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content")
                    )
                    if delta:
                        delta_count += 1
                        yield {"delta": delta}
                    usage = data.get("usage")
                    if usage:
                        usage_seen = True
                        # gateway puts cost_rub in metadata, not usage; pull from
                        # either location for forward compat.
                        meta = data.get("metadata") or {}
                        cost_rub = (
                            float(meta.get("cost_rub", 0))
                            if meta.get("cost_rub")
                            else float(usage.get("cost_rub", 0))
                        )
                        yield {
                            "usage": {
                                "tokens_in": usage.get("prompt_tokens", 0),
                                "tokens_out": usage.get("completion_tokens", 0),
                                "cost_rub": cost_rub,
                            }
                        }
    except httpx.HTTPError as e:
        log.exception("llm.stream.transport_error err=%s", e)
        yield {"error": f"http: {e}"}
    log.info(
        "llm.stream.done lines=%d deltas=%d usage_seen=%s",
        line_count, delta_count, usage_seen,
    )


async def _mock_stream(messages: list[dict[str, str]]) -> AsyncIterator[dict[str, Any]]:
    user_text = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"),
        "пустой запрос",
    )[:200]
    chunks = [
        f"Готово. Сгенерировал сайт по запросу: «{user_text}»\n\n",
        '<file path="index.html">',
        "\n<!DOCTYPE html><html lang=\"ru\"><head>",
        "<meta charset=\"utf-8\"><title>Omnia mock</title>",
        '\n<script src="https://cdn.tailwindcss.com"></script>',
        "\n</head><body class=\"bg-slate-50 p-12 font-sans\">",
        '\n  <h1 class="text-4xl font-bold mb-4">Сайт по запросу</h1>',
        f'\n  <p class="text-slate-700 max-w-2xl">{user_text}</p>',
        "\n</body></html>\n",
        "</file>\n",
    ]
    for c in chunks:
        await asyncio.sleep(0.04)
        yield {"delta": c}
    yield {"usage": {"tokens_in": 200, "tokens_out": 350, "cost_rub": 0.5}}

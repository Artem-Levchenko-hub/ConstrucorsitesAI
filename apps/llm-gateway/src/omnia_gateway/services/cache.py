"""Response cache for non-streaming chat completions.

R-04 single source of truth: this module owns the cache-key shape so all
callers see identical hit/miss behavior. Key intentionally excludes user_id —
two users asking the same question hit the same cached answer (cost saver per
brief).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.redis import get_redis

log = structlog.get_logger(__name__)

_KEY_PREFIX = "llm:cache:"


def make_cache_key(model: str, messages: list[dict[str, str]]) -> str:
    """sha256 over (model + system + last user). Excludes user_id."""
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"),
        "",
    )
    payload = json.dumps({"m": model, "s": system, "u": last_user}, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}{digest}"


async def get(key: str) -> dict[str, Any] | None:
    raw = await get_redis().get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("cache.corrupt_entry", key=key)
        return None


async def set(key: str, value: dict[str, Any]) -> None:
    ttl = get_settings().cache_ttl_seconds
    await get_redis().set(key, json.dumps(value, ensure_ascii=False), ex=ttl)

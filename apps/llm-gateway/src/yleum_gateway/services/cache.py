"""Response cache for non-streaming chat completions.

R-04 single source of truth: this module owns the cache-key shape so all
callers see identical hit/miss behavior. Responses are scoped to the complete
sanitized conversation, owner, project, and generation parameters.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

import structlog

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.redis import get_redis

log = structlog.get_logger(__name__)

_KEY_PREFIX = "llm:cache:v2:"


def make_cache_key(
    model: str,
    messages: list[dict[str, Any]],
    *,
    user_id: str | None = None,
    project_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Hash the effective request; the v2 prefix cannot read older shared entries."""
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "user_id": user_id,
            "project_id": project_id,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}{digest}"


async def get(key: str) -> dict[str, Any] | None:
    raw = await get_redis().get(key)
    if raw is None:
        return None
    try:
        return cast(dict[str, Any], json.loads(raw))
    except json.JSONDecodeError:
        log.warning("cache.corrupt_entry", key=key)
        return None


async def set(key: str, value: dict[str, Any]) -> None:
    ttl = get_settings().cache_ttl_seconds
    await get_redis().set(key, json.dumps(value, ensure_ascii=False), ex=ttl)

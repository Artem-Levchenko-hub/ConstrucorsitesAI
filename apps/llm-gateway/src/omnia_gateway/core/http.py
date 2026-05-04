"""Shared async httpx client — one TCP/TLS pool reused across all upstream calls.

Why: creating a new client per request burns a TCP+TLS handshake on every Yandex
call (R-31). One process-wide client amortizes that cost.
"""

from __future__ import annotations

import httpx

from omnia_gateway.core.config import get_settings

_client: httpx.AsyncClient | None = None


async def init_http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=get_settings().request_timeout_seconds,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _client


async def close_http() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_http() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialized — call init_http() in lifespan")
    return _client

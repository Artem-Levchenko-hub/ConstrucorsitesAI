"""Rate-limit dependency — per-key throttle on the costly generate/edit path.

Exercises `rate_limit_prompt` directly (no FastAPI app): it raises ApiError(429)
once a key exceeds the window, keys anonymous traffic by client IP, and no-ops
when disabled.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Request

from omnia_api.core import ratelimit
from omnia_api.core.errors import ApiError


def _request(ip: str, *, xff: bool = True) -> Request:
    headers = [(b"x-forwarded-for", ip.encode())] if xff else []
    return Request(
        {"type": "http", "headers": headers, "client": (ip, 5000), "scheme": "http"}
    )


def _settings(limit: str = "2/minute", enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        rate_limit_enabled=enabled,
        prompt_rate_limit=limit,
        jwt_cookie_name="omnia_session",
    )


async def test_rate_limit_blocks_after_window(monkeypatch) -> None:
    monkeypatch.setattr(ratelimit, "get_settings", lambda: _settings("2/minute"))
    req = _request("203.0.113.50")
    await ratelimit.rate_limit_prompt(req)  # 1 — ok
    await ratelimit.rate_limit_prompt(req)  # 2 — ok
    with pytest.raises(ApiError) as ei:
        await ratelimit.rate_limit_prompt(req)  # 3 — over the limit
    assert ei.value.status_code == 429
    assert ei.value.code == "rate_limited"


async def test_rate_limit_disabled_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(ratelimit, "get_settings", lambda: _settings("1/minute", enabled=False))
    req = _request("203.0.113.51")
    for _ in range(5):
        await ratelimit.rate_limit_prompt(req)  # never raises while disabled


def test_client_ip_prefers_first_forwarded_hop() -> None:
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.7, 10.0.0.1")],
        "client": ("127.0.0.1", 12345),
    }
    assert ratelimit._client_ip(Request(scope)) == "203.0.113.7"


def test_client_ip_falls_back_to_socket_peer() -> None:
    scope = {"type": "http", "headers": [], "client": ("198.51.100.4", 5000)}
    assert ratelimit._client_ip(Request(scope)) == "198.51.100.4"

"""Rate-limit wiring — the limited endpoint returns 429 past the per-key limit.

Exercises the shared ``limiter`` end-to-end on a throwaway app (no auth/DB needed)
so a slowapi misconfiguration (missing request arg, bad handler) is caught.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from slowapi.extension import _rate_limit_exceeded_handler

from omnia_api.core.ratelimit import _client_ip, limiter


def test_limiter_blocks_past_limit() -> None:
    limiter.reset()
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    @app.get("/_rl_probe")
    @limiter.limit("2/minute")
    async def probe(request: Request) -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/_rl_probe").status_code == 200
    assert client.get("/_rl_probe").status_code == 200
    assert client.get("/_rl_probe").status_code == 429
    limiter.reset()


def test_client_ip_prefers_first_forwarded_hop() -> None:
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.7, 10.0.0.1")],
        "client": ("127.0.0.1", 12345),
    }
    assert _client_ip(Request(scope)) == "203.0.113.7"


def test_client_ip_falls_back_to_socket_peer() -> None:
    scope = {"type": "http", "headers": [], "client": ("198.51.100.4", 5000)}
    assert _client_ip(Request(scope)) == "198.51.100.4"

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from yleum_api.services.generation import provider_health as client
from yleum_api.services.generation import provider_health_auth as auth

SECRET = "synthetic-health-signing-secret-32-bytes"


@pytest.mark.parametrize(
    "edge",
    [
        "valid",
        "forged",
        "nonce",
        "run",
        "expired",
        "duplicate",
        "redirect",
        "error",
        "cleanup_expiry",
    ],
)
async def test_free_health_client_accepts_only_current_bound_signed_read(monkeypatch, edge):
    import time
    from datetime import UTC, datetime

    now = int(time.time())
    clock = [now]
    monkeypatch.setattr(
        client,
        "get_settings",
        lambda: SimpleNamespace(
            llm_gateway_url="http://unit", generation_response_signing_secret=SecretStr(SECRET)
        ),
    )
    monkeypatch.setattr(auth, "_secret", lambda _: SECRET)
    requests = []
    original = httpx.AsyncClient

    async def handle(request):
        requests.append(request)
        assert request.method == "GET"
        identity, nonce = auth.verify_health_request(request.headers[auth.HEADER], secret=SECRET)
        if edge == "nonce":
            nonce = str(UUID(int=99))
        if edge == "run":
            identity = dict(identity, run_id=str(UUID(int=99)))
        measured = now - 16 if edge == "expired" else now
        snapshot = auth.unknown_snapshot("a" * 64, "unsupported", now=measured)
        assertion = auth.sign_health_response(snapshot, identity, nonce, secret=SECRET)
        if edge == "forged":
            assertion += "x"
        body = json.dumps({"assertion": assertion})
        if edge == "duplicate":
            body = '{"assertion":"forged",' + body[1:]
        return httpx.Response(
            302 if edge == "redirect" else 503 if edge == "error" else 200, text=body
        )

    class Client(original):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            if edge == "cleanup_expiry":
                clock[0] += 16

    monkeypatch.setattr(
        client.httpx,
        "AsyncClient",
        lambda **kwargs: Client(transport=httpx.MockTransport(handle), **kwargs),
    )
    verify = client.verify_health_response
    monkeypatch.setattr(
        client,
        "verify_health_response",
        lambda *args: verify(*args, now=datetime.fromtimestamp(clock[0], UTC)),
    )
    result = await client.free_health_for_run(
        run_id=UUID(int=1), project_id=UUID(int=2), user_id=UUID(int=3), dependency="funds"
    )
    assert len(requests) == 1
    assert result.reason == ("unsupported" if edge == "valid" else "unavailable")
    assert result.available_for("funds") is False


def test_health_does_not_expand_existing_response_purposes():
    from yleum_api.services.generation.response_auth import verify_generation_response_token

    token = auth.mint_health_request(
        {
            "run_id": str(UUID(int=1)),
            "project_id": str(UUID(int=2)),
            "user_id": str(UUID(int=3)),
            "dependency": "funds",
        },
        str(UUID(int=4)),
        secret=SECRET,
    )
    for purpose in (
        "generation_response.read",
        "generation_response.record",
        "generation_response.reconcile",
    ):
        with pytest.raises(ValueError):
            verify_generation_response_token(token, purpose=purpose, secret=SECRET)


def test_actual_gateway_and_api_health_auth_are_wire_compatible(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "llm-gateway/src"))
    from yleum_gateway.core import provider_health_auth as gateway

    identity = {
        "run_id": str(UUID(int=1)),
        "project_id": str(UUID(int=2)),
        "user_id": str(UUID(int=3)),
        "dependency": "funds",
    }
    nonce = str(UUID(int=4))
    request = auth.mint_health_request(identity, nonce, secret=SECRET)
    assert gateway.verify_health_request(request, secret=SECRET) == (identity, nonce)
    response = gateway.sign_health_response(
        gateway.unknown_snapshot("a" * 64, "unsupported"), identity, nonce, secret=SECRET
    )
    assert (
        auth.verify_health_response(response, identity, nonce, secret=SECRET).reason
        == "unsupported"
    )


async def test_absent_signing_secret_is_unknown_without_http(monkeypatch):
    calls = []

    def denied(*args, **kwargs):
        calls.append(True)
        raise AssertionError("HTTP must not be constructed")

    monkeypatch.setattr(client.httpx, "AsyncClient", denied)
    monkeypatch.setattr(
        client, "get_settings", lambda: SimpleNamespace(llm_gateway_url="http://unit")
    )
    monkeypatch.setattr(
        auth, "_secret", lambda _: (_ for _ in ()).throw(ValueError("secret unavailable"))
    )
    result = await client.free_health_for_run(
        run_id=UUID(int=1), project_id=UUID(int=2), user_id=UUID(int=3), dependency="funds"
    )
    assert result.reason == "unavailable" and result.credential_scope is None
    assert calls == []

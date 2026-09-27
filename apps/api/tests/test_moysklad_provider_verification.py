from __future__ import annotations

import httpx
import pytest

from yleum_api.services import integration_providers


@pytest.mark.asyncio
async def test_moysklad_verification_uses_supported_accept_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("accept") != "application/json;charset=utf-8":
            return httpx.Response(400, json={"errors": [{"error": "Unsupported Accept"}]})
        return httpx.Response(200, json={"name": "Тестовый склад"})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        integration_providers.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )

    label = await integration_providers.verify_provider(
        "moysklad", {}, {"token": "synthetic-token"}
    )

    assert label == "Тестовый склад"
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer synthetic-token"

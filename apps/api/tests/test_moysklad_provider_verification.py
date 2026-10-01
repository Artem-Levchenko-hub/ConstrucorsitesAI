from __future__ import annotations

import httpx
import pytest

from yleum_api.services import integration_providers


@pytest.mark.parametrize("credentials", [
    {}, {"token": ""}, {"token": " \t\n "}, {"token": None}, {"token": 42},
    {"token": True}, {"token": []}, {"token": {"invalid": "value"}},
])
async def test_missing_or_invalid_moysklad_token_requires_reconnection_without_http(
    monkeypatch, credentials,
):
    def no_http(**kwargs):
        pytest.fail("Missing credentials must be rejected before creating an HTTP client")

    monkeypatch.setattr(integration_providers.httpx, "AsyncClient", no_http)
    with pytest.raises(integration_providers.IntegrationCredentialsInvalid) as caught:
        await integration_providers.verify_provider("moysklad", {}, credentials)
    assert "Подключите МойСклад заново" in str(caught.value)
    assert "неизвестном формате" not in str(caught.value)


async def test_verify_route_reports_cleared_moysklad_credentials_as_422(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from yleum_api.core.errors import ApiError
    from yleum_api.routers import app_integrations

    connection = SimpleNamespace(public_config={}, status="disconnected", last_error=None)
    binding = SimpleNamespace(status="ready", last_error=None)
    session = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(app_integrations, "_owned_max_project", AsyncMock())
    monkeypatch.setattr(app_integrations, "require_max_studio_access", lambda _: None)
    monkeypatch.setattr(app_integrations, "_account_connection", AsyncMock(return_value=connection))
    monkeypatch.setattr(app_integrations, "_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(app_integrations, "load_credentials", AsyncMock(return_value={}))

    def no_http(**kwargs):
        pytest.fail("Disconnected verification must not contact MoySklad")

    monkeypatch.setattr(integration_providers.httpx, "AsyncClient", no_http)
    with pytest.raises(ApiError) as caught:
        await app_integrations.verify_integration(
            uuid4(), "moysklad", session, SimpleNamespace(id=uuid4()),
        )
    assert caught.value.status_code == 422
    assert caught.value.code == "integration_credentials_invalid"
    assert "Подключите МойСклад заново" in caught.value.message
    assert connection.status == binding.status == "error"
    assert connection.last_error == binding.last_error == caught.value.message
    session.commit.assert_awaited_once()


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

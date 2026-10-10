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
        body = {"name": "Тестовый склад"} if request.url.path.endswith(
            "/context/companysettings"
        ) else {"rows": []}
        return httpx.Response(200, json=body)

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
    assert len(seen) == 5
    assert all(request.method == "GET" for request in seen)
    assert all(request.headers["authorization"] == "Bearer synthetic-token" for request in seen)
    assert all(request.headers["accept-encoding"] == "gzip" for request in seen)
    assert all(request.url.params["limit"] == "1" for request in seen[1:])


@pytest.mark.parametrize("denied_path", [
    "/entity/product", "/entity/organization", "/entity/store", "/report/stock/all",
])
async def test_moysklad_read_permission_denial_prevents_verified_connection(
    monkeypatch, denied_path,
):
    original_client = httpx.AsyncClient

    def respond(request):
        if request.url.path.endswith(denied_path):
            return httpx.Response(403, json={"errors": [{"error": "private provider detail"}]})
        return httpx.Response(200, json={"name": "Synthetic", "rows": []})

    monkeypatch.setattr(integration_providers.httpx, "AsyncClient", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(integration_providers.IntegrationCredentialsInvalid) as caught:
        await integration_providers.verify_provider("moysklad", {}, {"token": "synthetic-token"})
    assert "private provider detail" not in str(caught.value)


@pytest.mark.parametrize("body", [{}, {"rows": None}, {"rows": "not a list"}])
async def test_moysklad_malformed_read_response_does_not_verify_access(monkeypatch, body):
    original_client = httpx.AsyncClient

    def respond(request):
        payload = body if request.url.path.endswith("/entity/product") else {
            "name": "Synthetic", "rows": [],
        }
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(integration_providers.httpx, "AsyncClient", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(integration_providers.IntegrationProviderError):
        await integration_providers.verify_provider("moysklad", {}, {"token": "synthetic-token"})


async def test_moysklad_preflight_has_one_deadline_across_all_reads(monkeypatch):
    import asyncio

    original_client = httpx.AsyncClient
    seen = []

    async def respond(request):
        seen.append(request)
        await asyncio.sleep(0.012)
        return httpx.Response(200, json={"name": "Synthetic", "rows": []})

    monkeypatch.setattr(integration_providers, "MOYSKLAD_PREFLIGHT_TIMEOUT_SECONDS", 0.02,
                        raising=False)
    monkeypatch.setattr(integration_providers.httpx, "AsyncClient", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(integration_providers.IntegrationProviderUnavailable):
        await integration_providers.verify_provider("moysklad", {}, {"token": "synthetic-token"})
    assert len(seen) < 5


async def test_moysklad_rate_limit_does_not_verify_connection(monkeypatch):
    original_client = httpx.AsyncClient

    def respond(request):
        if request.url.path.endswith("/report/stock/all"):
            return httpx.Response(429, json={"errors": [{"error": "private detail"}]})
        return httpx.Response(200, json={"name": "Synthetic", "rows": []})

    monkeypatch.setattr(integration_providers.httpx, "AsyncClient", lambda **kwargs:
                        original_client(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(integration_providers.IntegrationProviderUnavailable) as caught:
        await integration_providers.verify_provider("moysklad", {}, {"token": "synthetic-token"})
    assert "private detail" not in str(caught.value)

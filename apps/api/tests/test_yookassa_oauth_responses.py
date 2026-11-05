"""Validate real YooKassa exchange parsing against synthetic provider HTTP responses."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest

from yleum_api.services import integration_oauth
from yleum_api.services.integration_providers import (
    IntegrationCredentialsInvalid,
    IntegrationProviderError,
)

_CLIENT = httpx.AsyncClient
_TOKEN = {"access_token": "synthetic-access", "expires_in": 157680000}


@pytest.fixture
def provider_http(monkeypatch):
    monkeypatch.setattr(
        integration_oauth, "credentials",
        lambda _: integration_oauth.OAuthCredentials("synthetic-client", "synthetic-secret"),
    )
    monkeypatch.setattr(
        integration_oauth, "callback_url",
        lambda _: "https://yleum.ru/api/integrations/oauth/yookassa/callback",
    )

    def install(handler):
        return patch.object(httpx, "AsyncClient", side_effect=lambda **kwargs: _CLIENT(
            transport=httpx.MockTransport(handler), **kwargs,
        ))

    return install


@pytest.mark.parametrize("token", [
    [], {}, "synthetic-provider-body",
    {**_TOKEN, "access_token": None}, {**_TOKEN, "access_token": {}},
    {**_TOKEN, "access_token": 123}, {**_TOKEN, "access_token": "  "},
    {**_TOKEN, "access_token": "synthetic\r\nheader"},
    {**_TOKEN, "access_token": "synthetic\x00token"},
    {"access_token": "synthetic-access"},
    *[{**_TOKEN, "expires_in": value} for value in (
        None, True, False, 0, -1, 1.0, 1.5, float("inf"), float("nan"),
        "NaN", "Infinity", "1.5", "-1", 10**100,
    )],
    {**_TOKEN, "token_type": "basic"}, {**_TOKEN, "token_type": 123},
])
async def test_malformed_yookassa_token_is_safe_provider_error(provider_http, token):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(
            200, json=token if request.method == "POST" else {"account_id": "123"},
        )

    with provider_http(provider), pytest.raises(IntegrationProviderError) as caught:
        await integration_oauth.exchange_code("yookassa", "synthetic-code")
    assert "synthetic" not in str(caught.value)
    assert len(calls) == 1, "Malformed grants must never be sent to the merchant profile API"


@pytest.mark.parametrize("profile", [
    [], {}, None, {"shop_id": "123"}, {"account_id": None}, {"account_id": 123},
    {"account_id": {}}, {"account_id": "  "},
])
async def test_yookassa_requires_verified_nonempty_account_id(provider_http, profile):
    with provider_http(lambda request: httpx.Response(
        200, json=_TOKEN if request.method == "POST" else profile,
    )), pytest.raises(IntegrationProviderError) as caught:
        await integration_oauth.exchange_code("yookassa", "synthetic-code")
    assert "synthetic" not in str(caught.value)


@pytest.mark.parametrize("expires", [157680000, "157680000"])
@pytest.mark.parametrize("token_type", [None, "Bearer", "bearer"])
async def test_verified_yookassa_grant_preserves_shop_and_expiry(
    provider_http, expires, token_type,
):
    before = datetime.now(UTC)
    token = {**_TOKEN, "expires_in": expires, "refresh_token": "unsupported-synthetic-refresh"}
    if token_type is not None:
        token["token_type"] = token_type
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(
            200, json=token if request.method == "POST" else {"account_id": "123"},
        )

    with provider_http(provider):
        result = await integration_oauth.exchange_code("yookassa", "synthetic-code")
    assert result.public_config == {"shop_id": "123"}
    assert result.secret_values == {"access_token": "synthetic-access"}
    assert result.account_label == "Магазин 123"
    assert before + timedelta(seconds=int(expires) - 60) <= result.expires_at
    assert result.expires_at <= datetime.now(UTC) + timedelta(seconds=int(expires))
    assert calls[1].headers["authorization"] == "Bearer synthetic-access"


@pytest.mark.parametrize(("fields", "mode"), [
    ({"test": True}, "test"),
    ({"test": False}, "live"),
    ({}, None),
    ({"test": "false", "shop_mode": "live"}, None),
    ({"test": 0}, None),
    ({"test": None}, None),
])
async def test_yookassa_oauth_mode_comes_only_from_boolean_me_test(provider_http, fields, mode):
    calls = []

    def provider(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json=(
            {**_TOKEN, "test": True} if request.method == "POST"
            else {"account_id": "123", **fields}
        ))

    with provider_http(provider):
        result = await integration_oauth.exchange_code("yookassa", "synthetic-code")
    expected = {"shop_id": "123"}
    if mode is not None:
        expected["shop_mode"] = mode
    assert result.public_config == expected
    assert calls == [("POST", "/oauth/v2/token"), ("GET", "/v3/me")]


async def test_yookassa_expiry_requires_reauthorization_without_refresh_http(provider_http):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={**_TOKEN, "refresh_token": "synthetic-refresh"})

    with provider_http(provider), pytest.raises(IntegrationCredentialsInvalid):
        await integration_oauth.refresh_access_token(
            "yookassa", {"shop_id": "123"},
            {"access_token": "synthetic-old", "refresh_token": "synthetic-refresh"},
        )
    assert calls == []


@pytest.mark.parametrize(("provider", "method", "access_field"), [
    ("yandex_metrica", "POST", "oauth_token"), ("bitrix24", "GET", "access_token"),
])
async def test_other_providers_keep_their_existing_refresh_protocol(
    provider_http, provider, method, access_field,
):
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(200, json={
            "access_token": "synthetic-new", "refresh_token": "synthetic-rotated",
            "expires_in": 3600,
        })

    with provider_http(response):
        result, expires_at = await integration_oauth.refresh_access_token(
            provider, {}, {"refresh_token": "synthetic-old", "preserved": "synthetic-value"},
        )
    assert result == {
        "refresh_token": "synthetic-rotated", "preserved": "synthetic-value",
        access_field: "synthetic-new",
    }
    assert expires_at > datetime.now(UTC)
    assert len(calls) == 1 and calls[0].method == method

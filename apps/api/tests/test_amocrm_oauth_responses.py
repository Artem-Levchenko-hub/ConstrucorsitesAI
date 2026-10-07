"""Initial amoCRM grants must be usable before a connection is marked active."""

from unittest.mock import patch

import httpx
import pytest

from yleum_api.services import integration_oauth
from yleum_api.services.integration_providers import IntegrationProviderError

_CLIENT = httpx.AsyncClient
_TOKEN = {
    "access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "expires_in": 3600,
}


@pytest.mark.parametrize(
    ("token", "profile"),
    [
        ([], {"name": "Synthetic"}),
        (_TOKEN, []),
        ({**_TOKEN, "access_token": {}}, {"name": "Synthetic"}),
        ({**_TOKEN, "refresh_token": ""}, {"name": "Synthetic"}),
        ({"access_token": "synthetic-access", "expires_in": 3600}, {"name": "Synthetic"}),
    ],
)
async def test_invalid_initial_amo_grant_is_rejected_safely(token, profile, monkeypatch):
    def provider(request):
        return httpx.Response(200, json=token if request.method == "POST" else profile)

    monkeypatch.setattr(
        integration_oauth, "credentials",
        lambda _: integration_oauth.OAuthCredentials("synthetic-client", "synthetic-secret"),
    )
    monkeypatch.setattr(integration_oauth, "callback_url", lambda _: "https://example.test/callback")
    with patch.object(httpx, "AsyncClient", side_effect=lambda **kwargs: _CLIENT(
        transport=httpx.MockTransport(provider), **kwargs,
    )):
        with pytest.raises(IntegrationProviderError):
            await integration_oauth.exchange_code(
                "amocrm", "synthetic-code", referer="synthetic.amocrm.ru",
            )


async def test_initial_amo_grant_preserves_both_tokens_and_expiry(monkeypatch):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(
            200, json=_TOKEN if request.method == "POST" else {"name": "Synthetic"},
        )

    monkeypatch.setattr(
        integration_oauth, "credentials",
        lambda _: integration_oauth.OAuthCredentials("synthetic-client", "synthetic-secret"),
    )
    monkeypatch.setattr(integration_oauth, "callback_url", lambda _: "https://example.test/callback")
    with patch.object(httpx, "AsyncClient", side_effect=lambda **kwargs: _CLIENT(
        transport=httpx.MockTransport(provider), **kwargs,
    )):
        result = await integration_oauth.exchange_code(
            "amocrm", "synthetic-code", referer="synthetic.amocrm.ru",
        )
    assert result.secret_values == {
        "access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
    }
    assert result.expires_at is not None
    assert result.public_config == {"base_url": "https://synthetic.amocrm.ru"}
    assert calls[1].headers["authorization"] == "Bearer synthetic-access"

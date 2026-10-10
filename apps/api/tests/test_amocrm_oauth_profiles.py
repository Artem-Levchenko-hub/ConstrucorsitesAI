"""Pinned OAuth clients must survive changes to the new-connection default."""

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from yleum_api.services import integration_oauth
from yleum_api.services.integration_providers import IntegrationProviderError

_CLIENT = httpx.AsyncClient


@pytest.fixture
def profiles(monkeypatch):
    settings = SimpleNamespace(**{
        f"integration_{provider}_{field}": None
        for provider in ("yookassa", "yandex", "bitrix24")
        for field in ("client_id", "client_secret")
    })
    values = {
        "integration_amocrm_client_id": "synthetic-legacy",
        "integration_amocrm_client_secret": SecretStr("synthetic-legacy-secret"),
        "integration_amocrm_legacy_redirect_uri": "https://legacy.example.test/callback",
        "integration_amocrm_public_client_id": "synthetic-public",
        "integration_amocrm_public_client_secret": SecretStr("synthetic-public-secret"),
        "integration_amocrm_public_redirect_uri": "https://public.example.test/callback",
        "integration_amocrm_public_oauth_enabled": True,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setattr(integration_oauth, "get_settings", lambda: settings)
    return settings


@pytest.mark.parametrize("client_key,client", [
    (None, "legacy"), ("amocrm-legacy-v1", "legacy"), ("amocrm-public-v1", "public"),
])
async def test_refresh_uses_pinned_pair_and_redirect_even_after_default_switch(
    profiles, monkeypatch, client_key, client,
):
    profiles.integration_amocrm_public_oauth_enabled = client != "public"
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _CLIENT(
        **kwargs, transport=httpx.MockTransport(provider),
    ))
    result, _ = await integration_oauth.refresh_access_token(
        "amocrm", {"base_url": "https://synthetic.amocrm.ru"},
        {"refresh_token": "old"}, client_key=client_key,
    )
    assert result["refresh_token"] == "rotated"
    assert requests == [{
        "client_id": f"synthetic-{client}", "client_secret": f"synthetic-{client}-secret",
        "grant_type": "refresh_token", "refresh_token": "old",
        "redirect_uri": f"https://{client}.example.test/callback",
    }]


def test_new_start_default_is_explicit_and_profile_has_no_secret_repr(profiles):
    key = integration_oauth.new_authorization_client_key("amocrm")
    assert key == "amocrm-public-v1"
    url = integration_oauth.authorization_url("amocrm", "synthetic-state", client_key=key)
    assert parse_qs(urlparse(url).query)["client_id"] == ["synthetic-public"]
    profile = integration_oauth.resolve_client_profile("amocrm", key)
    assert "synthetic-public-secret" not in repr(profile)
    profiles.integration_amocrm_public_oauth_enabled = False
    assert integration_oauth.new_authorization_client_key("amocrm") == "amocrm-legacy-v1"


@pytest.mark.parametrize("failure", ["unknown", "missing"])
async def test_invalid_profile_fails_before_http(profiles, monkeypatch, failure):
    if failure == "missing":
        profiles.integration_amocrm_public_client_secret = None
    key = "unknown-profile" if failure == "unknown" else "amocrm-public-v1"

    def forbidden(**kwargs):
        pytest.fail("invalid profile initiated provider HTTP")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    with pytest.raises(IntegrationProviderError):
        await integration_oauth.refresh_access_token(
            "amocrm", {"base_url": "https://synthetic.amocrm.ru"},
            {"refresh_token": "old"}, client_key=key,
        )



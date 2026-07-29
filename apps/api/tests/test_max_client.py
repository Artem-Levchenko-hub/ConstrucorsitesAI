from __future__ import annotations

import ssl
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from omnia_api.services import max_client


def test_max_ca_is_the_official_russian_trusted_root() -> None:
    certificate = x509.load_pem_x509_certificate(max_client._MAX_CA_FILE.read_bytes())

    assert certificate.subject == certificate.issuer
    assert (
        certificate.fingerprint(hashes.SHA256()).hex()
        == "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"
    )
    assert isinstance(max_client._max_ssl_context(), ssl.SSLContext)


@pytest.mark.asyncio
async def test_tls_verification_failure_has_specific_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate",
            request=request,
        )

    monkeypatch.setattr(
        max_client,
        "_http_client",
        lambda: httpx.AsyncClient(
            base_url=max_client.BASE_URL,
            transport=httpx.MockTransport(handler),
        ),
    )

    with pytest.raises(
        max_client.MaxTlsConfigurationError,
        match="TLS-сертификат MAX API не прошёл проверку доверия",
    ):
        await max_client.get_me("secret")


@pytest.mark.asyncio
async def test_get_me_normalizes_official_user_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "user_id": 42,
            "first_name": "Omnia",
            "last_name": "Bot",
            "username": "@omnia_bot",
        }

    monkeypatch.setattr(max_client, "_request", fake_request)
    bot = await max_client.get_me("secret")
    assert bot.id == "42"
    assert bot.name == "Omnia Bot"
    assert bot.username == "omnia_bot"


@pytest.mark.asyncio
async def test_subscribe_uses_secret_and_required_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_request(*args: Any, **kwargs: Any) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(max_client, "_request", fake_request)
    await max_client.subscribe("secret", "https://app.example/api/max/webhook", "hook-secret")
    assert captured["args"][:3] == ("POST", "/subscriptions", "secret")
    assert captured["kwargs"]["json"] == {
        "url": "https://app.example/api/max/webhook",
        "secret": "hook-secret",
        "update_types": ["message_created", "message_callback", "bot_started"],
    }


@pytest.mark.asyncio
async def test_subscription_lookup_accepts_wrapped_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"subscriptions": [{"url": "https://app.example/api/max/webhook"}]}

    monkeypatch.setattr(max_client, "_request", fake_request)
    assert (
        await max_client.has_subscription("secret", "https://app.example/api/max/webhook") is True
    )

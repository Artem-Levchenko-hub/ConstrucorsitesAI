from types import SimpleNamespace

import httpx
import pytest

from yleum_api.services import yookassa


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_definitive_rejection_is_distinct_from_an_uncertain_network_result(
    monkeypatch, status
):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"type": "error"}))
    client_class = httpx.AsyncClient
    monkeypatch.setattr(yookassa, "_credentials", lambda: ("test-shop", "test-key"))
    monkeypatch.setattr(
        yookassa,
        "get_settings",
        lambda: SimpleNamespace(
            yookassa_api_url="https://provider.example.test/v3",
        ),
    )
    monkeypatch.setattr(
        yookassa.httpx,
        "AsyncClient",
        lambda **kwargs: client_class(
            **kwargs,
            transport=transport,
        ),
    )
    with pytest.raises(yookassa.YooKassaUnavailable) as error:
        await yookassa._request("POST", "/payments", idempotency_key="one-order", json={})
    assert getattr(error.value, "definitive", False) is True


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
async def test_ambiguous_provider_failure_never_claims_the_operation_did_not_happen(
    monkeypatch, status
):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"type": "error"}))
    client_class = httpx.AsyncClient
    monkeypatch.setattr(yookassa, "_credentials", lambda: ("test-shop", "test-key"))
    monkeypatch.setattr(
        yookassa,
        "get_settings",
        lambda: SimpleNamespace(
            yookassa_api_url="https://provider.example.test/v3",
        ),
    )
    monkeypatch.setattr(
        yookassa.httpx,
        "AsyncClient",
        lambda **kwargs: client_class(
            **kwargs,
            transport=transport,
        ),
    )
    with pytest.raises(yookassa.YooKassaUnavailable) as error:
        await yookassa._request("POST", "/payments", idempotency_key="one-order", json={})
    assert getattr(error.value, "definitive", False) is False

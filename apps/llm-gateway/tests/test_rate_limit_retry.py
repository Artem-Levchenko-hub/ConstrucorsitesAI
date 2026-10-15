"""Real HTTP transport and settlement gates for closed rate-limit retries."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.core.errors import BillingReconciliationRequiredError
from yleum_gateway.main import create_app
from yleum_gateway.routers import messages_native
from yleum_gateway.services import provider_calls

URL = "https://api.llmgw.ru/v1/chat/completions"
BODY = {"error": {"code": "rate_limit_exceeded", "type": "rate_limit_error", "message": "private"}}


def install_transport(monkeypatch, reply):
    for transport in ("HTTPTransport", "AsyncHTTPTransport"):
        monkeypatch.setattr(httpx, transport, lambda *a, **kw: httpx.MockTransport(reply))


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects, monkeypatch):
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic")
    monkeypatch.setenv("LLMGW_BASE_URL", "https://api.llmgw.ru/v1")
    reset_settings_cache()
    monkeypatch.setattr(
        messages_native, "native_messages_route", lambda: ("synthetic", "https://api.llmgw.ru/v1")
    )
    with TestClient(create_app()) as value:
        yield value


def send(client, native):
    return client.post(
        "/v1/messages" if native else "/v1/chat/completions",
        json={
            "model": "claude-sonnet-5.5",
            "messages": [{"role": "user", "content": "synthetic"}],
            "stream": False,
        },
    )


def error(response, native):
    return response.json()["error"] if native else response.json()["detail"]["error"]


@pytest.mark.parametrize("native", [False, True])
def test_retry_after_durable_failed_receipt(client, monkeypatch, native):
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(429, json=BODY, headers={"retry-after": "17"})

    install_transport(monkeypatch, reply)
    response = send(client, native)
    assert len(calls) == 1 and response.status_code == (429 if native else 502)
    envelope = error(response, native)
    assert envelope["type" if native else "code"] == (
        "api_error" if native else "model_unavailable"
    )
    details = envelope["details"]
    assert details["retry_safe"] is True
    assert (
        details["retry_policy"] == "llmgw_key_rate_limit_v1"
        and details["retry_after_seconds"] == 17
    )
    assert details["provider_charge_ambiguous"] is False
    provider_calls.finish_call.assert_awaited_once()
    receipt = provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == "failed"
    assert receipt["provider_cost_rub"] is None and receipt["provider_cost_usd"] is None
    assert receipt["provider_request_id"] is None and "private" not in response.text


@pytest.mark.parametrize("native", [False, True])
def test_failed_journal_never_releases_retry(client, monkeypatch, native):
    install_transport(monkeypatch, lambda request: httpx.Response(429, json=BODY))
    monkeypatch.setattr(
        provider_calls,
        "finish_call",
        AsyncMock(side_effect=BillingReconciliationRequiredError("fixture")),
    )
    response = send(client, native)
    assert response.status_code == 503 and "llmgw_key_rate_limit_v1" not in response.text


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "addition",
    [
        {"usage": {"cost_rub": "2.5"}},
        {"usage": {"completion_tokens": 1}},
        {"usage": {"cost_rub": "NaN"}},
        {"choices": [{"message": {"content": "private"}}]},
        {"metadata": "malformed"},
    ],
)
def test_conflicts_stay_ambiguous(client, monkeypatch, native, addition):
    install_transport(monkeypatch, lambda request: httpx.Response(429, json={**BODY, **addition}))
    response = send(client, native)
    assert error(response, native)["details"]["provider_charge_ambiguous"] is True
    assert "retry_policy" not in response.text
    receipt = provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == "ambiguous"
    if addition == {"usage": {"cost_rub": "2.5"}}:
        assert receipt["provider_cost_rub"] == Decimal("2.5")


def test_pure_policy():
    from yleum_gateway.providers.rate_limit import refusal_retry_candidate

    now = datetime(2026, 10, 12, 12, tzinfo=UTC)

    def classify(body=BODY, headers=None, url=URL, status=429):
        return refusal_retry_candidate(
            httpx.Response(
                status, json=body, headers=headers or {}, request=httpx.Request("POST", url)
            ),
            now=now,
        )

    assert classify()["retry_policy"] == "llmgw_key_rate_limit_v1"
    for header in (
        "17",
        "Mon, 12 Oct 2026 12:00:17 GMT",
        "Monday, 12-Oct-26 12:00:17 GMT",
        "Mon Oct 12 12:00:17 2026",
    ):
        assert classify(headers={"retry-after": header})["retry_after_seconds"] == 17
    for header in (
        "-1",
        "1.5",
        "86401",
        "9" * 100,
        "NaN",
        "tomorrow",
        "Foo, 12 Oct 2026 12:00:17 GMT",
        "Whatever, 12-Oct-26 12:00:17 GMT",
        "Mon, 12 Oct 2026 12:00:17 GMT garbage",
    ):
        assert classify(headers={"retry-after": header}) is None
    for url in (
        "http://api.llmgw.ru/v1/chat/completions",
        URL + "?x=1",
        "https://provider.test/v1/chat/completions",
    ):
        assert classify(url=url) is None
    for body in (
        {"error": {"message": "rate_limit_error"}},
        {"error": {"code": "rate_limit_exceeded", "type": "authentication_error"}},
        {"error": {"code": "rate_limit_exceeded", "cost": 0}},
        {"error": {"code": "unknown"}},
        [],
    ):
        assert classify(body=body) is None
    assert classify(headers={"x-cost-rub": "0"}) is None
    assert classify(status=503) is None


@pytest.mark.parametrize(
    "raw",
    [
        b'{"error":{"code":"rate_limit_exceeded","code":"rate_limit_error"}}',
        b'{"error":{"code":"rate_limit_exceeded"}',
        b'{"error":{"code":"rate_limit_exceeded","message":"' + b"x" * 16384 + b'"}}',
        b"\xff",
    ],
)
def test_invalid_original_bytes_deny_permission(raw):
    from yleum_gateway.providers.rate_limit import refusal_retry_candidate

    response = httpx.Response(429, content=raw, request=httpx.Request("POST", URL))
    assert refusal_retry_candidate(response) is None


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "headers", [{"retry-after": "86401"}, {"retry-after": "broken"}, {"x-cost-rub": "0"}]
)
def test_transport_invalid_evidence_never_releases_retry(client, monkeypatch, native, headers):
    install_transport(monkeypatch, lambda request: httpx.Response(429, json=BODY, headers=headers))
    response = send(client, native)
    assert "retry_policy" not in response.text
    assert error(response, native)["details"]["provider_charge_ambiguous"] is True
    assert provider_calls.finish_call.await_args.kwargs["status"] == "ambiguous"


def test_retry_after_date_boundaries():
    from yleum_gateway.providers.rate_limit import refusal_retry_candidate

    now = datetime(2026, 10, 12, 12, tzinfo=UTC)
    for value, expected in [
        ("Mon, 12 Oct 2026 11:59:00 GMT", 0),
        ("Tue, 13 Oct 2026 12:00:00 GMT", 86400),
        ("Tue, 13 Oct 2026 12:00:01 GMT", None),
    ]:
        result = refusal_retry_candidate(
            httpx.Response(
                429, json=BODY, headers={"retry-after": value}, request=httpx.Request("POST", URL)
            ),
            now=now,
        )
        if expected is None:
            assert result is None
        else:
            assert result["retry_after_seconds"] == expected

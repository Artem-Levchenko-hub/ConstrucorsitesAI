"""Only a committed, specific temporary reservation refusal may wait and retry."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.core.errors import BillingReconciliationRequiredError
from yleum_gateway.main import create_app
from yleum_gateway.providers.inflight_budget import POLICY, URL, retry_candidate
from yleum_gateway.routers import messages_native
from yleum_gateway.services import provider_calls

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def direct():
    return {"error": {"message": "Reservations occupy the available budget.", "code": 402,
                      "metadata": {"reason": "in_flight_budget_exhausted",
                                   "limit_source": "openrouter_in_flight_budget",
                                   "remedy_hint": "Wait for settlement.",
                                   "headers": {"Retry-After": "120"}, "provider_name": None}},
            "user_id": "synthetic-organization"}


def wrapped():
    # Same shape as the actual retained refusal; no real organization or key.
    message = (
        "litellm.APIError: APIError: OpenrouterException - " + json.dumps(direct())
        + "\n\nLiteLLM: model group 'anthropic/claude-sonnet-5.5' failed with the error above. "
        "No fallback was attempted."
    )
    return {"error": {"message": message, "type": "invalid_request_error", "param": None, "code": "402"}}


def classify(body=None, *, raw=None, headers=None, scope="llmgw", url=URL, status=402,
             method="POST", history=None):
    response = httpx.Response(status, json=(direct() if body is None else body) if raw is None else None,
                              content=raw, headers=headers or {}, request=httpx.Request(method, url),
                              history=history or [])
    return retry_candidate(response, provider_scope=scope, now=NOW)


@pytest.mark.parametrize("body", [direct(), wrapped()])
def test_recognizes_exact_direct_and_real_litellm_shape(body):
    assert classify(body) == {
        "upstream_http_status": 402, "provider_scope": "llmgw",
        "provider_refusal_category": "rate_limit", "provider_charge_ambiguous": False,
        "retry_safe": True, "retry_policy": POLICY, "retry_after_seconds": 120,
    }


@pytest.mark.parametrize("headers", [
    {"retry-after": "120"}, {"retry-after": "Wed, 11 Nov 2026 00:02:00 GMT"},
])
def test_matching_outer_delay_is_accepted(headers):
    assert classify(wrapped(), headers=headers)["retry_after_seconds"] == 120


def test_actual_header_can_supply_missing_nested_delay():
    body = direct()
    del body["error"]["metadata"]["headers"]
    assert classify(body, headers={"retry-after": "120"})["retry_after_seconds"] == 120
    assert classify(body) is None


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "86401", "NaN", "9" * 129, "tomorrow", 120, True, None])
def test_invalid_nested_delay_denies_permission(value):
    body = direct()
    body["error"]["metadata"]["headers"]["Retry-After"] = value
    assert classify(body) is None


def test_positive_delay_boundaries_and_expired_dates():
    body = direct()
    for value in ("1", "86400"):
        body["error"]["metadata"]["headers"]["Retry-After"] = value
        assert classify(body)["retry_after_seconds"] == int(value)
    for value in ("Wed, 11 Nov 2026 00:00:00 GMT", "Tue, 10 Nov 2026 23:59:59 GMT"):
        body["error"]["metadata"]["headers"]["Retry-After"] = value
        assert classify(body) is None
    del body["error"]["metadata"]["headers"]
    assert classify(body, headers={"Retry-After": "0"}) is None
    assert classify(body, headers={"Retry-After": "Wed, 11 Nov 2026 00:00:00 GMT"}) is None


@pytest.mark.parametrize("headers", [
    {"retry-after": "119"}, {"retry-after": "121"}, {"retry-after": "-1"},
    {"retry-after": "bad"}, [("Retry-After", "120"), ("Retry-After", "120")],
    {"x-cost-rub": "0"}, {"x-llmgw-cost-rub": "1"}, {"content-type": "text/event-stream"},
])
def test_conflicting_or_monetary_headers_deny_permission(headers):
    assert classify(headers=headers) is None


@pytest.mark.parametrize("changes", [
    {"scope": "aitunnel"}, {"url": "http://api.llmgw.ru/v1/chat/completions"},
    {"url": URL + "?forwarded=1"}, {"url": "https://provider.test/v1/chat/completions"},
    {"method": "GET"}, {"status": 401}, {"status": 429}, {"status": 503},
    {"history": [httpx.Response(307)]},
])
def test_untrusted_transport_never_authorizes(changes):
    assert classify(**changes) is None


@pytest.mark.parametrize("field,value", [
    ("reason", "weight_exceeds_budget"), ("reason", "insufficient_funds"),
    ("limit_source", "openrouter_credits"), ("limit_source", "openrouter_key_limit"),
    ("reason", None), ("limit_source", True), ("provider_name", {"usage": 0}),
])
def test_other_402s_are_not_a_transient_reservation(field, value):
    body = direct()
    body["error"]["metadata"][field] = value
    assert classify(body) is None


@pytest.mark.parametrize("location", ["top", "error", "metadata", "headers"])
def test_receipt_or_unexplained_fields_cannot_hide_in_known_envelope(location):
    body = direct()
    target = {"top": body, "error": body["error"], "metadata": body["error"]["metadata"],
              "headers": body["error"]["metadata"]["headers"]}[location]
    target["usage"] = {"completion_tokens": 1}
    assert classify(body) is None


@pytest.mark.parametrize("raw", [
    b'{"error":{"code":402,"code":"402"}}',
    b'{"error":{"code":402,"metadata":{"reason":"in_flight_budget_exhausted","reason":"x"}}}',
    b'{"error":{"code":402,"metadata":{"reason":NaN}}}',
    b'{"error":{"code":402,"metadata":{"reason":Infinity}}}',
    b'{"error":{"code":402', b"\xff", b" " * 16385,
])
def test_original_bytes_must_be_complete_bounded_unique_and_finite(raw):
    assert classify(raw=raw) is None


def test_nested_original_bytes_have_same_json_guards():
    body = wrapped()
    body["error"]["message"] = body["error"]["message"].replace('"code": 402', '"code": 402, "code": 402')
    assert classify(body) is None
    body = wrapped()
    body["error"]["message"] = body["error"]["message"].replace('"provider_name": null', '"provider_name": NaN')
    assert classify(body) is None


@pytest.mark.parametrize("alter", [
    lambda value: value.replace("OpenrouterException", "DifferentException"),
    lambda value: "try parsing this: " + value,
    lambda value: value + " arbitrary extra text",
    lambda value: value.replace("No fallback was attempted.", "A fallback may have run."),
    lambda value: value.replace("in_flight_budget_exhausted", "insufficient_funds"),
])
def test_arbitrary_message_fragments_do_not_grant_authority(alter):
    body = wrapped()
    body["error"]["message"] = alter(body["error"]["message"])
    assert classify(body) is None


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects, monkeypatch):
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic")
    monkeypatch.setenv("LLMGW_BASE_URL", "https://api.llmgw.ru/v1")
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AITUNNEL_TEXT_API_KEY", "synthetic-backup")
    reset_settings_cache()
    monkeypatch.setattr(messages_native, "native_messages_route", lambda: ("synthetic", "https://api.llmgw.ru/v1"))
    with TestClient(create_app()) as value:
        yield value


def install_transport(monkeypatch, body):
    requests = []
    def reply(request):
        requests.append(request)
        assert str(request.url) == URL, "recognized reservation must never switch to backup"
        return httpx.Response(402, json=body)
    for transport in ("HTTPTransport", "AsyncHTTPTransport"):
        monkeypatch.setattr(httpx, transport, lambda *args, **kwargs: httpx.MockTransport(reply))
    return requests


def send(client, native):
    return client.post("/v1/messages" if native else "/v1/chat/completions", json={
        "model": "claude-sonnet-5.5", "messages": [{"role": "user", "content": "synthetic"}],
        "stream": False,
    })


def details(response, native):
    return (response.json()["error"] if native else response.json()["detail"]["error"])["details"]


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("body", [direct(), wrapped()])
def test_routes_commit_refusal_then_publish_wait_without_fallback(client, monkeypatch, native, body):
    requests = install_transport(monkeypatch, body)
    response = send(client, native)
    assert response.status_code == (402 if native else 502)
    assert len(requests) == 1
    result = details(response, native)
    assert result["retry_policy"] == POLICY and result["retry_after_seconds"] == 120
    assert result["provider_charge_ambiguous"] is False and result["retry_safe"] is True
    assert result["provider_refusal_category"] == "rate_limit"
    assert result["provider_scope"] == "llmgw"
    provider_calls.finish_call.assert_awaited_once()
    receipt = provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == "failed"
    assert receipt["provider_cost_rub"] is None and receipt["provider_cost_usd"] is None
    assert "synthetic-organization" not in response.text and "Reservations occupy" not in response.text


@pytest.mark.parametrize("native", [False, True])
def test_failed_receipt_never_publishes_retry_permission(client, monkeypatch, native):
    requests = install_transport(monkeypatch, wrapped())
    monkeypatch.setattr(provider_calls, "finish_call", AsyncMock(side_effect=BillingReconciliationRequiredError("synthetic")))
    response = send(client, native)
    assert len(requests) == 1 and response.status_code == 503
    assert POLICY not in response.text


@pytest.mark.parametrize("native", [False, True])
def test_ordinary_balance_402_keeps_existing_backup_path(client, monkeypatch, native):
    requests = []
    def reply(request):
        requests.append(request)
        return httpx.Response(402, json={"error": {"code": "insufficient_funds", "message": "synthetic"}})
    for transport in ("HTTPTransport", "AsyncHTTPTransport"):
        monkeypatch.setattr(httpx, transport, lambda *args, **kwargs: httpx.MockTransport(reply))
    response = send(client, native)
    assert len(requests) == 2
    assert str(requests[0].url) == URL
    assert str(requests[1].url) != URL
    assert POLICY not in response.text

"""Sol text-agent transport contracts, without a live provider or database."""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.core.runner_auth import RunnerClaims
from yleum_gateway.main import create_app
from yleum_gateway.providers import llmgw
from yleum_gateway.routers import chat, messages_native
from yleum_gateway.services import model_router, pricing, provider_calls
from yleum_gateway.services.generation_identity import canonical_request
from yleum_gateway.services.output_budget import output_tokens

MODEL = "gpt-6.2-sol"
SLUG = "openai/gpt-6.2-sol"
ACTION = '<omnia:action name="write_file">{"path":"src/app/page.tsx","content":"Привет 🌿"}</omnia:action>'
MESSAGES = [{"role": "system", "content": "Return one tagged JSON action."}, {
    "role": "user", "content": [
        {"type": "text", "text": "Сохрани точный текст и иллюстрацию."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ],
}]
USAGE = {
    "prompt_tokens": 1000, "completion_tokens": 200,
    "prompt_tokens_details": {"cached_tokens": 100},
    "completion_tokens_details": {"reasoning_tokens": 150},
}


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects, monkeypatch):
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic-sol-provider-key")
    reset_settings_cache()
    with TestClient(create_app()) as value:
        yield value


def upstream(monkeypatch, *, finish_reason="stop", reported_rub=None, reported_model=SLUG):
    captured = []

    def reply(request):
        body = json.loads(request.content)
        captured.append(body)
        common = {"id": "synthetic-sol-response", "model": reported_model}
        headers = {"x-llmgw-request-id": "synthetic-sol-provider-receipt"}
        if reported_rub is not None:
            headers["x-llmgw-cost-rub"] = reported_rub
        if body["stream"]:
            chunks = [
                {**common, "choices": [{"delta": {"content": ACTION}}]},
                {**common, "choices": [{"delta": {}, "finish_reason": finish_reason}], "usage": USAGE},
            ]
            content = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            return httpx.Response(200, headers=headers, text=content + "data: [DONE]\n\n")
        return httpx.Response(200, headers=headers, json={
            **common, "choices": [{"message": {"role": "assistant", "content": ACTION},
                                    "finish_reason": finish_reason}], "usage": USAGE,
        })

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    return captured


def response_body(response, stream):
    if not stream:
        return response.json()
    events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    return json.loads(events[-2])


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("limit", ["absent", None, 4096])
@pytest.mark.parametrize("temperature", ["absent", 0.0, 0.9])
def test_actual_chat_wire_uses_sol_policy_and_preserves_owner_multimodal_input(
    client, monkeypatch, stream, limit, temperature,
):
    captured = upstream(monkeypatch)
    body = {"model": MODEL, "messages": MESSAGES, "stream": stream}
    if limit != "absent":
        body["max_tokens"] = limit
    if temperature != "absent":
        body["temperature"] = temperature
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    wire = captured[0]
    assert wire["model"] == SLUG
    assert wire["messages"] == MESSAGES
    assert wire["reasoning_effort"] == "medium"
    assert "temperature" not in wire and "max_tokens" not in wire
    assert "tools" not in wire and "tool_choice" not in wire
    if limit == 4096:
        assert wire["max_completion_tokens"] == 4096
    else:
        assert "max_completion_tokens" not in wire
    if stream:
        assert wire["stream_options"] == {"include_usage": True}
        assert ACTION in "".join(json.loads(line[6:])["choices"][0].get("delta", {}).get("content", "")
                                for line in response.text.splitlines()
                                if line.startswith("data: ") and line[6:] != "[DONE]")
    else:
        assert response.json()["choices"][0]["message"]["content"] == ACTION
    assert response_body(response, stream)["model"] == MODEL


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("reported_rub,expected", [(None, Decimal("0.6471")), ("0", Decimal("0")), ("0.9", Decimal("0.9"))])
def test_reasoning_completion_usage_is_billed_once_with_receipt_precedence(
    client, monkeypatch, stream, reported_rub, expected,
):
    captured = upstream(monkeypatch, reported_rub=reported_rub)
    response = client.post("/v1/chat/completions", json={
        "model": MODEL, "messages": MESSAGES, "stream": stream, "user": str(uuid4()),
    })
    assert response.status_code == 200, response.text
    body = response_body(response, stream)
    assert len(captured) == 1
    assert body["usage"]["completion_tokens"] == 200
    assert body["usage"]["completion_tokens_details"]["reasoning_tokens"] == 150
    assert body["model"] == MODEL
    assert Decimal(body["metadata"]["cost_rub"]) == expected
    chat.billing.charge.assert_awaited_once()
    charge = chat.billing.charge.await_args.kwargs
    assert charge["model_id"] == MODEL and charge["tokens_out"] == 200
    assert charge["cache_read_tokens"] == 100 and charge["cost_rub"] == expected
    assert charge["provider_request_id"] == "synthetic-sol-provider-receipt"
    assert charge["cost_provenance"]["basis"] == ("token_tariff" if reported_rub is None else "provider_reported_rub")


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("requested,reported", [
    (MODEL, "anthropic/claude-sonnet-5.5"),
    ("claude-sonnet-5.5", SLUG),
])
@pytest.mark.parametrize("reported_rub", ["0", "0.9"])
def test_sol_choice_isolation_rejects_model_substitution_but_keeps_known_expense(
    client, monkeypatch, stream, requested, reported, reported_rub,
):
    captured = upstream(monkeypatch, reported_model=reported, reported_rub=reported_rub)
    finish = AsyncMock()
    monkeypatch.setattr(provider_calls, "finish_call", finish)
    response = client.post("/v1/chat/completions", json={
        "model": requested, "messages": MESSAGES, "stream": stream, "user": str(uuid4()),
    })
    assert len(captured) == 1
    if stream:
        events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        assert "[DONE]" not in events
        assert not any(json.loads(event).get("usage") for event in events)
        error = json.loads(events[-1])["error"]
    else:
        assert response.status_code == 503
        body = response.json()
        assert "choices" not in body
        error = body["detail"]["error"]
    assert error["code"] == "billing_reconciliation_required"
    assert error["details"].get("retry_safe") is not True
    chat.billing.charge.assert_not_awaited()
    finish.assert_awaited_once()
    receipt = finish.await_args.kwargs
    assert receipt["status"] == "ambiguous"
    assert receipt["provider_request_id"] == "synthetic-sol-provider-receipt"
    assert receipt["provider_cost_rub"] == Decimal(reported_rub)
    assert receipt.get("calculated_cost_rub") is None


@pytest.mark.parametrize("stream", [False, True])
def test_existing_non_sol_primary_fallback_still_accounts_for_actual_model(client, monkeypatch, stream):
    captured = upstream(monkeypatch, reported_model="anthropic/claude-sonnet-5", reported_rub="0.9")
    response = client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-5.5", "messages": MESSAGES, "stream": stream, "user": str(uuid4()),
    })
    assert response.status_code == 200
    assert len(captured) == 1
    body = response_body(response, stream)
    assert body["model"] == "claude-sonnet-5"
    assert body["metadata"]["fallback_used"] is True
    chat.billing.charge.assert_awaited_once()
    assert chat.billing.charge.await_args.kwargs["model_id"] == "claude-sonnet-5"
    assert chat.billing.charge.await_args.kwargs["cost_rub"] == Decimal("0.9")


@pytest.mark.parametrize("stream", [False, True])
def test_settled_sol_output_limit_reason_is_preserved_without_replaying_post(client, monkeypatch, stream):
    captured = upstream(monkeypatch, finish_reason="length")
    response = client.post("/v1/chat/completions", json={"model": MODEL, "messages": MESSAGES, "stream": stream})
    assert len(captured) == 1
    # The gateway settles the receipt, including SSE [DONE], and preserves the
    # limit reason. The API agent finalizer rejects partial actions on length.
    assert response.status_code == 200
    assert response_body(response, stream)["choices"][0]["finish_reason"] == "length"


@pytest.mark.parametrize("stream", [False, True])
def test_filtered_sol_output_is_not_success_or_a_retry(client, monkeypatch, stream):
    captured = upstream(monkeypatch, finish_reason="content_filter")
    response = client.post("/v1/chat/completions", json={"model": MODEL, "messages": MESSAGES, "stream": stream})
    assert len(captured) == 1
    if stream:
        events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        assert "[DONE]" not in events
        error = json.loads(events[-1])["error"]
    else:
        assert response.status_code == 502
        error = response.json()["detail"]["error"]
    assert error["code"] == "provider_output_filtered" and error["details"]["retry_safe"] is False


@pytest.mark.parametrize("model", [MODEL, SLUG])
@pytest.mark.parametrize("project_cell", [False, True])
def test_native_messages_refuses_sol_before_provider_or_billing(
    client, monkeypatch, model, project_cell,
):
    monkeypatch.setattr(messages_native, "native_messages_route", lambda: pytest.fail("Sol must not reach native transport"))
    start = AsyncMock(side_effect=AssertionError("native Sol dispatch is forbidden"))
    precheck = AsyncMock(side_effect=AssertionError("native Sol precheck is forbidden"))
    monkeypatch.setattr(provider_calls, "start_call", start)
    monkeypatch.setattr(messages_native.billing, "precheck_balance", precheck)
    body = {"model": model, "user": str(uuid4()), "messages": [{"role": "user", "content": "fixture"}]}
    if project_cell:
        claims = RunnerClaims(str(uuid4()), uuid4(), uuid4(), uuid4(), uuid4(), 1, 0, 1, 2)
        monkeypatch.setattr(messages_native, "verify_runner_bearer_header", lambda header: claims)
        monkeypatch.setattr(messages_native, "consume_runner_jti", AsyncMock())
        body["metadata"] = {"message_id": claims.jti, "project_id": str(claims.project_id),
                            "run_id": str(claims.run_id), "session_id": str(claims.session_id),
                            "workspace_id": str(claims.workspace_id), "fencing_epoch": 1, "cancel_epoch": 0}
    response = client.post("/v1/project-cell/messages" if project_cell else "/v1/messages", json=body)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["type"] == "validation_failed"
    assert "/v1/chat/completions" in response.json()["error"]["message"]
    start.assert_not_awaited()
    precheck.assert_not_awaited()


@pytest.mark.parametrize("reported_model", [MODEL, SLUG])
def test_native_claude_request_cannot_adopt_sol_response_but_keeps_its_receipt(
    client, monkeypatch, reported_model,
):
    posts = []
    finish = AsyncMock()
    monkeypatch.setattr(provider_calls, "finish_call", finish)
    monkeypatch.setattr(messages_native, "native_messages_route", lambda: ("synthetic", "https://provider.test/v1"))

    def provider(*args):
        posts.append(args)
        return httpx.Response(200, headers={"x-request-id": "unexpected-sol-receipt", "x-cost-rub": "0.9"}, json={
            "model": reported_model,
            "choices": [{"message": {"content": "fixture"}, "finish_reason": "stop"}],
            "usage": USAGE,
        })

    monkeypatch.setattr(messages_native, "_post_llmgw", provider)
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5.5", "user": str(uuid4()),
        "messages": [{"role": "user", "content": "fixture"}],
    })
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "model_route_mismatch"
    assert len(posts) == 1
    finish.assert_awaited_once()
    receipt = finish.await_args.kwargs
    assert receipt["status"] == "ambiguous"
    assert receipt["provider_request_id"] == "unexpected-sol-receipt"
    assert receipt["provider_cost_rub"] == Decimal("0.9")
    chat.billing.charge.assert_not_awaited()


def test_model_registry_quote_and_real_catalog_use_exact_sol_identity():
    assert llmgw.is_llmgw_model(MODEL) and model_router.is_supported(MODEL)
    assert llmgw.native_slug(MODEL) == SLUG
    assert llmgw.slug_to_omnia(SLUG) == llmgw.slug_to_omnia(MODEL) == MODEL
    assert output_tokens(MODEL, None, fallback=8192) is None
    assert output_tokens(MODEL, 4096, fallback=8192) == 4096
    meta = next(item for item in pricing.list_models() if item["id"] == MODEL)
    assert meta["provider"] == "openai" and meta["context_window"] == 1_050_000
    assert meta["price_rub_per_1k_in"] == 0.33971
    assert meta["price_rub_per_1k_out"] == 1.69853
    assert pricing.calculate_cost_rub(MODEL, 1_000_000, 0) == Decimal("339.71")
    assert pricing.calculate_cost_rub(MODEL, 0, 1_000_000) == Decimal("1698.53")
    assert pricing.calculate_cost_rub(MODEL, 1_000_000, 0, 1_000_000) == Decimal("16.99")
    rates = {"input_text": "339.71", "output_text": "1698.53", "cached_input": "16.99"}
    catalog = {"items": [{"model_name": SLUG, "pricing": {
        "currency": "RUB", "unit": "1M_tokens", "input_rub_per_mtok": rates["input_text"],
        "output_rub_per_mtok": rates["output_text"], "cached_input_rub_per_mtok": rates["cached_input"],
        "rates": [{"billable": key, "unit": "token", "quantity": "1000000", "variant": "",
                   "price_rub": value, "is_from_price": False} for key, value in rates.items()],
    }}]}
    assert pricing._parse_catalog_prices(catalog) == {MODEL: tuple(Decimal(value) for value in rates.values())}


def test_upstream_parameter_translation_does_not_change_ingress_identity_protocol():
    body = {"model": MODEL, "messages": MESSAGES, "stream": False, "max_tokens": 4096, "temperature": 0.0}
    identity = canonical_request("/v1/chat/completions", body)
    assert identity == canonical_request("/v1/chat/completions", dict(body))
    changed = {**body, "model": "claude-sonnet-5.5"}
    assert identity != canonical_request("/v1/chat/completions", changed)
    with pytest.raises(ValueError, match="unsupported generation request fields"):
        canonical_request("/v1/chat/completions", {**body, "max_completion_tokens": 4096})

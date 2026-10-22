"""Native transport refusal categories stay safe through terminal history."""

from unittest.mock import AsyncMock

import httpx
import pytest

from yleum_api.services import agent_native
from yleum_api.services.generation import provider_attempts
from yleum_api.services.generation.recovery_trace import safe_recovery_trace
from yleum_api.services.generation_failure import failure_for_error

URL = "https://gateway.test/v1/messages"
SUCCESS = {"content": [], "stop_reason": "end_turn"}


@pytest.mark.parametrize("status,category,marker", [
    (401, "access", "provider_refusal_access"),
    (401, "funds", "provider_refusal_funds"),
    (401, "rate_limit", "provider_refusal_rate_limit"),
    (403, "access", "provider_refusal_access"),
    (401, None, "provider_http_401"),
    (403, None, "provider_http_403"),
    (402, None, "provider_http_402"),
    (402, "funds", "provider_refusal_funds"),
])
async def test_native_auth_refusal_is_terminal_even_after_a_success(
    monkeypatch, status, category, marker,
):
    calls = []

    def reply(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=SUCCESS)
        return httpx.Response(status, json={"error": {
            "message": "synthetic-private-key https://private.test/path",
            "details": {"upstream_http_status": status, "provider_refusal_category": category},
        }})

    async def no_sleep(delay):
        pytest.fail("access/funds rejection must not repeat a provider POST")

    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)
    # Keep this synthetic run label at the transport boundary; SQL admission is tested separately.
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=None),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        assert await agent_native._call_messages(
            client, URL, [], "s", run_id="synthetic",
        ) == SUCCESS
        with pytest.raises(RuntimeError) as raised:
            await agent_native._call_messages(client, URL, [], "s", run_id="synthetic")
    assert len(calls) == 2
    assert str(raised.value).startswith(marker + ":")
    assert "private" not in str(raised.value)


@pytest.mark.parametrize("category", [None, True, ["funds"], {"private": "funds"},
                                       "unknown-private", "funds: private", "FUNDS"])
async def test_unknown_native_401_category_never_asserts_a_provider_balance(monkeypatch, category):
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(401, json={"error": {
            "message": "insufficient balance synthetic-private-key",
            "details": {"upstream_http_status": 401, "provider_refusal_category": category},
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(RuntimeError) as raised:
            await agent_native._call_messages(client, URL, [], "s")
    assert len(calls) == 1
    assert str(raised.value).startswith("provider_http_401:")
    public = failure_for_error(raised.value)
    assert "проверить доступ и средства" in public.message.casefold()
    assert "недостат" not in public.message.casefold()
    assert "private" not in str(raised.value) + public.model_dump_json()


@pytest.mark.parametrize("error,marker", [
    ({"type": "wallet_empty"}, "PLATFORM_WALLET_EMPTY"),
    ({"type": "wallet_empty", "details": {
        "upstream_http_status": 402, "provider_refusal_category": "funds",
    }}, "PLATFORM_WALLET_EMPTY"),
    ({"type": "api_error", "details": {
        "upstream_http_status": 402, "provider_refusal_category": "funds",
    }}, "provider_refusal_funds"),
    ({}, "provider_http_402"),
    ({"type": ["wallet_empty"]}, "provider_http_402"),
    ({"type": "wallet_empty private"}, "provider_http_402"),
])
async def test_native_402_distinguishes_platform_wallet_from_explicit_provider_funds(
    monkeypatch, error, marker,
):
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(402, json={"error": {
            **error, "message": "insufficient balance synthetic-private-key",
        }})

    async def no_sleep(delay):
        pytest.fail("a wallet/payment refusal must not repeat a provider POST")

    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(RuntimeError) as raised:
            await agent_native._call_messages(client, URL, [], "s")
    assert len(calls) == 1
    assert str(raised.value).startswith(marker + ":")
    assert "private" not in str(raised.value)
    public = failure_for_error(raised.value)
    assert not public.retryable
    if marker == "PLATFORM_WALLET_EMPTY":
        assert public.code == "wallet_empty"
        assert "кошел" in public.message.casefold() and "Yleum" in public.message
        assert "провайдер" not in public.message.casefold()
    elif marker == "provider_http_402":
        assert "проверить доступ и средства" in public.message.casefold()
        assert "недостат" not in public.message.casefold()
    else:
        assert "Провайдер модели сообщил о недостатке средств" in public.message


@pytest.mark.parametrize("recovers", [False, True])
async def test_known_native_rate_refusal_keeps_existing_retry_cadence(monkeypatch, recovers):
    calls, delays = [], []

    def reply(request):
        calls.append(request)
        if recovers and len(calls) == 2:
            return httpx.Response(200, json=SUCCESS)
        return httpx.Response(429, json={"error": {
            "type": "api_error",
            "message": "synthetic-private-key",
            "details": {
                "upstream_http_status": 429, "provider_refusal_category": "rate_limit",
                "provider_charge_ambiguous": False, "retry_safe": True,
                "retry_policy": "llmgw_key_rate_limit_v1",
            },
        }})

    async def pause(delay):
        delays.append(delay)

    monkeypatch.setattr(agent_native, "_CALL_RETRIES", 3)
    monkeypatch.setattr(agent_native.asyncio, "sleep", pause)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        if recovers:
            assert await agent_native._call_messages(client, URL, [], "s") == SUCCESS
        else:
            with pytest.raises(RuntimeError, match=r"^provider_refusal_rate_limit:") as raised:
                await agent_native._call_messages(client, URL, [], "s")
            assert "private" not in str(raised.value)
    assert len(calls) == (2 if recovers else 3)
    assert delays == ([6.0] if recovers else [6.0, 12.0])


@pytest.mark.parametrize("marker,code", [
    ("provider_refusal_access", "provider_access"),
    ("provider_refusal_funds", "provider_access"),
    ("provider_refusal_rate_limit", "provider_unavailable"),
    ("provider_http_401", "provider_access"),
    ("provider_http_402", "provider_access"),
    ("PLATFORM_WALLET_EMPTY", "wallet_empty"),
])
async def test_native_terminal_preserves_strict_safe_marker_for_history(monkeypatch, marker, code):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("JWT_SECRET", "test-only-native-secret")
    agent_native.get_settings.cache_clear()
    monkeypatch.setattr(agent_native, "_call_messages", AsyncMock(
        side_effect=RuntimeError(f"{marker}: synthetic-private-key https://private.test/path"),
    ))

    async def execute(action):
        pytest.fail("provider refusal must not execute or accept a starter")

    result = await agent_native.run_native_build(system="s", task="t", execute=execute)
    assert not result.done and result.stop_reason == "provider_error"
    assert result.summary.startswith(marker + ":")
    assert "private" not in result.summary
    failure = failure_for_error(result.summary)
    assert failure.code == code and not failure.retryable
    agent_native.get_settings.cache_clear()


def server_error_body(status=502):
    return {"type": "error", "error": {
        "type": "billing_unavailable", "message": "private-key https://private.test/path",
        "details": {
            "upstream_http_status": status, "provider_charge_ambiguous": True, "retry_safe": False,
        },
    }}


@pytest.mark.parametrize("gateway_status,upstream_status", [
    (503, 502), (502, 502), (503, 504), (503, 500),
])
async def test_native_server_failure_preserves_exact_status_without_retry(
    monkeypatch, gateway_status, upstream_status,
):
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(gateway_status, json=server_error_body(upstream_status))

    async def no_sleep(delay):
        pytest.fail("an ambiguous server response must not retry")

    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(agent_native._NativeProviderServerError) as raised:
            await agent_native._call_messages(client, URL, [], "s")
    assert len(calls) == 1
    assert str(raised.value).startswith(f"provider_http_{upstream_status}:")
    assert "private" not in str(raised.value)
    diagnostic = raised.value.diagnostics()
    assert diagnostic["http_status"] == gateway_status
    assert diagnostic["upstream_http_status"] == upstream_status
    assert diagnostic["retry_safe"] is diagnostic["will_retry"] is False
    assert diagnostic["provider_charge_ambiguous"] is True
    assert safe_recovery_trace(diagnostic)["upstream_http_status"] == upstream_status


@pytest.mark.parametrize("body", [
    "<html>private bad gateway</html>", {"error": {"message": "private"}},
])
async def test_bare_gateway_502_never_claims_an_upstream_status(monkeypatch, body):
    calls = []

    def reply(request):
        calls.append(request)
        if isinstance(body, str):
            return httpx.Response(502, text=body)
        return httpx.Response(502, json=body)

    async def no_sleep(delay):
        pytest.fail("an unproven gateway 502 must not retry")

    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(agent_native._NativeProviderServerError) as raised:
            await agent_native._call_messages(client, URL, [], "s")
    assert len(calls) == 1 and str(raised.value).startswith("provider_request_failed:")
    diagnostic = raised.value.diagnostics()
    assert diagnostic["http_status"] == 502 and "upstream_http_status" not in diagnostic
    assert diagnostic["error_origin"] == "gateway"
    assert "private" not in str(raised.value)


@pytest.mark.parametrize("mutation", [
    "ambiguous", "retry", "bool_status", "low_status", "high_status", "extra",
    "type", "duplicate", "redirect", "url", "method", "large",
])
def test_native_server_diagnostic_requires_closed_internal_gateway_contract(mutation):
    import json

    body = server_error_body()
    details = body["error"]["details"]
    request = httpx.Request("POST", URL)
    if mutation == "ambiguous":
        details["provider_charge_ambiguous"] = False
    elif mutation == "retry":
        details["retry_safe"] = True
    elif mutation == "bool_status":
        details["upstream_http_status"] = True
    elif mutation in {"low_status", "high_status"}:
        details["upstream_http_status"] = 499 if mutation == "low_status" else 600
    elif mutation == "extra":
        details["private"] = "anything"
    elif mutation == "type":
        body["error"]["type"] = "api_error"
    elif mutation == "url":
        request = httpx.Request("POST", "https://foreign.test/v1/messages")
    elif mutation == "method":
        request = httpx.Request("GET", URL)
    content = json.dumps(body)
    if mutation == "duplicate":
        content = content.replace(
            '"upstream_http_status": 502',
            '"upstream_http_status": 502, "upstream_http_status": 503',
        )
    elif mutation == "large":
        content += " " * 16_384
    response = httpx.Response(503, content=content.encode(), request=request,
                             history=[httpx.Response(307)] if mutation == "redirect" else [])
    assert agent_native._gateway_ambiguous_server_status(response, URL) is None
    assert not agent_native._gateway_preconnect_proven(response, URL)


@pytest.mark.parametrize("trace_fails", [False, True])
async def test_native_terminal_server_error_keeps_primary_and_emits_safe_trace(
    monkeypatch, trace_fails,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("JWT_SECRET", "test-only-native-secret")
    agent_native.get_settings.cache_clear()
    monkeypatch.setattr(agent_native, "_call_messages", AsyncMock(
        side_effect=agent_native._NativeProviderServerError(503, 502),
    ))
    events = []

    async def emit(kind, data):
        events.append((kind, data))
        if kind == "agent.provider_error" and trace_fails:
            raise RuntimeError("private diagnostic sink detail")

    async def execute(action):
        pytest.fail("provider failure must not execute a tool or accept a starter")

    result = await agent_native.run_native_build(system="s", task="t", execute=execute, emit=emit)
    assert not result.done and result.stop_reason == "provider_error"
    assert result.summary.startswith("provider_http_502:") and "private" not in result.summary
    diagnostics = [data for kind, data in events if kind == "agent.provider_error"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["upstream_http_status"] == 502 and diagnostics[0]["http_status"] == 503
    assert diagnostics[0]["will_retry"] is False
    assert diagnostics[0]["provider_charge_ambiguous"] is True
    agent_native.get_settings.cache_clear()

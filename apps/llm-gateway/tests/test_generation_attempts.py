"""Durable provider identity is independent of transport and never model authority."""

import hashlib
import importlib
import json
from uuid import UUID

import pytest


def protocol():
    module = "yleum_gateway.services.generation_identity"
    assert importlib.util.find_spec(module), "missing generation provider protocol"
    return importlib.import_module(module)


def test_exact_canonical_wire_and_omission_vector():
    body = {
        "model": "model",
        "messages": [{"role": "user", "content": "Привет"}],
        "stream": False,
        "temperature": 0.0,
    }
    expected = (
        '{"body":{"messages":[{"content":"Привет","role":"user"}],'
        '"model":"model","stream":false,"temperature":0.0},'
        '"route":"/v1/chat/completions","version":1}'
    ).encode()
    p = protocol()
    assert p.canonical_request("/v1/chat/completions", body) == expected
    assert p.request_digest("/v1/chat/completions", body) == hashlib.sha256(expected).hexdigest()
    assert p.request_digest("/v1/chat/completions", {**body, "max_tokens": None}) != (
        p.request_digest("/v1/chat/completions", body)
    )
    assert (
        p.request_digest(
            "/v1/chat/completions", {**body, "metadata": {"retry_count": 8}, "user": "owner"}
        )
        == hashlib.sha256(expected).hexdigest()
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {"bad"}, (1, 2)])
def test_noncanonical_values_never_get_a_digest(bad):
    with pytest.raises(ValueError):
        protocol().request_digest("/v1/messages", {"model": "m", "messages": [bad]})


@pytest.mark.parametrize(
    "raw",
    [
        '{"model":"m","model":"other","messages":[]}',
        '{"model":"m","messages":[],"temperature":NaN}',
        "[]",
    ],
)
def test_raw_json_rejects_duplicate_keys_nonfinite_and_nonobject(raw):
    with pytest.raises(ValueError):
        protocol().decode_request(raw.encode())


def test_unknown_semantic_fields_fail_closed():
    with pytest.raises(ValueError):
        protocol().request_digest("/v1/messages", {"model": "m", "messages": [], "new": 1})


def test_identity_roundtrip_is_strict_and_frozen():
    p = protocol()
    value = {
        "version": 1,
        **{
            key: str(UUID(int=i))
            for i, key in enumerate(
                ("run_id", "project_id", "user_id", "attempt_id"),
                1,
            )
        },
        "prompt_hash": "a" * 64,
        "request_sha256": "b" * 64,
    }
    identity = p.GenerationProviderIdentity.from_wire(value)
    assert identity.to_wire() == value
    assert p.GenerationProviderIdentity.from_wire(json.loads(json.dumps(value))) == identity
    for update in (
        {"version": True},
        {"extra": "forged"},
        {"run_id": "bad"},
        {"request_sha256": "B" * 64},
    ):
        with pytest.raises(ValueError):
            p.GenerationProviderIdentity.from_wire({**value, **update})
    with pytest.raises((AttributeError, TypeError)):
        identity.prompt_hash = "c" * 64


@pytest.mark.parametrize(
    "route,stream",
    [("/v1/messages", False), ("/v1/chat/completions", False), ("/v1/chat/completions", True)],
)
@pytest.mark.parametrize("enabled", [False, True])
def test_identity_never_falls_through_when_disabled_or_commit_unknown(
    monkeypatch, neutralize_lifespan, neutralize_side_effects, route, stream, enabled
):
    from unittest.mock import AsyncMock, Mock

    from fastapi.testclient import TestClient

    from yleum_gateway.main import create_app
    from yleum_gateway.providers import llmgw
    from yleum_gateway.routers import chat, messages_native
    from yleum_gateway.services import generation_attempts as attempts

    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", str(enabled).lower())
    body = {
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": stream,
    }
    p = protocol()
    identity = p.GenerationProviderIdentity(
        UUID(int=1), UUID(int=2), UUID(int=3), UUID(int=4), "a" * 64, p.request_digest(route, body)
    )
    body["metadata"] = {
        "run_id": str(identity.run_id),
        "project_id": str(identity.project_id),
        "generation_provider_identity": identity.to_wire(),
    }
    admission = AsyncMock(side_effect=attempts.AttemptConflict("commit unknown"))
    monkeypatch.setattr(attempts, "reserve_generation_attempt", admission)
    monkeypatch.setattr(
        messages_native, "native_messages_route", lambda: ("synthetic", "https://provider.test/v1")
    )
    posts = Mock(side_effect=AssertionError("unexpected physical POST"))
    monkeypatch.setattr(messages_native, "_post_llmgw", posts)
    completion = AsyncMock(side_effect=AssertionError("unexpected completion"))
    monkeypatch.setattr(llmgw, "acompletion", completion)
    monkeypatch.setattr(chat.router_module, "acompletion", completion)

    def forbidden_stream(*args, **kwargs):
        posts()
        raise AssertionError("unexpected stream")

    monkeypatch.setattr(llmgw, "astream", forbidden_stream)
    with TestClient(create_app()) as client:
        response = client.post(route, json=body)
    assert response.status_code in {409, 503}, response.text
    posts.assert_not_called()
    completion.assert_not_called()
    assert admission.await_count == int(enabled)


@pytest.mark.parametrize("bad", [None, {}, {"version": 2}, {"version": True}])
@pytest.mark.parametrize("route", ["/v1/messages", "/v1/chat/completions"])
def test_present_invalid_identity_is_denied_before_journal(
    monkeypatch, neutralize_lifespan, route, bad
):
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from yleum_gateway.main import create_app
    from yleum_gateway.services import provider_calls

    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    start = AsyncMock(side_effect=AssertionError("invalid identity reached journal"))
    monkeypatch.setattr(provider_calls, "start_call", start)
    body = {
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "x"}],
        "metadata": {"generation_provider_identity": bad},
    }
    with TestClient(create_app()) as client:
        response = client.post(route, json=body)
    assert response.status_code == 409
    start.assert_not_called()


@pytest.mark.parametrize("route", ["/v1/messages", "/v1/chat/completions"])
def test_identity_raw_duplicate_keys_cannot_be_lost_in_pydantic(
    monkeypatch, neutralize_lifespan, route
):
    from fastapi.testclient import TestClient

    from yleum_gateway.main import create_app

    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    raw = '{"model":"old","model":"claude-sonnet-5.5","messages":[{"role":"user","content":"x"}],"metadata":{"generation_provider_identity":null}}'
    with TestClient(create_app()) as client:
        response = client.post(route, content=raw, headers={"content-type": "application/json"})
    assert response.status_code == 409


@pytest.mark.parametrize("stream", [False, True, "nonstream"])
async def test_durable_late_connect_error_is_not_preconnect_proof(monkeypatch, stream):
    import httpx

    from yleum_gateway.core.config import reset_settings_cache
    from yleum_gateway.core.errors import UpstreamProviderError
    from yleum_gateway.providers import llmgw

    monkeypatch.setenv("LLMGW_API_KEY", "synthetic")
    reset_settings_cache()

    class BrokenBody(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __iter__(self):
            raise httpx.ConnectError("late failure after provider accepted")
            yield b""

        async def __aiter__(self):
            raise httpx.ConnectError("late failure after provider accepted")
            yield b""

    posts = []

    def reply(request):
        posts.append(request)
        return httpx.Response(
            200, stream=BrokenBody(), headers={"x-llmgw-request-id": "accepted-request"}
        )

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(reply))
    with pytest.raises(UpstreamProviderError) as caught:
        if stream is True:
            async for _ in llmgw.astream(
                "claude-sonnet-5.5", [{"role": "user", "content": "x"}], retry_preconnect=False
            ):
                pass
        else:
            await llmgw.acompletion(
                model="claude-sonnet-5.5",
                messages=[{"role": "user", "content": "x"}],
                retry_preconnect=False,
                max_tokens=32 if stream == "nonstream" else None,
            )
    assert len(posts) == 1
    assert caught.value.details["provider_charge_ambiguous"] is True
    assert caught.value.details.get("provider_failure_kind") != "preconnect"


def test_native_durable_late_connect_error_retains_receipt_without_retry(
    monkeypatch, neutralize_lifespan
):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import httpx
    from fastapi.testclient import TestClient

    from yleum_gateway.main import create_app
    from yleum_gateway.services import generation_attempts as attempts
    from yleum_gateway.services import provider_calls

    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic")
    body = {"model": "claude-sonnet-5.5", "messages": [{"role": "user", "content": "x"}]}
    p = protocol()
    identity = p.GenerationProviderIdentity(
        *(UUID(int=i) for i in range(1, 5)), "a" * 64, p.request_digest("/v1/messages", body)
    )
    body["metadata"] = {
        "run_id": str(identity.run_id),
        "project_id": str(identity.project_id),
        "generation_provider_identity": identity.to_wire(),
    }
    monkeypatch.setattr(attempts, "dispatch", AsyncMock(return_value=uuid4()))
    closed = AsyncMock()
    monkeypatch.setattr(attempts, "close_attempt", closed)

    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ConnectError("late after acceptance")
            yield b""

    posts = []

    def reply(request):
        posts.append(request)
        return httpx.Response(
            200,
            stream=BrokenBody(),
            headers={"x-llmgw-request-id": "accepted-native", "x-llmgw-cost-rub": "1.23"},
        )

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(reply))
    with TestClient(create_app()) as client:
        response = client.post("/v1/messages", json=body)
    assert len(posts) == 1 and response.status_code == 503
    assert "retry_safe" not in response.text
    assert provider_calls.finish_call.await_args.kwargs["status"] == "ambiguous"
    assert provider_calls.finish_call.await_args.kwargs["provider_request_id"] == "accepted-native"
    assert closed.await_args.kwargs["reason"] is None


@pytest.mark.parametrize(
    "route", ["/v1/messages", "/v1/project-cell/messages", "/v1/chat/completions"]
)
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("escaped", [False, True])
@pytest.mark.parametrize("identity_value", [None, {"version": 1}, {"version": 99}, "valid", "stale"])
def test_duplicate_metadata_cannot_hide_any_reserved_identity(
    monkeypatch,
    neutralize_lifespan,
    neutralize_side_effects,
    route,
    enabled,
    escaped,
    identity_value,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from yleum_gateway.main import create_app
    from yleum_gateway.routers import messages_native
    from yleum_gateway.services import generation_attempts as attempts

    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", str(enabled).lower())
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic")
    monkeypatch.setattr(
        messages_native,
        "verify_runner_bearer_header",
        lambda _: SimpleNamespace(project_id=UUID(int=2), run_id=UUID(int=1)),
    )
    monkeypatch.setattr(messages_native, "validate_runner_metadata", lambda *_: {})
    monkeypatch.setattr(messages_native, "consume_runner_jti", AsyncMock())
    dispatch = AsyncMock(side_effect=AssertionError("hidden identity reached dispatch"))
    monkeypatch.setattr(attempts, "dispatch", dispatch)
    if isinstance(identity_value, str):
        identity_value = protocol().GenerationProviderIdentity(
            *(UUID(int=i) for i in range(1, 5)), "a" * 64,
            protocol().request_digest(route, {
                "model": "claude-sonnet-5.5",
                "messages": [{"role": "user", "content": "x"}],
            }) if identity_value == "valid" else "c" * 64,
        ).to_wire()
    key = r"generation_provider_\u0069dentity" if escaped else "generation_provider_identity"
    metadata_key = r"metad\u0061ta" if escaped else "metadata"
    raw = (
        '{"model":"claude-sonnet-5.5","messages":[{"role":"user","content":"x"}],'
        f'"{metadata_key}":{{"{key}":{json.dumps(identity_value)}}},"metadata":{{}}}}'
    )
    with TestClient(create_app()) as client:
        response = client.post(route, content=raw, headers={"content-type": "application/json"})
    assert response.status_code == 409, response.text
    dispatch.assert_not_called()


def test_legacy_duplicate_metadata_and_prompt_marker_are_not_v1(monkeypatch):
    from yleum_gateway.services import generation_attempts as attempts

    monkeypatch.setattr(attempts, "get_settings", lambda: pytest.fail("legacy settings access"))
    raw = b'{"model":"m","messages":[{"content":"generation_provider_identity"}],"metadata":{"other":1},"metadata":{}}'
    assert attempts.parse_attempt(raw, "/v1/chat/completions") is None

"""Prep output policy and durable fallback facts, with no DB or provider I/O."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from yleum_api.core import config
from yleum_api.services import llm_client
from yleum_api.services import prompt_preparation as prep
from yleum_api.services.generation import provider_attempts
from yleum_api.services.generation.contracts import GenerationIds


def synthetic_settings():
    return config.Settings(
        _env_file=None, database_url="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        jwt_secret="synthetic-test-secret-with-no-production-use", mock_llm=False,
        prompt_preparation_enabled=True, max_generation_deadline_seconds=900,
        llm_gateway_url="https://gateway.test",
    )


@pytest.fixture
def claimed(monkeypatch):
    settings = synthetic_settings()
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    ids = GenerationIds(*(uuid4() for _ in range(5)))
    fingerprint = "a" * 64
    messages = prep.preparation_messages("PRIVATE_OWNER_REQUEST", {})
    state = {}

    async def claim(*_args):
        if state:
            return None, prep._cached(state, fingerprint)
        state.update(version=1, state="claimed", input_sha256=fingerprint,
                     claim_id=str(uuid4()), result="")
        return {**state, "messages": messages}, ""

    async def write(_session, _ids, value, *, claim_id):
        assert claim_id == state["claim_id"] and "messages" not in value
        state.update(value)
        return True

    class Session:
        commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    session = Session()
    monkeypatch.setattr(prep, "_claim", AsyncMock(side_effect=claim))
    monkeypatch.setattr(prep, "_write", AsyncMock(side_effect=write))
    return SimpleNamespace(ids=ids, state=state, messages=messages, factory=lambda: session)


@pytest.mark.parametrize("response,code,reason,tokens", [
    ({"choices": [{"finish_reason": "length", "message": {"content": "PRIVATE_PARTIAL"}}],
      "usage": {"completion_tokens": 8192}}, "provider_output_truncated", "length", 8192),
    ({"choices": [{"finish_reason": "content_filter", "message": {"content": "PRIVATE_FILTERED"}}],
      "usage": {"completion_tokens": 17}}, "provider_output_filtered", "content_filter", 17),
    ({"choices": [{"finish_reason": "stop", "message": {"content": {"PRIVATE": "BODY"}}}],
      "usage": {"completion_tokens": 18}}, "provider_output_invalid", "stop", 18),
    ({"choices": [], "usage": {"completion_tokens": 19}},
     "provider_output_invalid", None, 19),
    ({"choices": [{"finish_reason": "PRIVATE_UNKNOWN_REASON", "message": {"content": "PRIVATE"}}],
      "usage": {"completion_tokens": True}}, "provider_output_invalid", "unknown", None),
    (None, "provider_output_invalid", None, None),
], ids=[
    "truncated", "filtered", "invalid_message", "empty_choices", "unknown_stop", "invalid_json",
])
async def test_real_completion_failure_is_saved_once_without_partial_or_private_text(
    monkeypatch, claimed, caplog, response, code, reason, tokens,
):
    async def post(payload, _url, budget):
        assert payload["messages"] == claimed.messages and "max_tokens" not in payload
        assert payload["metadata"] == {
            "project_id": str(claimed.ids.project_id), "free": True, "stage": "prompt_preparation",
        }
        assert budget == 900.0
        return (httpx.Response(200, json=response) if response is not None else
                httpx.Response(200, content=b"PRIVATE_MALFORMED_JSON"))

    call = AsyncMock(side_effect=post)
    monkeypatch.setattr(llm_client, "_chat_post", call)
    assert await prep.prepare_prompt_brief(claimed.factory, claimed.ids) == ""
    failure = dict(claimed.state["failure"])
    assert failure == {
        "code": code, "error_type": "LLMOutputTruncated" if reason == "length" else "LLMError",
        "finish_reason": reason, "requested_max_tokens": None, "completion_tokens": tokens,
        "http_status": 200,
    }
    assert claimed.state["state"] == "fallback" and claimed.state["result"] == ""
    assert "PRIVATE" not in json.dumps(claimed.state) + caplog.text
    # A later entry reuses this terminal fallback; it cannot dispatch again.
    assert await prep.prepare_prompt_brief(claimed.factory, claimed.ids) == ""
    assert claimed.state["failure"] == failure
    call.assert_awaited_once()


async def test_complete_brief_larger_than_old_cap_is_ready_and_reused(monkeypatch, claimed):
    note = "Полный согласованный промпт.\n" * 300
    post = AsyncMock(return_value=httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": note}}],
        "usage": {"completion_tokens": 16000},
    }))
    monkeypatch.setattr(llm_client, "_chat_post", post)
    assert 4800 < len(note) <= prep.MAX_RESULT
    for _ in range(2):
        assert await prep.prepare_prompt_brief(claimed.factory, claimed.ids) == note.strip()
    assert claimed.state["state"] == "ready" and "failure" not in claimed.state
    post.assert_awaited_once()


@pytest.mark.parametrize("value,code", [
    (None, "provider_output_invalid"), ("  ", "provider_output_empty"),
    ("x" * (prep.MAX_RESULT + 1), "provider_output_oversize"),
], ids=["invalid", "empty", "oversize"])
async def test_unusable_brief_keeps_storage_guard_and_explains_fallback(claimed, value, code):
    complete = AsyncMock(return_value=value)
    for _ in range(2):
        assert await prep.prepare_prompt_brief(
            claimed.factory, claimed.ids, complete=complete,
        ) == ""
    assert claimed.state["failure"]["code"] == code
    assert claimed.state["result"] == "" and claimed.state["state"] == "fallback"
    complete.assert_awaited_once()


@pytest.mark.parametrize("error,code", [
    (TimeoutError("PRIVATE_TIMEOUT"), "timeout"),
    (llm_client.LLMError("PRIVATE_BODY", code="transport"), "transport"),
    (RuntimeError("PRIVATE_BODY"), "unknown"),
])
async def test_timeout_or_unknown_failure_is_durable_and_never_repeated(claimed, error, code):
    complete = AsyncMock(side_effect=error)
    for _ in range(2):
        assert await prep.prepare_prompt_brief(
            claimed.factory, claimed.ids, complete=complete,
        ) == ""
    assert claimed.state["failure"]["code"] == code
    assert "PRIVATE" not in json.dumps(claimed.state)
    complete.assert_awaited_once()


def test_failure_projection_rejects_untrusted_scalars_and_codes():
    failure = llm_client.LLMError(
        "PRIVATE_RESPONSE", code="PRIVATE_CODE", finish_reason="PRIVATE_STOP",
        requested_max_tokens=True, completion_tokens=-1, http_status=True,
        provider_stream_context={"PRIVATE": "SECRET"},
    )
    assert prep._failure_diagnostics(failure) == {
        "code": "unknown", "error_type": "LLMError", "finish_reason": "unknown",
        "requested_max_tokens": None, "completion_tokens": None, "http_status": None,
    }


async def test_wrapper_uses_configured_timeout_header_and_no_explicit_output_cap(monkeypatch):
    settings = synthetic_settings()
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    # Preparation deliberately has no retained generation-provider identity.
    prepare = AsyncMock(return_value=None)
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(provider_attempts, "reserve_generation_wire", AsyncMock(return_value=None))
    seen = []

    def gateway(request):
        payload = json.loads(request.content)
        seen.append(request)
        assert "max_tokens" not in payload
        assert "generation_provider_identity" not in payload["metadata"]
        budget = float(request.headers["x-yleum-provider-timeout-seconds"])
        assert 899 < budget <= 900
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": "Ready"}}],
        })

    real_client = httpx.AsyncClient
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(gateway), **kw,
    ))
    assert await llm_client.complete_prompt_brief(
        prep.preparation_messages("Owner request", {}),
        user_id=str(uuid4()), project_id=str(uuid4()),
    ) == "Ready"
    assert len(seen) == 1
    prepare.assert_awaited_once()


async def test_wrapper_cancellation_restores_attribution_and_claim_stays_unrepeated(
    monkeypatch, claimed,
):
    entered = asyncio.Event()

    async def post(*_args):
        entered.set()
        await asyncio.Event().wait()

    call = AsyncMock(side_effect=post)
    monkeypatch.setattr(llm_client, "_chat_post", call)
    original = (str(claimed.ids.run_id), str(claimed.ids.user_id), str(claimed.ids.project_id))
    token = llm_client._generation_billing_context.set(original)
    try:
        task = asyncio.create_task(prep.prepare_prompt_brief(claimed.factory, claimed.ids))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert llm_client._generation_billing_context.get() == original
        assert claimed.state["state"] == "claimed" and "failure" not in claimed.state
        assert await prep.prepare_prompt_brief(claimed.factory, claimed.ids) == ""
        call.assert_awaited_once()
    finally:
        llm_client._generation_billing_context.reset(token)

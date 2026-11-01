"""Private diagnostics use synthetic bytes only; no provider or other network."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from yleum_gateway.core.errors import UpstreamProviderError
from yleum_gateway.providers import llmgw
from yleum_gateway.routers import chat
from yleum_gateway.services import provider_validation_diagnostics as diagnostics

_KEY = "fixture-provider-key+/=NeverARealCredential"
_MODEL = "gemini-3.1-pro-preview-customtools"
_RESPONSE = b'{"error":{"code":"validation_error","detail":[{"loc":["body","messages",1],"type":"value_error"}]}}'


@pytest.mark.parametrize("native", [False, True], ids=["sync-json", "native-stream"])
async def test_real_422_transport_hook_preserves_exact_wire_and_original_error(monkeypatch, native):
    """The real adapter supplies the sent body, not a reconstructed payload."""
    from yleum_gateway.core.config import reset_settings_cache

    monkeypatch.setenv("LLMGW_API_KEY", _KEY)
    reset_settings_cache()
    sent, captured = [], []

    def reject(request):
        sent.append(request)
        return httpx.Response(422, content=_RESPONSE, headers={"x-request-id": "validation-fixture"})

    # Observe the private sink boundary, retaining all real HTTP/translation
    # code. Strict POSIX filesystem cases below run on Linux, not a Windows shim.
    def observe(error, credential):
        captured.append((diagnostics._SCOPE.get(), error, credential))

    monkeypatch.setattr(diagnostics, "_capture", observe)
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reject))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda *a, **kw: httpx.MockTransport(reject))
    scope = {"project_id": uuid4(), "run_id": uuid4(), "call_id": uuid4()}
    with diagnostics.provider_validation_scope(**scope), pytest.raises(UpstreamProviderError) as raised:
        await llmgw.acompletion(model="claude-sonnet-5.5" if native else _MODEL,
                               messages=[{"role": "user", "content": "hello\r\nмир"}],
                               max_tokens=None if native else 100, retry_preconnect=False)
    assert len(sent) == len(captured) == 1
    bound, error, credential = captured[0]
    assert bound.project_id == str(scope["project_id"]) and bound.call_id == str(scope["call_id"])
    assert credential == _KEY
    assert error is raised.value.__cause__
    assert error.response.request.content == sent[0].content
    assert json.loads(sent[0].content)["stream"] is native
    assert error.response.content == _RESPONSE
    assert raised.value.message == "llmgw HTTP 422"
    assert raised.value.details["upstream_http_status"] == 422
    assert _KEY not in str(raised.value.details)
    assert diagnostics._SCOPE.get() is None


@pytest.mark.parametrize("native", [False, True], ids=["sync-json", "native-stream"])
async def test_sink_failure_never_changes_provider_error_or_causes_replay(monkeypatch, native):
    from yleum_gateway.core.config import reset_settings_cache

    monkeypatch.setenv("LLMGW_API_KEY", _KEY)
    reset_settings_cache()
    sent = []

    def reject(request):
        sent.append(request)
        return httpx.Response(422, content=_RESPONSE)

    def broken_sink(*args):
        raise OSError("private diagnostic sink unavailable")

    monkeypatch.setattr(diagnostics, "_capture", broken_sink)
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reject))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda *a, **kw: httpx.MockTransport(reject))
    with pytest.raises(UpstreamProviderError) as raised:
        await llmgw.acompletion(model="claude-sonnet-5.5" if native else _MODEL,
            messages=[{"role": "user", "content": "synthetic"}],
            max_tokens=None if native else 100, retry_preconnect=False)
    assert len(sent) == 1
    assert isinstance(raised.value.__cause__, httpx.HTTPStatusError)
    assert raised.value.__cause__.response.content == _RESPONSE
    assert raised.value.message == "llmgw HTTP 422"
    assert "diagnostic" not in str(raised.value.details)


async def test_chat_scope_uses_committed_call_and_resets_on_failure(monkeypatch):
    call, project, run = uuid4(), uuid4(), uuid4()
    dispatch = AsyncMock(return_value=call)
    finished = AsyncMock()
    monkeypatch.setattr(chat.generation_attempts, "dispatch", dispatch)
    monkeypatch.setattr(chat, "_finish_failed_provider", finished)
    monkeypatch.setattr(chat.text_failover, "select_scope", AsyncMock(return_value="llmgw"))
    seen = []

    async def fail(**kwargs):
        seen.append(diagnostics._SCOPE.get())
        raise UpstreamProviderError("llmgw HTTP 422", details={"upstream_http_status": 422})

    monkeypatch.setattr(chat.llmgw, "acompletion", fail)
    request = Request({"type": "http", "headers": []})
    req = chat.ChatCompletionRequest(model=_MODEL, messages=[{"role": "user", "content": "synthetic"}],
                                    metadata={"project_id": project, "run_id": run})
    with pytest.raises(HTTPException) as raised:
        await chat._dispatch_chat_provider(req, request, identity=object(),
            filtered_messages=[{"role": "user", "content": "synthetic"}],
            received_at=asyncio.get_running_loop().time())
    assert dispatch.await_count == 1 and finished.await_count == 1
    assert seen == [diagnostics._Scope(str(project), str(run), str(call))]
    assert raised.value.detail["error"]["details"]["upstream_http_status"] == 422
    assert diagnostics._SCOPE.get() is None

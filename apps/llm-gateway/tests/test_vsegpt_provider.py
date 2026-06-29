"""Tests for the direct vsegpt.ru DeepSeek provider (providers/vsegpt.py).

The provider does a sync httpx.Client call on a worker thread (no proxy) — these
tests stub httpx.Client so nothing hits the network, and assert the OpenAI-shape
normalization, the Omnia-id remap, chain-of-thought stripping, and fail-fast
error translation.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omnia_gateway.core import config
from omnia_gateway.core.errors import UpstreamProviderError, ValidationFailedError
from omnia_gateway.providers import vsegpt

_MODEL = "deepseek-v4-flash-thinking"


def _canned(content: str) -> dict[str, Any]:
    return {
        "id": "cmpl-xyz",
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22},
    }


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "https://api.vsegpt.ru/v1/chat/completions"),
                response=httpx.Response(self.status_code, text=self.text),
            )

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Stand-in for httpx.Client used as a context manager. A single instance is
    installed as the module's ``httpx.Client`` and is itself callable, so
    ``httpx.Client(**kwargs)`` returns the same recording instance."""

    captured: dict[str, Any] = {}

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def __call__(self, *args: Any, **kwargs: Any) -> "_FakeClient":
        _FakeClient.captured["client_kwargs"] = kwargs
        return self

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def post(  # noqa: A002 — mirrors httpx.Client.post signature
        self,
        url: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        _FakeClient.captured["url"] = url
        _FakeClient.captured["payload"] = json
        _FakeClient.captured["headers"] = headers
        return self._response


@pytest.fixture
def _with_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VSEGPT_API_KEY", "sk-or-vv-test")
    monkeypatch.setenv("VSEGPT_BASE_URL", "https://api.vsegpt.ru/v1")
    config.reset_settings_cache()
    yield
    config.reset_settings_cache()


def _install_fake(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> None:
    _FakeClient.captured = {}
    monkeypatch.setattr(vsegpt.httpx, "Client", _FakeClient(response))


def test_is_vsegpt_model() -> None:
    assert vsegpt.is_vsegpt_model(_MODEL) is True
    # The workers, the Opus art_director, the Gemini orchestrator and the MiniMax
    # developer all ride vsegpt now.
    assert vsegpt.is_vsegpt_model("deepseek-chat") is True
    assert vsegpt.is_vsegpt_model("claude-opus-4-8") is True
    assert vsegpt.is_vsegpt_model("gemini-3.5-flash-high") is True
    assert vsegpt.is_vsegpt_model("minimax-m2.7") is True
    assert vsegpt.is_vsegpt_model("deepseek-v4-pro-thinking") is True
    assert vsegpt.is_vsegpt_model("deepseek-v4-pro") is True
    assert vsegpt.is_vsegpt_model("gemini-3-flash-vision") is True


def test_vision_model_keeps_image_blocks() -> None:
    # `vis-` models PASS the OpenAI multimodal array (text + image_url) so the
    # screenshot reaches the judge; text-only models flatten it away.
    assert vsegpt._is_vision("gemini-3-flash-vision") is True
    # Owner 2026-06-29: Opus 4.8 is the vision judge — natively multimodal, kept as a
    # vision model (no vis- prefix) so its screenshot image_url blocks are forwarded.
    assert vsegpt._is_vision("claude-opus-4-8") is True
    assert vsegpt._is_vision("deepseek-chat") is False
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "judge this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ]
    vis = vsegpt._to_vsegpt_messages(msgs, vision=True)
    assert isinstance(vis[0]["content"], list)
    assert any(b.get("type") == "image_url" for b in vis[0]["content"])
    txt = vsegpt._to_vsegpt_messages(msgs, vision=False)
    assert isinstance(txt[0]["content"], str)
    assert "judge this" in txt[0]["content"]
    # Opus 4.7 stays a proxyapi/Router model — not dispatched to vsegpt.
    assert vsegpt.is_vsegpt_model("claude-opus-4-7") is False
    assert vsegpt.is_vsegpt_model("gpt-5") is False


@pytest.mark.asyncio
async def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VSEGPT_API_KEY", "")
    config.reset_settings_cache()
    try:
        with pytest.raises(UpstreamProviderError):
            await vsegpt.acompletion(model=_MODEL, messages=[{"role": "user", "content": "hi"}])
    finally:
        config.reset_settings_cache()


@pytest.mark.asyncio
async def test_unknown_model_raises(_with_key) -> None:
    with pytest.raises(ValidationFailedError):
        await vsegpt.acompletion(
            model="not-a-vsegpt-model", messages=[{"role": "user", "content": "hi"}]
        )


@pytest.mark.asyncio
async def test_happy_path_normalizes_and_remaps_model(
    monkeypatch: pytest.MonkeyPatch, _with_key
) -> None:
    _install_fake(monkeypatch, _FakeResponse(_canned("Привет, мир!")))

    out = await vsegpt.acompletion(
        model=_MODEL, messages=[{"role": "user", "content": "сделай лендинг"}]
    )

    # model remapped to the Omnia id (not the vsegpt slug) so chat.py bills right.
    assert out["model"] == _MODEL
    assert out["choices"][0]["message"]["content"] == "Привет, мир!"
    assert out["usage"]["prompt_tokens"] == 11
    assert out["usage"]["completion_tokens"] == 22
    # The request carried the vsegpt slug + bearer key.
    assert _FakeClient.captured["payload"]["model"] == "deepseek/deepseek-v4-flash-thinking"
    assert _FakeClient.captured["headers"]["Authorization"] == "Bearer sk-or-vv-test"
    # Proxy env must be ignored (RU endpoint hit direct).
    assert _FakeClient.captured["client_kwargs"]["trust_env"] is False
    assert "mounts" in _FakeClient.captured["client_kwargs"]


@pytest.mark.asyncio
async def test_strips_think_block(monkeypatch: pytest.MonkeyPatch, _with_key) -> None:
    _install_fake(
        monkeypatch,
        _FakeResponse(_canned("<think>reasoning that must not leak</think>Final answer.")),
    )
    out = await vsegpt.acompletion(model=_MODEL, messages=[{"role": "user", "content": "x"}])
    assert out["choices"][0]["message"]["content"] == "Final answer."


@pytest.mark.asyncio
async def test_default_max_tokens_sent(monkeypatch: pytest.MonkeyPatch, _with_key) -> None:
    _install_fake(monkeypatch, _FakeResponse(_canned("ok")))
    await vsegpt.acompletion(model=_MODEL, messages=[{"role": "user", "content": "x"}])
    # Thinking model gets a wide budget by default so CoT can't truncate output
    # (32768 leaves room for reasoning + a full landing page).
    assert _FakeClient.captured["payload"]["max_tokens"] == 32768


@pytest.mark.asyncio
async def test_flattens_multimodal_content(monkeypatch: pytest.MonkeyPatch, _with_key) -> None:
    _install_fake(monkeypatch, _FakeResponse(_canned("ok")))
    await vsegpt.acompletion(
        model=_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ],
    )
    # Image block dropped, text kept — DeepSeek is text-only.
    assert _FakeClient.captured["payload"]["messages"][0]["content"] == "describe"


@pytest.mark.asyncio
async def test_http_error_translates(monkeypatch: pytest.MonkeyPatch, _with_key) -> None:
    _install_fake(monkeypatch, _FakeResponse({"error": "bad"}, status=502))
    with pytest.raises(UpstreamProviderError):
        await vsegpt.acompletion(model=_MODEL, messages=[{"role": "user", "content": "x"}])

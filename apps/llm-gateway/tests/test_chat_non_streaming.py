"""POST /v1/chat/completions — non-streaming, with mocked provider + DB."""
from __future__ import annotations

from typing import Iterator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnia_gateway.main import create_app


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """App with DB lifecycle and usage_logger replaced by no-ops."""
    monkeypatch.setattr("omnia_gateway.main.init_pool", AsyncMock(return_value=None))
    monkeypatch.setattr("omnia_gateway.main.close_pool", AsyncMock(return_value=None))
    monkeypatch.setattr("omnia_gateway.main.configure_logging", lambda: None)
    monkeypatch.setattr(
        "omnia_gateway.routers.chat.log_usage",
        AsyncMock(return_value=uuid4()),
    )
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_models_endpoint_lists_all_supported(client: TestClient) -> None:
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    ids = {m["id"] for m in body["data"]}
    assert {"claude-sonnet-4-6", "gpt-4.1", "gpt-5-mini", "yandexgpt-5", "qwen-3-coder"} <= ids
    # No keys configured in test → all unavailable.
    assert all(m["available"] is False for m in body["data"])


def test_chat_completion_non_streaming_happy_path(client: TestClient) -> None:
    fake_response = {
        "id": "test-1",
        "object": "chat.completion",
        "created": 1234,
        "model": "claude-sonnet-4-6",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    with patch(
        "omnia_gateway.routers.chat.litellm_router.acompletion",
        new=AsyncMock(return_value=fake_response),
    ):
        body = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
            "user": str(uuid4()),
            "metadata": {
                "project_id": str(uuid4()),
                "message_id": str(uuid4()),
            },
        }
        r = client.post("/v1/chat/completions", json=body)

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "hi"
    # 10*0.30/1000 + 5*1.50/1000 = 0.0030 + 0.0075 = 0.0105
    assert data["metadata"]["cost_rub"] == "0.0105"
    assert data["metadata"]["actual_model_used"] == "claude-sonnet-4-6"


def test_chat_stream_true_returns_501(client: TestClient) -> None:
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    r = client.post("/v1/chat/completions", json=body)
    assert r.status_code == 501
    assert r.json()["detail"]["error"]["code"] == "not_implemented"


def test_chat_unknown_model_returns_404(client: TestClient) -> None:
    body = {
        "model": "totally-fake-model",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
    r = client.post("/v1/chat/completions", json=body)
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "model_not_found"


def test_chat_no_provider_key_returns_503(client: TestClient) -> None:
    """No API keys configured (per conftest) → ModelUnavailableError → 503."""
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
    r = client.post("/v1/chat/completions", json=body)
    assert r.status_code == 503
    assert r.json()["detail"]["error"]["code"] == "model_unavailable"

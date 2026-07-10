"""POST /v1/chat/completions with stream=true — SSE chunks + cancellation hook."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnia_gateway.main import create_app

_MODEL = "claude-opus-4-8"


async def _fake_astream(
    model: str, messages: list[dict], **kwargs: object
) -> AsyncIterator[tuple[str, str]]:
    """Mimic providers.oneprovider.astream — yields (delta, omnia_id) tuples."""
    for piece in ["Hel", "lo", " world"]:
        yield piece, model


@pytest.fixture
def app(neutralize_lifespan: None, neutralize_side_effects: None) -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _parse_sse(body: str) -> list[dict | str]:
    out: list[dict | str] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line.removeprefix("data: ")
        if payload == "[DONE]":
            out.append("[DONE]")
            continue
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return out


def test_chat_streaming_yields_sse_chunks(client: TestClient) -> None:
    with patch(
        "omnia_gateway.services.streaming.oneprovider.astream",
        _fake_astream,
    ):
        body = {
            "model": _MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "user": str(uuid4()),
        }
        r = client.post("/v1/chat/completions", json=body)
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")

    events = _parse_sse(r.text)
    # At least: 3 deltas + final usage chunk + [DONE]
    delta_chunks = [
        e
        for e in events
        if isinstance(e, dict) and e.get("choices", [{}])[0].get("delta", {}).get("content")
    ]
    final_chunks = [e for e in events if isinstance(e, dict) and e.get("usage")]
    done = [e for e in events if e == "[DONE]"]
    assert len(delta_chunks) == 3
    assert len(final_chunks) == 1
    assert done == ["[DONE]"]
    assembled = "".join(c["choices"][0]["delta"]["content"] for c in delta_chunks)
    assert assembled == "Hello world"
    final = final_chunks[0]
    assert final["model"] == _MODEL
    assert final["metadata"]["actual_model_used"] == _MODEL
    assert final["metadata"]["fallback_used"] is False

"""Anthropic-shaped native-agent adapter over llmgw OpenAI tool calling."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnia_gateway.main import create_app
from omnia_gateway.routers import messages_native


@pytest.fixture
def app(neutralize_lifespan: None) -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_openai_messages_preserve_tool_turns() -> None:
    body = {
        "system": [{"type": "text", "text": "Build carefully"}],
        "messages": [
            {"role": "user", "content": "Create an app"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private"},
                    {"type": "text", "text": "I will inspect it."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "src/app.tsx"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "export default App",
                    }
                ],
            },
        ],
    }

    adapted = messages_native._openai_messages(body)

    assert adapted[0] == {"role": "system", "content": "Build carefully"}
    assert adapted[1] == {"role": "user", "content": "Create an app"}
    assert adapted[2]["content"] == "I will inspect it."
    assert adapted[2]["tool_calls"][0]["id"] == "toolu_1"
    assert adapted[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert adapted[3] == {
        "role": "tool",
        "tool_call_id": "toolu_1",
        "content": "export default App",
    }


def test_anthropic_response_preserves_tool_id_and_arguments() -> None:
    upstream = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "Writing the file.",
                    "tool_calls": [
                        {
                            "id": "toolu_2",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"path":"src/app.tsx","content":"ok"}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "prompt_tokens_details": {"cached_tokens": 4},
        },
    }

    adapted = messages_native._anthropic_response(upstream, "claude-opus-4-8")

    assert adapted["stop_reason"] == "tool_use"
    assert adapted["content"][0] == {
        "type": "text",
        "text": "Writing the file.",
    }
    assert adapted["content"][1] == {
        "type": "tool_use",
        "id": "toolu_2",
        "name": "write_file",
        "input": {"path": "src/app.tsx", "content": "ok"},
    }
    assert adapted["usage"]["cache_read_input_tokens"] == 4


def test_native_endpoint_uses_llmgw_chat_tools(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda: ("test-key", "https://api.llmgw.ru/v1"),
    )

    def fake_post(
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        captured.update(url=url, json=payload, headers=headers)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-2",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "toolu_done",
                                    "type": "function",
                                    "function": {
                                        "name": "done",
                                        "arguments": '{"summary":"ok"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    monkeypatch.setattr(messages_native, "_post_llmgw", fake_post)

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-opus-4-8",
            "max_tokens": 128,
            "system": "Build",
            "messages": [{"role": "user", "content": "Finish"}],
            "tools": [
                {
                    "name": "done",
                    "description": "Finish",
                    "input_schema": {
                        "type": "object",
                        "properties": {"summary": {"type": "string"}},
                        "required": ["summary"],
                    },
                }
            ],
            "tool_choice": {"type": "auto"},
        },
    )

    assert response.status_code == 200
    assert captured["url"] == "https://api.llmgw.ru/v1/chat/completions"
    assert captured["json"]["model"] == "anthropic/claude-opus-4.8"
    assert captured["json"]["tools"][0]["function"]["name"] == "done"
    assert response.json()["content"][0] == {
        "type": "tool_use",
        "id": "toolu_done",
        "name": "done",
        "input": {"summary": "ok"},
    }

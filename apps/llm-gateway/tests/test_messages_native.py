"""Anthropic-shaped native-agent adapter over llmgw OpenAI tool calling."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnia_gateway.core.errors import WalletEmptyError
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


def test_openai_messages_compact_completed_large_file_history() -> None:
    large_source = "export const feature = true;\n" * 1_500
    large_read = "line from an old read\n" * 1_500
    latest_error = "src/app/page.tsx(1,1): error TS1005"
    body = {
        "messages": [
            {"role": "user", "content": "Build the complete app"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "write_1",
                        "name": "write_file",
                        "input": {"path": "src/app/page.tsx", "content": large_source},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "write_1", "content": large_read}
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "build_1", "name": "build", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "build_1",
                        "content": latest_error,
                        "is_error": True,
                    }
                ],
            },
        ]
    }

    adapted = messages_native._openai_messages(body)
    encoded = json.dumps(adapted, ensure_ascii=False)

    assert large_source not in encoded
    assert large_read not in encoded
    assert "OMITTED FROM HISTORY" in encoded
    assert "OLDER TOOL RESULT OMITTED" in encoded
    assert latest_error in encoded
    assert len(encoded) < 3_000


def test_openai_adapter_preserves_prompt_cache_breakpoints() -> None:
    cache = {"type": "ephemeral"}
    body = {
        "system": [{"type": "text", "text": "Stable system", "cache_control": cache}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Stable prefix"},
                    {"type": "text", "text": "Moving tail", "cache_control": cache},
                ],
            }
        ],
    }

    adapted = messages_native._openai_messages(body)
    tools = messages_native._openai_tools(
        [
            {
                "name": "done",
                "description": "Finish",
                "input_schema": {"type": "object", "properties": {}},
                "cache_control": cache,
            }
        ]
    )

    assert adapted[0]["content"][0]["cache_control"] == cache
    assert adapted[1]["content"][-1]["cache_control"] == cache
    assert tools[0]["cache_control"] == cache

    tool_tail = messages_native._openai_messages(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "result",
                            "cache_control": cache,
                        }
                    ],
                }
            ]
        }
    )
    assert tool_tail[0]["role"] == "tool"
    assert tool_tail[0]["cache_control"] == cache


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
            "prompt_tokens_details": {"cached_tokens": 4, "cache_creation_tokens": 3},
        },
    }

    adapted = messages_native._anthropic_response(upstream, "gemini-3.1-pro-preview-customtools")

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
    assert adapted["usage"]["cache_creation_input_tokens"] == 3


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
            "model": "gemini-3.1-pro-preview-customtools",
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
    assert captured["json"]["model"] == "google/gemini-3.1-pro-preview-customtools"
    assert captured["json"]["tools"][0]["function"]["name"] == "done"
    assert response.json()["content"][0] == {
        "type": "tool_use",
        "id": "toolu_done",
        "name": "done",
        "input": {"summary": "ok"},
    }


def test_native_endpoint_attributes_and_bills_actual_cached_usage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = "11111111-1111-1111-1111-111111111111"
    project_id = "22222222-2222-2222-2222-222222222222"
    run_id = "33333333-3333-3333-3333-333333333333"
    message_id = "44444444-4444-4444-4444-444444444444"
    precheck = AsyncMock(return_value=None)
    charge = AsyncMock(return_value=UUID("55555555-5555-5555-5555-555555555555"))
    monkeypatch.setattr(messages_native.billing, "precheck_balance", precheck)
    monkeypatch.setattr(messages_native.billing, "charge", charge)
    monkeypatch.setattr(messages_native.file_logger, "log_request", lambda payload: None)
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda: ("test-key", "https://api.llmgw.ru/v1"),
    )

    def fake_post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-cost-usd": "0.125"},
            json={
                "id": "provider-request-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ok", "tool_calls": []},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "prompt_tokens_details": {
                        "cached_tokens": 600,
                        "cache_creation_tokens": 200,
                    },
                },
            },
        )

    monkeypatch.setattr(messages_native, "_post_llmgw", fake_post)
    response = client.post(
        "/v1/messages",
        json={
            "model": "gemini-3.1-pro-preview-customtools",
            "max_tokens": 1000,
            "user": user_id,
            "metadata": {
                "project_id": project_id,
                "run_id": run_id,
                "message_id": message_id,
                "stage": "verification",
                "retry_count": 2,
            },
            "messages": [{"role": "user", "content": "check"}],
        },
    )

    assert response.status_code == 200
    precheck.assert_awaited_once()
    kwargs = charge.await_args.kwargs
    assert kwargs["user_id"] == UUID(user_id)
    assert kwargs["project_id"] == UUID(project_id)
    assert kwargs["run_id"] == UUID(run_id)
    assert kwargs["message_id"] == UUID(message_id)
    assert kwargs["stage"] == "verification"
    assert kwargs["retry_count"] == 2
    assert kwargs["cache_read_tokens"] == 600
    assert kwargs["cache_write_tokens"] == 200
    assert str(kwargs["provider_cost_usd"]) == "0.125"


def test_native_endpoint_stops_before_provider_when_wallet_limit_is_reached(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    precheck = AsyncMock(side_effect=WalletEmptyError("Insufficient wallet balance for request"))
    upstream = pytest.fail
    monkeypatch.setattr(messages_native.billing, "precheck_balance", precheck)
    monkeypatch.setattr(messages_native, "_post_llmgw", upstream)
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda: ("test-key", "https://api.llmgw.ru/v1"),
    )

    response = client.post(
        "/v1/messages",
        json={
            "model": "gemini-3.1-pro-preview-customtools",
            "user": "11111111-1111-1111-1111-111111111111",
            "metadata": {
                "project_id": "22222222-2222-2222-2222-222222222222",
                "run_id": "33333333-3333-3333-3333-333333333333",
            },
            "messages": [{"role": "user", "content": "do not call upstream"}],
        },
    )

    assert response.status_code == 402
    assert response.json()["error"]["type"] == "wallet_empty"
    precheck.assert_awaited_once()

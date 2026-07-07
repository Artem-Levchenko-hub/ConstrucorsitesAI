"""Converter tests for the native-agent vsegpt adapter (providers/vsegpt_native).

Pure-function coverage: Anthropic Messages request → OpenAI chat payload, and
OpenAI completion → Anthropic response. The agent loop (apps/api agent_native)
consumes exactly ``content`` blocks / ``stop_reason`` / ``usage`` — every branch
here mirrors a real turn shape from that loop.
"""

from __future__ import annotations

import json
from typing import Any

from omnia_gateway.providers.vsegpt_native import (
    to_anthropic_response,
    to_openai_payload,
)

_MODEL = "claude-opus-4-8"
_SLUG = "claude-opus-4-8"


def _agent_request(**over: Any) -> dict[str, Any]:
    """The exact first-turn shape agent_native._call_messages sends."""
    body: dict[str, Any] = {
        "model": _MODEL,
        "max_tokens": 32000,
        "thinking": {"type": "enabled", "budget_tokens": 8000},
        "system": "You are the build agent.",
        "tools": [
            {
                "name": "read_file",
                "description": "Read a file.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ],
        "tool_choice": {"type": "auto"},
        "messages": [{"role": "user", "content": "построй лендинг"}],
    }
    body.update(over)
    return body


# ── request conversion ───────────────────────────────────────────────────────


def test_request_basic_shape() -> None:
    p = to_openai_payload(_agent_request())
    assert p["model"] == _SLUG
    assert p["max_tokens"] == 32000
    assert p["stream"] is False
    assert "thinking" not in p  # dropped by design — that's the latency win
    assert p["messages"][0] == {"role": "system", "content": "You are the build agent."}
    assert p["messages"][1] == {"role": "user", "content": "построй лендинг"}
    fn = p["tools"][0]["function"]
    assert p["tools"][0]["type"] == "function"
    assert fn["name"] == "read_file"
    assert fn["parameters"]["required"] == ["path"]
    assert p["tool_choice"] == "auto"


def test_request_tool_choice_variants() -> None:
    p_any = to_openai_payload(_agent_request(tool_choice={"type": "any"}))
    assert p_any["tool_choice"] == "required"
    p_tool = to_openai_payload(
        _agent_request(tool_choice={"type": "tool", "name": "read_file"})
    )
    assert p_tool["tool_choice"] == {
        "type": "function",
        "function": {"name": "read_file"},
    }


def test_request_history_round_trip() -> None:
    """Assistant thinking+text+tool_use, then user tool_result — the turn-2 shape."""
    msgs = [
        {"role": "user", "content": "построй лендинг"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "…chain…", "signature": "sig=="},
                {"type": "text", "text": "Читаю файл."},
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "read_file",
                    "input": {"path": "src/app/page.tsx"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01",
                    "content": "export default function Page() {}",
                }
            ],
        },
    ]
    p = to_openai_payload(_agent_request(messages=msgs))
    m = p["messages"]
    # system + user + assistant + tool
    assert [x["role"] for x in m] == ["system", "user", "assistant", "tool"]
    a = m[2]
    assert a["content"] == "Читаю файл."  # thinking block dropped, text kept
    assert a["tool_calls"][0]["id"] == "toolu_01"
    assert a["tool_calls"][0]["function"]["name"] == "read_file"
    assert json.loads(a["tool_calls"][0]["function"]["arguments"]) == {
        "path": "src/app/page.tsx"
    }
    t = m[3]
    assert t["tool_call_id"] == "toolu_01"
    assert t["content"] == "export default function Page() {}"


def test_request_tool_result_error_prefix() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_02",
                    "content": "TS2307: Cannot find module",
                    "is_error": True,
                }
            ],
        }
    ]
    p = to_openai_payload(_agent_request(messages=msgs))
    assert p["messages"][-1]["content"].startswith("[TOOL ERROR] TS2307")


def test_request_image_block_becomes_data_url() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "вот скрин"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "AAAA",
                    },
                },
            ],
        }
    ]
    p = to_openai_payload(_agent_request(messages=msgs))
    parts = p["messages"][-1]["content"]
    assert parts[0] == {"type": "text", "text": "вот скрин"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,AAAA"


def test_request_system_block_list_flattens() -> None:
    p = to_openai_payload(
        _agent_request(
            system=[
                {"type": "text", "text": "part one", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "part two"},
            ]
        )
    )
    assert p["messages"][0] == {"role": "system", "content": "part one\npart two"}


# ── response conversion ──────────────────────────────────────────────────────


def _openai_response(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Готово."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 378, "completion_tokens": 65},
    }
    data.update(over)
    return data


def test_response_text_end_turn() -> None:
    r = to_anthropic_response(_openai_response(), _MODEL)
    assert r["type"] == "message"
    assert r["role"] == "assistant"
    assert r["model"] == _MODEL
    assert r["content"] == [{"type": "text", "text": "Готово."}]
    assert r["stop_reason"] == "end_turn"
    assert r["usage"] == {"input_tokens": 378, "output_tokens": 65}


def test_response_tool_calls_become_tool_use() -> None:
    r = to_anthropic_response(
        _openai_response(
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_9",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"city": "Paris"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        ),
        _MODEL,
    )
    assert r["stop_reason"] == "tool_use"
    (block,) = r["content"]
    assert block["type"] == "tool_use"
    assert block["id"] == "call_9"
    assert block["name"] == "get_weather"
    assert block["input"] == {"city": "Paris"}


def test_response_malformed_args_degrade_to_empty() -> None:
    r = to_anthropic_response(
        _openai_response(
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_x",
                                "type": "function",
                                "function": {"name": "grep", "arguments": "{broken"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        ),
        _MODEL,
    )
    assert r["content"][0]["input"] == {}


def test_response_think_block_stripped() -> None:
    r = to_anthropic_response(
        _openai_response(
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "<think>hidden</think>Ответ.",
                    },
                    "finish_reason": "stop",
                }
            ]
        ),
        _MODEL,
    )
    assert r["content"] == [{"type": "text", "text": "Ответ."}]


def test_response_length_maps_to_max_tokens() -> None:
    r = to_anthropic_response(
        _openai_response(
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "обрыв"},
                    "finish_reason": "length",
                }
            ]
        ),
        _MODEL,
    )
    assert r["stop_reason"] == "max_tokens"

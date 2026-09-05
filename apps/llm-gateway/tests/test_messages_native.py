"""Anthropic-shaped native-agent adapter over llmgw OpenAI tool calling."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnia_gateway.core import runner_auth
from omnia_gateway.core.errors import WalletEmptyError
from omnia_gateway.main import create_app
from omnia_gateway.routers import messages_native

_RUNNER_SECRET = "runner-secret"
_RUNNER_ISSUER = "omnia-agent-runner"
_RUNNER_AUDIENCE = "omnia-project-cell-runner"
_RUNNER_PROJECT_ID = "22222222-2222-2222-2222-222222222222"
_RUNNER_RUN_ID = "33333333-3333-3333-3333-333333333333"
_RUNNER_SESSION_ID = "66666666-6666-6666-6666-666666666666"
_RUNNER_WORKSPACE_ID = "77777777-7777-7777-7777-777777777777"
_RUNNER_MESSAGE_ID = "88888888-8888-4888-8888-888888888888"


@pytest.fixture
def app(neutralize_lifespan: None) -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _runner_token(
    *,
    now: int | None = None,
    alg: str = "HS256",
    overrides: dict[str, Any] | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else now
    header = {"alg": alg, "typ": "JWT"}
    payload: dict[str, Any] = {
        "iss": _RUNNER_ISSUER,
        "aud": _RUNNER_AUDIENCE,
        "jti": _RUNNER_MESSAGE_ID,
        "project_id": _RUNNER_PROJECT_ID,
        "run_id": _RUNNER_RUN_ID,
        "session_id": _RUNNER_SESSION_ID,
        "workspace_id": _RUNNER_WORKSPACE_ID,
        "fencing_epoch": 4,
        "cancel_epoch": 0,
        "nbf": issued_at - 1,
        "exp": issued_at + 60,
    }
    if overrides:
        payload.update(overrides)
    header_segment = _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_segment = _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    digest_name = {"HS256": "sha256", "HS512": "sha512"}.get(alg)
    signature = _b64url(
        hmac.new(
            _RUNNER_SECRET.encode("utf-8"),
            signing_input,
            getattr(hashlib, digest_name or "sha256"),
        ).digest()
    )
    return f"{header_segment}.{payload_segment}.{signature}"


class _FakeRunnerRedis:
    def __init__(
        self,
        *,
        results: list[bool] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._results = list(results) if results is not None else [True]
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def set(
        self,
        name: str,
        value: bytes,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        self.calls.append({"name": name, "value": value, "ex": ex, "nx": nx})
        if self._error is not None:
            raise self._error
        if self._results:
            return self._results.pop(0)
        return False


def _install_runner_auth(
    monkeypatch: pytest.MonkeyPatch,
    *,
    redis_results: list[bool] | None = None,
    redis_error: Exception | None = None,
) -> _FakeRunnerRedis:
    monkeypatch.setenv("RUNNER_AUTH_SECRET", _RUNNER_SECRET)
    monkeypatch.setenv("RUNNER_AUTH_ISSUER", _RUNNER_ISSUER)
    monkeypatch.setenv("RUNNER_AUTH_AUDIENCE", _RUNNER_AUDIENCE)
    fake_redis = _FakeRunnerRedis(results=redis_results, error=redis_error)
    monkeypatch.setattr(runner_auth, "get_redis", lambda: fake_redis)
    return fake_redis


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


def test_openai_tool_results_precede_mixed_user_feedback() -> None:
    cache = {"type": "ephemeral"}
    adapted = messages_native._openai_messages({
        "messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": call_id, "name": "read_file", "input": {}}
                for call_id in ("one", "two")
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "one", "content": "first"},
                {"type": "tool_result", "tool_use_id": "two", "content": "second",
                 "cache_control": cache},
                {"type": "text", "text": "Stop exploring and implement the missing API.",
                 "cache_control": cache},
            ]},
        ],
    })
    assert [message["role"] for message in adapted] == ["assistant", "tool", "tool", "user"]
    assert [message["tool_call_id"] for message in adapted[1:3]] == ["one", "two"]
    assert adapted[2]["cache_control"] == cache
    assert adapted[3]["content"] == [{
        "type": "text", "text": "Stop exploring and implement the missing API.",
        "cache_control": cache,
    }]


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


def test_project_cell_endpoint_rejects_missing_bearer_before_json_or_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_runner_auth(monkeypatch)
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)
    precheck = AsyncMock(return_value=None)
    monkeypatch.setattr(messages_native.billing, "precheck_balance", precheck)

    response = client.post(
        "/v1/project-cell/messages",
        content="{not-json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"
    precheck.assert_not_awaited()


def test_project_cell_endpoint_fails_closed_without_issuer_config(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNNER_AUTH_SECRET", _RUNNER_SECRET)
    monkeypatch.setenv("RUNNER_AUTH_ISSUER", "")
    monkeypatch.setenv("RUNNER_AUTH_AUDIENCE", _RUNNER_AUDIENCE)
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)

    response = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token()}"},
        json={"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "check"}]},
    )

    assert response.status_code == 503
    assert "issuer is not configured" in response.json()["error"]["message"]


def test_project_cell_endpoint_rejects_metadata_mismatch_before_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_runner_auth(monkeypatch)
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)
    monkeypatch.setattr(messages_native.billing, "precheck_balance", AsyncMock(return_value=None))

    response = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token()}"},
        json={
            "model": "claude-sonnet-5",
            "metadata": {
                "project_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "run_id": _RUNNER_RUN_ID,
                "session_id": _RUNNER_SESSION_ID,
                "workspace_id": _RUNNER_WORKSPACE_ID,
                "fencing_epoch": 4,
                "cancel_epoch": 0,
                "message_id": _RUNNER_MESSAGE_ID,
            },
            "messages": [{"role": "user", "content": "check"}],
        },
    )

    assert response.status_code == 401
    assert "project_id" in response.json()["error"]["message"]


def test_project_cell_endpoint_requires_message_id_equal_to_jti(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_runner_auth(monkeypatch)
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)

    missing = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token()}"},
        json={
            "model": "claude-sonnet-5",
            "metadata": {
                "project_id": _RUNNER_PROJECT_ID,
                "run_id": _RUNNER_RUN_ID,
                "session_id": _RUNNER_SESSION_ID,
                "workspace_id": _RUNNER_WORKSPACE_ID,
                "fencing_epoch": 4,
                "cancel_epoch": 0,
            },
            "messages": [{"role": "user", "content": "check"}],
        },
    )
    wrong = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token()}"},
        json={
            "model": "claude-sonnet-5",
            "metadata": {
                "project_id": _RUNNER_PROJECT_ID,
                "run_id": _RUNNER_RUN_ID,
                "session_id": _RUNNER_SESSION_ID,
                "workspace_id": _RUNNER_WORKSPACE_ID,
                "fencing_epoch": 4,
                "cancel_epoch": 0,
                "message_id": "99999999-9999-4999-8999-999999999999",
            },
            "messages": [{"role": "user", "content": "check"}],
        },
    )

    assert missing.status_code == 401
    assert "message_id is required" in missing.json()["error"]["message"]
    assert wrong.status_code == 401
    assert "message_id" in wrong.json()["error"]["message"]


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"exp": int(time.time()) - 5, "nbf": int(time.time()) - 10}, "expired"),
        ({"nbf": int(time.time()) + 30, "exp": int(time.time()) + 60}, "not active"),
        ({"exp": int(time.time()) + 900, "nbf": int(time.time()) - 1}, "ttl exceeds"),
    ],
)
def test_project_cell_endpoint_rejects_invalid_token_lifetime(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    expected_message: str,
) -> None:
    _install_runner_auth(monkeypatch)
    monkeypatch.setenv("RUNNER_AUTH_MAX_TTL_SECONDS", "120")
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)

    response = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token(overrides=overrides)}"},
        json={
            "model": "claude-sonnet-5",
            "metadata": {
                "project_id": _RUNNER_PROJECT_ID,
                "run_id": _RUNNER_RUN_ID,
                "session_id": _RUNNER_SESSION_ID,
                "workspace_id": _RUNNER_WORKSPACE_ID,
                "fencing_epoch": 4,
                "cancel_epoch": 0,
            },
            "messages": [{"role": "user", "content": "check"}],
        },
    )

    assert response.status_code == 401
    assert expected_message in response.json()["error"]["message"]


def test_project_cell_endpoint_rejects_algorithm_confusion(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_runner_auth(monkeypatch)
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)

    response = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token(alg='HS512')}"},
        json={
            "model": "claude-sonnet-5",
            "metadata": {
                "project_id": _RUNNER_PROJECT_ID,
                "run_id": _RUNNER_RUN_ID,
                "session_id": _RUNNER_SESSION_ID,
                "workspace_id": _RUNNER_WORKSPACE_ID,
                "fencing_epoch": 4,
                "cancel_epoch": 0,
            },
            "messages": [{"role": "user", "content": "check"}],
        },
    )

    assert response.status_code == 401
    assert "algorithm" in response.json()["error"]["message"]


def test_project_cell_endpoint_rejects_wrong_issuer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_runner_auth(monkeypatch)
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)

    response = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token(overrides={'iss': 'wrong-issuer'})}"},
        json={
            "model": "claude-sonnet-5",
            "metadata": {
                "project_id": _RUNNER_PROJECT_ID,
                "run_id": _RUNNER_RUN_ID,
                "session_id": _RUNNER_SESSION_ID,
                "workspace_id": _RUNNER_WORKSPACE_ID,
                "fencing_epoch": 4,
                "cancel_epoch": 0,
                "message_id": _RUNNER_MESSAGE_ID,
            },
            "messages": [{"role": "user", "content": "check"}],
        },
    )

    assert response.status_code == 401
    assert "issuer" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"project_id": "not-a-uuid"}, "project_id"),
        ({"run_id": "not-a-uuid"}, "run_id"),
        ({"session_id": "not-a-uuid"}, "session_id"),
        ({"workspace_id": "not-a-uuid"}, "workspace_id"),
        ({"jti": "runner-msg-1"}, "jti"),
        ({"fencing_epoch": 0}, "fencing_epoch"),
        ({"cancel_epoch": -1}, "cancel_epoch"),
    ],
)
def test_project_cell_endpoint_rejects_invalid_identity_claims(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    expected_message: str,
) -> None:
    _install_runner_auth(monkeypatch)
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)

    response = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token(overrides=overrides)}"},
        json={
            "model": "claude-sonnet-5",
            "metadata": {
                "project_id": _RUNNER_PROJECT_ID,
                "run_id": _RUNNER_RUN_ID,
                "session_id": _RUNNER_SESSION_ID,
                "workspace_id": _RUNNER_WORKSPACE_ID,
                "fencing_epoch": 4,
                "cancel_epoch": 0,
            },
            "messages": [{"role": "user", "content": "check"}],
        },
    )

    assert response.status_code == 401
    assert expected_message in response.json()["error"]["message"]


def test_project_cell_endpoint_rejects_duplicate_jti_before_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_redis = _install_runner_auth(monkeypatch, redis_results=[True, False])
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda: ("test-key", "https://api.llmgw.ru/v1"),
    )
    calls = {"n": 0}

    def fake_post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        _ = url, payload, headers
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-cell-dup",
                "choices": [{"finish_reason": "stop", "message": {"content": "ok", "tool_calls": []}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    monkeypatch.setattr(messages_native, "_post_llmgw", fake_post)
    payload = {
        "model": "claude-sonnet-5",
        "metadata": {
            "project_id": _RUNNER_PROJECT_ID,
            "run_id": _RUNNER_RUN_ID,
            "session_id": _RUNNER_SESSION_ID,
            "workspace_id": _RUNNER_WORKSPACE_ID,
            "fencing_epoch": 4,
            "cancel_epoch": 0,
            "message_id": _RUNNER_MESSAGE_ID,
        },
        "messages": [{"role": "user", "content": "check"}],
    }

    first = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token()}"},
        json=payload,
    )
    second = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token()}"},
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 401
    assert "already used" in second.json()["error"]["message"]
    assert calls["n"] == 1
    assert fake_redis.calls[0]["nx"] is True
    assert fake_redis.calls[0]["ex"] == 60


def test_project_cell_endpoint_fails_closed_when_replay_fence_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_runner_auth(monkeypatch, redis_error=RuntimeError("redis down"))
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)

    response = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token()}"},
        json={
            "model": "claude-sonnet-5",
            "metadata": {
                "project_id": _RUNNER_PROJECT_ID,
                "run_id": _RUNNER_RUN_ID,
                "session_id": _RUNNER_SESSION_ID,
                "workspace_id": _RUNNER_WORKSPACE_ID,
                "fencing_epoch": 4,
                "cancel_epoch": 0,
                "message_id": _RUNNER_MESSAGE_ID,
            },
            "messages": [{"role": "user", "content": "check"}],
        },
    )

    assert response.status_code == 503
    assert "replay fence unavailable" in response.json()["error"]["message"]


def test_project_cell_endpoint_calls_upstream_with_valid_runner_identity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_redis = _install_runner_auth(monkeypatch)
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda: ("test-key", "https://api.llmgw.ru/v1"),
    )
    precheck = AsyncMock(return_value=None)
    charge = AsyncMock(return_value=UUID("55555555-5555-5555-5555-555555555555"))
    monkeypatch.setattr(messages_native.billing, "precheck_balance", precheck)
    monkeypatch.setattr(messages_native.billing, "charge", charge)
    monkeypatch.setattr(messages_native.file_logger, "log_request", lambda payload: None)
    captured: dict[str, Any] = {}

    def fake_post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-cell-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ok", "tool_calls": []},
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    monkeypatch.setattr(messages_native, "_post_llmgw", fake_post)

    response = client.post(
        "/v1/project-cell/messages",
        headers={"Authorization": f"Bearer {_runner_token()}"},
        json={
            "model": "claude-sonnet-5",
            "metadata": {
                "project_id": _RUNNER_PROJECT_ID,
                "run_id": _RUNNER_RUN_ID,
                "session_id": _RUNNER_SESSION_ID,
                "workspace_id": _RUNNER_WORKSPACE_ID,
                "fencing_epoch": 4,
                "cancel_epoch": 0,
                "message_id": _RUNNER_MESSAGE_ID,
                "stage": "native_agent",
            },
            "messages": [{"role": "user", "content": "check"}],
        },
    )

    assert response.status_code == 200
    assert captured["url"] == "https://api.llmgw.ru/v1/chat/completions"
    assert captured["payload"]["model"] == "anthropic/claude-sonnet-5"
    precheck.assert_not_awaited()
    charge.assert_not_awaited()
    metadata = response.json()["metadata"]
    assert metadata["message_id"] == _RUNNER_MESSAGE_ID
    assert metadata["run_id"] == _RUNNER_RUN_ID
    assert metadata["session_id"] == _RUNNER_SESSION_ID
    assert metadata["workspace_id"] == _RUNNER_WORKSPACE_ID
    assert metadata["fencing_epoch"] == 4
    assert metadata["cancel_epoch"] == 0
    assert fake_redis.calls[0]["name"].endswith(_RUNNER_MESSAGE_ID)


def test_legacy_v1_messages_remains_auth_free(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNNER_AUTH_SECRET", _RUNNER_SECRET)
    monkeypatch.setenv("RUNNER_AUTH_ISSUER", _RUNNER_ISSUER)
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda: ("test-key", "https://api.llmgw.ru/v1"),
    )

    def fake_post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-legacy-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ok", "tool_calls": []},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    monkeypatch.setattr(messages_native, "_post_llmgw", fake_post)

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "still works"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["content"][0]["text"] == "ok"

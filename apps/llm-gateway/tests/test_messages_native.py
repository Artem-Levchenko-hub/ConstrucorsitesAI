"""Anthropic-shaped native-agent adapter over llmgw OpenAI tool calling."""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, Mock
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("invalid_request", "invalid_request"),
        ("auth.failed", "auth.failed"),
        ("credential-like value", ""),
        ("x" * 81, ""),
    ],
)
def test_upstream_error_type_is_safe_for_logs(raw: str, expected: str) -> None:
    assert messages_native._safe_upstream_error_type(raw) == expected


@pytest.fixture(autouse=True)
def open_native_run_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native endpoint tests run without Postgres unless budget behaviour is under test."""
    monkeypatch.setattr(
        messages_native.billing,
        "reserve_native_run_request",
        AsyncMock(
            return_value=messages_native.billing.NativeRunReservation(
                usage_id=UUID("99999999-9999-9999-9999-999999999999"),
                requests_before=0,
                cost_rub_before=messages_native.Decimal("0"),
                provider_cost_usd_before=messages_native.Decimal("0"),
            )
        ),
    )
    monkeypatch.setattr(
        messages_native.billing,
        "release_native_run_reservation",
        AsyncMock(return_value=None),
    )


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

    adapted = messages_native._anthropic_response(upstream, "claude-sonnet-5")

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


def test_native_endpoint_uses_sonnet_chat_tools(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda _model: ("test-key", "https://api.llmgw.ru/v1"),
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
    monkeypatch.setattr(
        messages_native.billing,
        "charge",
        AsyncMock(return_value=UUID("55555555-5555-5555-5555-555555555555")),
    )

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "max_tokens": 128,
            "user": "11111111-1111-1111-1111-111111111111",
            "metadata": {
                "run_id": "33333333-3333-3333-3333-333333333333",
                "free": True,
            },
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
    assert captured["json"]["model"] == "anthropic/claude-sonnet-5"
    assert captured["json"]["tools"][0]["function"]["name"] == "done"
    assert response.json()["content"][0] == {
        "type": "tool_use",
        "id": "toolu_done",
        "name": "done",
        "input": {"summary": "ok"},
    }
    assert messages_native.billing.charge.await_args.kwargs[
        "provider_cost_usd"
    ] == messages_native._reserve_native_provider_cost("claude-sonnet-5", captured["json"])


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
        lambda _model: ("test-key", "https://api.aitunnel.ru/v1"),
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
            "model": "claude-sonnet-5",
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
    assert kwargs["reserved_usage_id"] == UUID("99999999-9999-9999-9999-999999999999")
    reserve_kwargs = messages_native.billing.reserve_native_run_request.await_args.kwargs
    assert reserve_kwargs["reserved_cost_rub"] > Decimal("0")
    assert reserve_kwargs["reserved_provider_cost_usd"] > Decimal("0")
    assert reserve_kwargs["reserved_provider_cost_usd"] != Decimal("0.35")
    assert reserve_kwargs["max_requests"] == 160
    assert reserve_kwargs["max_cost_rub"] == Decimal("5000.0")
    assert reserve_kwargs["max_provider_cost_usd"] == Decimal("10.0")


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
        lambda _model: ("test-key", "https://api.aitunnel.ru/v1"),
    )

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
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


def test_native_endpoint_stops_free_run_before_provider_when_run_budget_is_reached(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reserve = AsyncMock(
        side_effect=messages_native.billing.RunBudgetExceededError("run budget exhausted")
    )
    upstream = pytest.fail
    monkeypatch.setattr(messages_native.billing, "reserve_native_run_request", reserve)
    monkeypatch.setattr(messages_native, "_post_llmgw", upstream)
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda _model: ("test-key", "https://api.aitunnel.ru/v1"),
    )

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "user": "11111111-1111-1111-1111-111111111111",
            "metadata": {
                "run_id": "33333333-3333-3333-3333-333333333333",
                "free": True,
            },
            "messages": [{"role": "user", "content": "do not spend again"}],
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["type"] == "run_budget_exhausted"
    reserve.assert_awaited_once()


def test_native_endpoint_fails_closed_when_run_accounting_is_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        messages_native.billing,
        "reserve_native_run_request",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda _model: ("test-key", "https://api.aitunnel.ru/v1"),
    )

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "user": "11111111-1111-1111-1111-111111111111",
            "metadata": {"run_id": "33333333-3333-3333-3333-333333333333"},
            "messages": [{"role": "user", "content": "do not bypass accounting"}],
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "billing_unavailable"


def test_native_endpoint_releases_reservation_after_explicit_upstream_rejection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = AsyncMock(return_value=None)
    warning = Mock()
    monkeypatch.setattr(messages_native.billing, "release_native_run_reservation", release)
    monkeypatch.setattr(messages_native.log, "warning", warning)
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda _model: ("test-key", "https://api.aitunnel.ru/v1"),
    )
    monkeypatch.setattr(
        messages_native,
        "_post_llmgw",
        lambda *_args, **_kwargs: httpx.Response(
            400,
            json={"error": {"message": "payload rejected", "type": "invalid_request"}},
        ),
    )
    monkeypatch.setattr(messages_native.billing, "charge", AsyncMock())

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "user": "11111111-1111-1111-1111-111111111111",
            "metadata": {
                "run_id": "33333333-3333-3333-3333-333333333333",
                "free": True,
            },
            "messages": [{"role": "user", "content": "invalid upstream payload"}],
        },
    )

    assert response.status_code == 400
    release.assert_awaited_once_with(UUID("99999999-9999-9999-9999-999999999999"))
    messages_native.billing.charge.assert_not_awaited()
    warning.assert_called_once_with(
        "native_messages.upstream_rejected",
        run_id="33333333-3333-3333-3333-333333333333",
        status_code=400,
        upstream_error_type="invalid_request",
    )


def test_native_endpoint_keeps_reservation_after_ambiguous_upstream_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = AsyncMock(return_value=None)
    monkeypatch.setattr(messages_native.billing, "release_native_run_reservation", release)
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda _model: ("test-key", "https://api.aitunnel.ru/v1"),
    )
    monkeypatch.setattr(
        messages_native,
        "_post_llmgw",
        lambda *_args, **_kwargs: httpx.Response(
            503,
            json={"error": {"message": "upstream unavailable"}},
        ),
    )

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "user": "11111111-1111-1111-1111-111111111111",
            "metadata": {
                "run_id": "33333333-3333-3333-3333-333333333333",
                "free": True,
            },
            "messages": [{"role": "user", "content": "ambiguous failure"}],
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "paid_call_ambiguous"
    release.assert_not_awaited()


def test_native_endpoint_marks_post_provider_settlement_failure_as_ambiguous(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda _model: ("test-key", "https://api.aitunnel.ru/v1"),
    )
    monkeypatch.setattr(
        messages_native,
        "_post_llmgw",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            json={
                "id": "provider-completed-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "completed", "tool_calls": []},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        ),
    )
    monkeypatch.setattr(
        messages_native.billing,
        "charge",
        AsyncMock(side_effect=RuntimeError("database unavailable after provider success")),
    )

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "user": "11111111-1111-1111-1111-111111111111",
            "metadata": {
                "run_id": "33333333-3333-3333-3333-333333333333",
                "free": True,
            },
            "messages": [{"role": "user", "content": "one paid attempt only"}],
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "paid_call_ambiguous"


def test_native_endpoint_marks_post_provider_wallet_race_as_ambiguous(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda _model: ("test-key", "https://api.aitunnel.ru/v1"),
    )
    monkeypatch.setattr(
        messages_native,
        "_post_llmgw",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            json={
                "id": "provider-completed-wallet-race",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "completed", "tool_calls": []},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        ),
    )
    monkeypatch.setattr(
        messages_native.billing,
        "charge",
        AsyncMock(side_effect=WalletEmptyError("wallet changed after provider call")),
    )

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "user": "11111111-1111-1111-1111-111111111111",
            "metadata": {
                "run_id": "33333333-3333-3333-3333-333333333333",
                "free": True,
            },
            "messages": [{"role": "user", "content": "one paid attempt only"}],
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "paid_call_ambiguous"


@pytest.mark.parametrize(
    ("user", "run_id"),
    [
        (None, "33333333-3333-3333-3333-333333333333"),
        ("11111111-1111-1111-1111-111111111111", None),
        ("not-a-uuid", "33333333-3333-3333-3333-333333333333"),
        ("11111111-1111-1111-1111-111111111111", "not-a-uuid"),
    ],
)
def test_native_endpoint_blocks_unattributed_calls_before_provider(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    user: str | None,
    run_id: str | None,
) -> None:
    reserve = AsyncMock()
    monkeypatch.setattr(messages_native.billing, "reserve_native_run_request", reserve)
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda _model: ("test-key", "https://api.aitunnel.ru/v1"),
    )
    metadata = {"free": True}
    if run_id is not None:
        metadata["run_id"] = run_id

    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5",
            "user": user,
            "metadata": metadata,
            "messages": [{"role": "user", "content": "must be attributed"}],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    reserve.assert_not_awaited()


def test_native_product_timeout_outlives_observed_four_minute_response() -> None:
    timeout = messages_native._UPSTREAM_TIMEOUT

    assert timeout.connect == 30.0
    assert timeout.write == 60.0
    assert timeout.pool == 30.0
    assert timeout.read == 600.0

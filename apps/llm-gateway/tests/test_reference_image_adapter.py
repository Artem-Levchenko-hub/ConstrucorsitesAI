import ast
import base64
from pathlib import Path
from typing import Any

import pytest

from yleum_gateway.routers.messages_native import _openai_messages


def test_incremental_cache_breakpoint_survives_reference_image_adapter():
    # Execute the actual pure API helper without importing the API runtime into
    # the independently packaged gateway test environment.
    source = Path(__file__).resolve().parents[2] / "api/src/yleum_api/services/agent_native.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if (
        isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id == "_CACHE"
    ) or (isinstance(node, ast.FunctionDef) and node.name == "_with_incremental_cache")]
    assert len(nodes) == 2
    namespace = {"Any": Any}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    data = base64.b64encode(b"synthetic-image").decode()
    original = [{"role": "user", "content": [
        {"type": "text", "text": "Use this image"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
    ]}]
    prepared = namespace["_with_incremental_cache"](original)
    assert "cache_control" not in original[0]["content"][-1]
    assert prepared[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    adapted = _openai_messages({"messages": prepared})[0]["content"]
    assert adapted[-1]["type"] == "image_url"
    assert adapted[-1]["cache_control"] == {"type": "ephemeral"}
    assert adapted[-1]["cache_control"] is not prepared[0]["content"][-1]["cache_control"]


def test_native_reference_preserves_image_text_and_cache():
    data = base64.b64encode(b"validated-private-image").decode()
    result = _openai_messages({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "Use this layout", "cache_control": {"type": "ephemeral"}},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
    ]}]})
    assert result[0]["content"] == [
        {"type": "text", "text": "Use this layout", "cache_control": {"type": "ephemeral"}},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
    ]


@pytest.mark.parametrize("source", [
    {"type": "url", "url": "http://127.0.0.1/private"},
    {"type": "base64", "media_type": "image/svg+xml", "data": "PHN2Zy8+"},
    {"type": "base64", "media_type": "image/png", "data": "not-base64"},
])
def test_native_reference_rejects_unsafe_image_source(source):
    with pytest.raises(ValueError, match="image"):
        _openai_messages({"messages": [{"role": "user", "content": [
            {"type": "image", "source": source},
        ]}]})


@pytest.mark.asyncio
async def test_native_admitted_fallback_preserves_private_bytes_and_tools(monkeypatch):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import httpx
    from structlog.testing import capture_logs

    from yleum_gateway.routers import messages_native as native

    data = base64.b64encode(b"unique-private-reference-payload").decode()
    messages = _openai_messages({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "Use reference", "cache_control": {"type": "ephemeral"}},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
    ]}]})
    payload = {"messages": messages, "tools": [{"type": "function", "function": {"name": "read_file"}}]}
    sent = []
    monkeypatch.setattr(native.text_failover, "select_scope", AsyncMock(return_value="llmgw"))
    monkeypatch.setattr(native.text_failover, "credentials", lambda scope: ("synthetic", "https://backup.test/v1"))
    monkeypatch.setattr(native.text_failover, "overload_candidate", lambda response, model: {"confirmed": True})
    monkeypatch.setattr(native.generation_attempts, "dispatch", AsyncMock(return_value=uuid4()))
    monkeypatch.setattr(native, "_finish_provider_call", AsyncMock(return_value=True))

    def post(url, body, headers, **kwargs):
        sent.append(body)
        return httpx.Response(503 if len(sent) == 1 else 200, json={})

    monkeypatch.setattr(native, "_post_llmgw", post)
    with capture_logs() as logs:
        result = await native._dispatch_native_provider(identity=None, model="claude-sonnet-5.5",
            payload=payload, primary_route=("synthetic", "https://primary.test/v1"), retry_count=0,
            dispatch_options={"run_id": None, "project_id": None, "message_id": None, "stage": "build_plan"})
    assert isinstance(result, tuple) and result[2] == "aitunnel"
    assert len(sent) == 2
    assert sent[0]["messages"] == sent[1]["messages"] == messages
    assert sent[0]["tools"] == sent[1]["tools"] == payload["tools"]
    assert data not in str(logs)


def test_wallet_estimate_counts_visual_budget_not_base64_length(monkeypatch):
    from decimal import Decimal

    from yleum_gateway.routers import messages_native as native
    monkeypatch.setattr(native, "calculate_cost_rub", lambda model, input_tokens, output_tokens: Decimal(input_tokens))
    estimates = []
    for size in [100, 1_000_000]:
        estimates.append(native._estimate_native_cost("claude-sonnet-5.5", {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "a" * size}}]}],
        }))
    assert estimates[0] == estimates[1]
    assert 4784 <= estimates[0] < 5000


def test_legacy_wallet_estimate_includes_same_visual_budget(monkeypatch):
    from decimal import Decimal

    from yleum_gateway.routers import chat
    from yleum_gateway.services import token_counter
    monkeypatch.setattr(token_counter, "count_text_tokens", lambda model, text: len(text) // 4)
    monkeypatch.setattr(chat, "calculate_cost_rub", lambda model, tokens_in, tokens_out: Decimal(tokens_in))
    plain = [{"role": "user", "content": "Reference"}]
    visual = [{"role": "user", "content": [{"type": "text", "text": "Reference"},
              {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "a" * 1000}}]}]
    assert chat._estimate_cost("claude-sonnet-5.5", visual) - chat._estimate_cost(
        "claude-sonnet-5.5", plain) == 4784


@pytest.mark.asyncio
async def test_legacy_final_payload_size_rejected_before_provider(monkeypatch):
    from yleum_gateway.core.errors import ValidationFailedError
    from yleum_gateway.providers import llmgw
    monkeypatch.setattr(llmgw, "_key_and_url", lambda *args: ("synthetic", "https://provider.test/v1"))
    with pytest.raises(ValidationFailedError, match="request.*size"):
        await llmgw.acompletion(model="claude-sonnet-5.5", messages=[
            {"role": "user", "content": "x" * 32_000_001},
        ])


@pytest.mark.asyncio
async def test_native_final_payload_size_rejected_before_attempt(monkeypatch):
    from unittest.mock import AsyncMock

    from yleum_gateway.routers import messages_native as native
    monkeypatch.setattr(native.text_failover, "select_scope", AsyncMock(return_value="llmgw"))
    dispatch = AsyncMock()
    monkeypatch.setattr(native.generation_attempts, "dispatch", dispatch)
    result = await native._dispatch_native_provider(identity=None, model="claude-sonnet-5.5",
        payload={"messages": [{"role": "user", "content": "x" * 32_000_001}]},
        primary_route=("synthetic", "https://primary.test"), retry_count=0,
        dispatch_options={"run_id": None, "project_id": None, "message_id": None, "stage": "build_plan"})
    assert result.status_code == 400
    dispatch.assert_not_awaited()

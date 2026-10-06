"""Provider journal admission and settlement at the public chat boundary."""

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from yleum_gateway.core.errors import BillingReconciliationRequiredError, UpstreamProviderError
from yleum_gateway.main import create_app
from yleum_gateway.routers import chat
from yleum_gateway.services import provider_calls, streaming

MODEL = "gemini-3.1-pro-preview-customtools"


@pytest.fixture
def journal(monkeypatch, neutralize_side_effects):
    start = AsyncMock(return_value=uuid4())
    finish = AsyncMock()
    monkeypatch.setattr(provider_calls, "start_call", start)
    monkeypatch.setattr(provider_calls, "finish_call", finish)
    return start, finish


@pytest.fixture
def client(neutralize_lifespan, journal):
    with TestClient(create_app()) as result:
        yield result


def response():
    return {"id": "receipt-id", "model": MODEL,
            "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                      "prompt_cache_hit_tokens": 20, "cache_creation_input_tokens": 5,
                      "cost_rub": "0.5", "cost_usd": "0.01"},
            "choices": [{"message": {"content": "answer"}}]}


def post(client, **extra):
    return client.post("/v1/chat/completions", json={
        "model": MODEL, "messages": [{"role": "user", "content": "hi"}], **extra,
    })


@pytest.mark.parametrize("user", [None, "paid", "free"])
def test_chat_journal_before_billing_for_every_expense_owner(client, journal, monkeypatch, user):
    start, finish = journal

    async def upstream(**kwargs):
        start.assert_awaited_once()
        return response()

    async def charge(**kwargs):
        finish.assert_awaited_once()

    monkeypatch.setattr(chat.router_module, "acompletion", AsyncMock(side_effect=upstream))
    chat.billing.charge.side_effect = charge
    result = post(client, **({"user": str(uuid4()), "metadata": {"free": user == "free"}}
                            if user else {}))
    assert result.status_code == 200
    start.assert_awaited_once()
    assert start.await_args.kwargs["free"] is (user == "free")
    assert (start.await_args.kwargs["user_id"] is None) is (user is None)
    assert start.await_args.kwargs["route"] == "/v1/chat/completions"
    finish.assert_awaited_once()
    receipt = finish.await_args.kwargs
    assert receipt["status"] == "completed"
    assert receipt["provider_request_id"] == "receipt-id"
    assert receipt["tokens_in"] == 100
    assert receipt["cache_read_tokens"] == 20
    assert receipt["cache_write_tokens"] == 5
    assert receipt["provider_cost_rub"] == Decimal("0.5")
    assert receipt["provider_cost_usd"] == Decimal("0.01")
    assert receipt["calculated_cost_rub"] == Decimal(receipt["cost_provenance"]["calculated_cost_rub"])
    assert chat.billing.charge.await_count == int(user is not None)


def test_cache_replay_has_no_provider_attempt(client, journal):
    chat.cache.get.return_value = response()
    assert post(client).status_code == 200
    journal[0].assert_not_awaited()
    journal[1].assert_not_awaited()


@pytest.mark.parametrize("failure", ["start", "finish"])
def test_journal_failure_blocks_upstream_or_billing(client, journal, monkeypatch, failure):
    journal[failure == "finish"].side_effect = BillingReconciliationRequiredError("unavailable")
    upstream = AsyncMock(return_value=response())
    monkeypatch.setattr(chat.router_module, "acompletion", upstream)
    result = post(client, user=str(uuid4()))
    assert result.status_code == 503
    assert result.json()["detail"]["error"]["code"] == "billing_reconciliation_required"
    assert upstream.await_count == int(failure == "finish")
    chat.billing.charge.assert_not_awaited()
    assert journal[1].await_count == int(failure == "finish")


@pytest.mark.parametrize("ambiguous", [False, True])
def test_transport_failure_retains_safe_class_only(client, journal, monkeypatch, ambiguous):
    monkeypatch.setattr(chat.router_module, "acompletion", AsyncMock(side_effect=UpstreamProviderError(
        "private payload", details={"provider_charge_ambiguous": ambiguous},
    )))
    assert post(client).status_code == 502
    receipt = journal[1].await_args.kwargs
    assert receipt["status"] == ("ambiguous" if ambiguous else "failed")
    assert receipt["error_type"] == "UpstreamProviderError"
    assert "private payload" not in repr(receipt)
    assert "calculated_cost_rub" not in receipt


@pytest.mark.parametrize("mode", ["complete", "lost_done", "partial", "connect"])
async def test_stream_receipt_finishes_once_before_charge(journal, monkeypatch, mode):
    async def upstream(model, messages, *, receipt, **kwargs):
        journal[0].assert_awaited_once()
        if mode == "connect":
            raise UpstreamProviderError("connection", details={"provider_charge_ambiguous": False})
        receipt.update({"id": "receipt-id", "model": MODEL})
        if mode != "partial":
            receipt["usage"] = response()["usage"]
        yield "answer", model
        if mode != "complete":
            raise UpstreamProviderError("transport", details={"provider_charge_ambiguous": True})

    async def charge(**kwargs):
        journal[1].assert_awaited_once()

    monkeypatch.setattr(streaming.llmgw, "astream", upstream)
    chat.billing.charge.side_effect = charge
    result = [event async for event in streaming.stream_completion(
        request=None, model=MODEL, messages=[{"role": "user", "content": "hi"}],
        user_id=uuid4(), project_id=None, message_id=None, temperature=None, max_tokens=None,
    )]
    journal[1].assert_awaited_once()
    receipt = journal[1].await_args.kwargs
    assert receipt["status"] == {"complete": "completed", "lost_done": "completed",
                                 "partial": "ambiguous", "connect": "failed"}[mode]
    assert ("[DONE]" in result) is (mode == "complete")
    if mode == "partial":
        assert receipt["cost_provenance"]["basis"] == "local_token_estimate"
    if mode == "connect":
        chat.billing.charge.assert_not_awaited()
        assert "calculated_cost_rub" not in receipt


@pytest.mark.parametrize("stream", [False, True])
def test_unknown_provider_model_is_not_journaled(client, journal, stream):
    assert post(client, model="another-provider-model", stream=stream).status_code == 404
    journal[0].assert_not_awaited()


def test_stream_admission_failure_returns_503_before_opening_sse(client, journal, monkeypatch):
    journal[0].side_effect = BillingReconciliationRequiredError("unavailable")
    upstream = AsyncMock()
    monkeypatch.setattr(streaming.llmgw, "astream", upstream)
    assert post(client, stream=True).status_code == 503
    upstream.assert_not_called()
    journal[1].assert_not_awaited()


async def test_stream_finish_failure_no_charge_or_done_or_second_write(journal, monkeypatch):
    async def upstream(model, messages, *, receipt, **kwargs):
        receipt.update(response())
        yield "answer", model

    journal[1].side_effect = BillingReconciliationRequiredError("unavailable")
    monkeypatch.setattr(streaming.llmgw, "astream", upstream)
    result = [event async for event in streaming.stream_completion(
        request=None, model=MODEL, messages=[{"role": "user", "content": "hi"}],
        user_id=uuid4(), project_id=None, message_id=None, temperature=None, max_tokens=None,
    )]
    journal[1].assert_awaited_once()
    assert "[DONE]" not in result
    assert json.loads(result[-1])["error"]["code"] == "billing_reconciliation_required"
    chat.billing.charge.assert_not_awaited()


async def test_disconnect_during_receipt_write_waits_for_same_settlement(journal, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    upstream_calls = 0

    async def upstream(model, messages, *, receipt, **kwargs):
        nonlocal upstream_calls
        upstream_calls += 1
        receipt.update(response())
        yield "answer", model

    async def finish(*args, **kwargs):
        entered.set()
        await release.wait()

    monkeypatch.setattr(streaming.llmgw, "astream", upstream)
    journal[1].side_effect = finish
    stream = streaming.stream_completion(
        request=None, model=MODEL, messages=[{"role": "user", "content": "hi"}],
        user_id=uuid4(), project_id=None, message_id=None, temperature=None, max_tokens=None,
    )
    await anext(stream)
    task = asyncio.create_task(anext(stream))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    chat.billing.charge.assert_not_awaited()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    journal[1].assert_awaited_once()
    chat.billing.charge.assert_awaited_once()
    assert upstream_calls == 1


async def test_headers_without_receipt_do_not_invent_zero_expense(journal, monkeypatch):
    async def upstream(model, messages, *, receipt, **kwargs):
        receipt["provider_request_id"] = "accepted-id"
        receipt["headers"] = {"x-llmgw-request-id": "accepted-id"}
        raise UpstreamProviderError("transport", details={"provider_charge_ambiguous": True})
        yield  # pragma: no cover

    monkeypatch.setattr(streaming.llmgw, "astream", upstream)
    result = [event async for event in streaming.stream_completion(
        request=None, model=MODEL, messages=[{"role": "user", "content": "hi"}],
        user_id=uuid4(), project_id=None, message_id=None, temperature=None, max_tokens=None,
    )]
    assert "[DONE]" not in result
    receipt = journal[1].await_args.kwargs
    assert receipt["status"] == "ambiguous"
    assert receipt["provider_request_id"] == "accepted-id"
    assert "calculated_cost_rub" not in receipt
    chat.billing.charge.assert_not_awaited()


@pytest.mark.parametrize("phase", ["pricing", "finish"])
@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancel_received_chat_response_preserves_same_receipt(journal, monkeypatch, phase, cancellations):
    entered, release = asyncio.Event(), asyncio.Event()
    saved = []
    upstream = AsyncMock(return_value=response())
    monkeypatch.setattr(chat.router_module, "acompletion", upstream)
    original_pricing = chat.resolve_request_cost

    async def pricing(*args, **kwargs):
        if phase == "pricing":
            entered.set()
            await release.wait()
        return await original_pricing(*args, **kwargs)

    async def finish(*args, **kwargs):
        if phase == "finish":
            entered.set()
            await release.wait()
        saved.append(kwargs)

    monkeypatch.setattr(chat, "resolve_request_cost", pricing)
    journal[1].side_effect = finish
    req = chat.ChatCompletionRequest(
        model=MODEL, messages=[chat.ChatMessage(role="user", content="hi")], user=uuid4(),
    )
    task = asyncio.create_task(chat.chat_completions(req, None))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    if cancellations == 2:
        task.cancel()
        await asyncio.sleep(0)
    assert not task.done()
    chat.billing.charge.assert_not_awaited()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    upstream.assert_awaited_once()
    journal[1].assert_awaited_once()
    assert len(saved) == 1
    assert saved[0]["status"] == "completed"
    assert saved[0]["provider_request_id"] == "receipt-id"
    assert saved[0]["provider_cost_rub"] == Decimal("0.5")
    assert saved[0]["tokens_in"] == 100
    chat.billing.charge.assert_awaited_once()


@pytest.mark.parametrize("failure", ["pricing", "unknown_model", "invalid_usage"])
@pytest.mark.parametrize("money", ["valid", "malformed"])
def test_chat_failed_settlement_keeps_safe_provider_evidence(client, journal, monkeypatch, failure, money):
    data = response()
    data["_provider_headers"] = {"x-llmgw-request-id": "canonical-receipt"}
    data["usage"].update(cost_rub="2.50" if money == "valid" else "NaN",
                         cost_usd=".025" if money == "valid" else {"secret": "unsafe"})
    if failure == "unknown_model":
        data["model"] = "unknown/provider-model"
    elif failure == "invalid_usage":
        data["usage"]["prompt_tokens"] = {"invalid": 1}
    else:
        monkeypatch.setattr(chat, "resolve_request_cost", AsyncMock(side_effect=ValueError("pricing")))
    monkeypatch.setattr(chat.router_module, "acompletion", AsyncMock(return_value=data))
    result = post(client, user=str(uuid4()))
    assert result.status_code >= 400
    journal[1].assert_awaited_once()
    saved = journal[1].await_args.kwargs
    assert saved["status"] == "ambiguous"
    assert saved["provider_request_id"] == "canonical-receipt"
    assert saved["provider_cost_rub"] == (Decimal("2.50") if money == "valid" else None)
    assert saved["provider_cost_usd"] == (Decimal(".025") if money == "valid" else None)
    assert "unsafe" not in repr(saved)
    chat.billing.charge.assert_not_awaited()


@pytest.mark.parametrize("failure", ["pricing", "unknown_model"])
@pytest.mark.parametrize("money", ["valid", "malformed"])
async def test_stream_failed_settlement_keeps_safe_provider_evidence(journal, monkeypatch, failure, money):
    async def upstream(model, messages, *, receipt, **kwargs):
        receipt.update(response())
        receipt["headers"] = {"x-llmgw-request-id": "canonical-stream"}
        receipt["usage"].update(cost_rub="2.50" if money == "valid" else "Infinity",
                                cost_usd=".025" if money == "valid" else True)
        if failure == "unknown_model":
            receipt["model"] = "unknown/provider-model"
        yield "answer", model
    if failure == "pricing":
        monkeypatch.setattr(streaming, "resolve_request_cost", AsyncMock(side_effect=ValueError("pricing")))
    monkeypatch.setattr(streaming.llmgw, "astream", upstream)
    result = [event async for event in streaming.stream_completion(
        request=None, model=MODEL, messages=[{"role": "user", "content": "hi"}],
        user_id=uuid4(), project_id=None, message_id=None, temperature=None, max_tokens=None,
    )]
    assert "[DONE]" not in result
    journal[1].assert_awaited_once()
    saved = journal[1].await_args.kwargs
    assert saved["status"] == "ambiguous"
    assert saved["provider_request_id"] == "canonical-stream"
    assert saved["provider_cost_rub"] == (Decimal("2.50") if money == "valid" else None)
    assert saved["provider_cost_usd"] == (Decimal(".025") if money == "valid" else None)
    chat.billing.charge.assert_not_awaited()


@pytest.mark.parametrize("bad_id", ["bad\nidentifier", "x" * 513, True, {"private": "payload"}])
def test_failed_chat_sanitizes_bad_id_without_losing_money(client, journal, monkeypatch, bad_id):
    data = response()
    data["id"] = bad_id
    monkeypatch.setattr(chat.router_module, "acompletion", AsyncMock(return_value=data))
    monkeypatch.setattr(chat, "resolve_request_cost", AsyncMock(side_effect=ValueError("pricing")))
    assert post(client).status_code == 503
    saved = journal[1].await_args.kwargs
    assert saved["provider_request_id"] is None
    assert saved["provider_cost_rub"] == Decimal(".5")

"""Public mini-app inference must not inherit generation's fail-open billing."""

from collections.abc import Iterator
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from omnia_gateway.core.errors import WalletEmptyError
from omnia_gateway.main import create_app
from omnia_gateway.routers import chat


@pytest.fixture
def client(neutralize_lifespan: None, neutralize_side_effects: None) -> Iterator[TestClient]:
    with TestClient(create_app()) as value:
        yield value


def payload() -> dict:
    return {
        "model": "gemini-3.1-pro-preview-customtools",
        "messages": [{"role": "user", "content": "hello"}],
        "user": str(uuid4()),
        "metadata": {"project_id": str(uuid4()), "free": False, "require_billing": True},
    }


def completion() -> dict:
    return {
        "model": "gemini-3.1-pro-preview-customtools",
        "choices": [{"message": {"role": "assistant", "content": "paid answer"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.mark.parametrize("invalid", ["missing_user", "free", "stream"])
def test_required_billing_rejects_unbillable_request(client, monkeypatch, invalid):
    body = payload()
    if invalid == "missing_user":
        body.pop("user")
    elif invalid == "free":
        body["metadata"]["free"] = True
    else:
        body["stream"] = True
    upstream = AsyncMock(return_value=completion())
    monkeypatch.setattr(chat.router_module, "acompletion", upstream)
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 422
    upstream.assert_not_awaited()
    chat.cache.get.assert_not_awaited()
    chat.billing.precheck_balance.assert_not_awaited()


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("failure", ["database", "empty_wallet"])
def test_required_precheck_fails_closed_even_with_cached_answer(client, monkeypatch, cached, failure):
    error = RuntimeError("private-db-error") if failure == "database" else WalletEmptyError("empty")
    monkeypatch.setattr(chat.billing, "precheck_balance", AsyncMock(side_effect=error))
    monkeypatch.setattr(chat.cache, "get", AsyncMock(return_value=completion() if cached else None))
    upstream = AsyncMock(return_value=completion())
    monkeypatch.setattr(chat.router_module, "acompletion", upstream)
    response = client.post("/v1/chat/completions", json=payload())
    assert response.status_code == (503 if failure == "database" else 402)
    assert "paid answer" not in response.text
    assert "private-db-error" not in response.text
    upstream.assert_not_awaited()
    chat.billing.charge.assert_not_awaited()
    chat.cache.set.assert_not_awaited()


def test_required_charge_failure_does_not_return_or_cache_answer(client, monkeypatch):
    monkeypatch.setattr(chat.router_module, "acompletion", AsyncMock(return_value=completion()))
    monkeypatch.setattr(chat.billing, "charge", AsyncMock(side_effect=RuntimeError("private-db-error")))
    response = client.post("/v1/chat/completions", json=payload())
    assert response.status_code == 503
    assert "paid answer" not in response.text
    assert "private-db-error" not in response.text
    chat.cache.set.assert_not_awaited()


def test_required_billing_success_debits_owner_before_cache(client, monkeypatch):
    body = payload()
    order = []
    async def check(*args):
        order.append("check")
    async def generate(**kwargs):
        order.append("upstream")
        return completion()
    async def charge(**kwargs):
        order.append("charge")
        assert kwargs["user_id"] == UUID(body["user"])
        assert kwargs["project_id"] == UUID(body["metadata"]["project_id"])
        assert kwargs["free"] is False
        assert kwargs["cost_rub"] == Decimal("0.0525")
    async def cache(*args):
        order.append("cache")
    monkeypatch.setattr(chat.billing, "precheck_balance", AsyncMock(side_effect=check))
    monkeypatch.setattr(chat.router_module, "acompletion", AsyncMock(side_effect=generate))
    monkeypatch.setattr(chat.billing, "charge", AsyncMock(side_effect=charge))
    monkeypatch.setattr(chat.cache, "set", AsyncMock(side_effect=cache))
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200
    assert order == ["check", "upstream", "charge", "cache"]


def test_required_cache_hit_checks_wallet_without_debit(client, monkeypatch):
    monkeypatch.setattr(chat.cache, "get", AsyncMock(return_value=completion()))
    upstream = AsyncMock()
    monkeypatch.setattr(chat.router_module, "acompletion", upstream)
    response = client.post("/v1/chat/completions", json=payload())
    assert response.status_code == 200
    chat.billing.precheck_balance.assert_awaited_once()
    chat.billing.charge.assert_not_awaited()
    upstream.assert_not_awaited()


@pytest.mark.parametrize("failure", ["precheck_balance", "charge"])
def test_legacy_generation_keeps_existing_fail_open_behavior(client, monkeypatch, failure):
    body = payload()
    body["metadata"].pop("require_billing")
    monkeypatch.setattr(chat.billing, failure, AsyncMock(side_effect=RuntimeError("db unavailable")))
    monkeypatch.setattr(chat.router_module, "acompletion", AsyncMock(return_value=completion()))
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "paid answer"

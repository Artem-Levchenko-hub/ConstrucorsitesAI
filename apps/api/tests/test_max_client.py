from __future__ import annotations

from typing import Any

import pytest

from omnia_api.services import max_client


@pytest.mark.asyncio
async def test_get_me_normalizes_official_user_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "user_id": 42,
            "first_name": "Omnia",
            "last_name": "Bot",
            "username": "@omnia_bot",
        }

    monkeypatch.setattr(max_client, "_request", fake_request)
    bot = await max_client.get_me("secret")
    assert bot.id == "42"
    assert bot.name == "Omnia Bot"
    assert bot.username == "omnia_bot"


@pytest.mark.asyncio
async def test_subscribe_uses_secret_and_required_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_request(*args: Any, **kwargs: Any) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(max_client, "_request", fake_request)
    await max_client.subscribe("secret", "https://app.example/api/max/webhook", "hook-secret")
    assert captured["args"][:3] == ("POST", "/subscriptions", "secret")
    assert captured["kwargs"]["json"] == {
        "url": "https://app.example/api/max/webhook",
        "secret": "hook-secret",
        "update_types": ["message_created", "message_callback", "bot_started"],
    }


@pytest.mark.asyncio
async def test_subscription_lookup_accepts_wrapped_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"subscriptions": [{"url": "https://app.example/api/max/webhook"}]}

    monkeypatch.setattr(max_client, "_request", fake_request)
    assert (
        await max_client.has_subscription("secret", "https://app.example/api/max/webhook") is True
    )

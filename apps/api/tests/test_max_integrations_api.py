from __future__ import annotations

import httpx
import pytest

from omnia_api.routers import projects as projects_router
from omnia_api.services import max_client, orchestrator_client
from omnia_api.services import repo as repo_svc

pytestmark = pytest.mark.asyncio


async def _register_and_create(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    template: str = "max_miniapp",
) -> str:
    monkeypatch.setattr(repo_svc, "init_repo", lambda *_args: "a" * 40)
    monkeypatch.setattr(projects_router, "enqueue_preview", lambda *_args: None)

    async def fake_publish(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(projects_router, "publish_event", fake_publish)
    registered = await client.post(
        "/api/auth/register",
        json={"email": f"{template}@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201
    created = await client.post(
        "/api/projects",
        json={"name": "MAX loyalty", "template": template},
    )
    assert created.status_code == 201
    return str(created.json()["id"])


async def test_max_connection_activation_and_disconnect_never_expose_secrets(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch)

    verified_tokens: list[str] = []

    async def fake_get_me(_token: str) -> max_client.MaxBot:
        verified_tokens.append(_token)
        return max_client.MaxBot(id="42", name="Omnia MAX", username="omnia_max_bot")

    async def fake_get_deploy(_project_id):
        return {
            "phase": "done",
            "prod_url": "https://max-project.example.com",
        }

    subscribed: list[tuple[str, str]] = []
    unsubscribed: list[str] = []

    async def fake_has_subscription(_token: str, _url: str) -> bool:
        assert _token == "rotated-max-bot-secret"
        return False

    async def fake_subscribe(_token: str, url: str, secret: str) -> None:
        assert secret
        subscribed.append((url, secret))

    async def fake_unsubscribe(_token: str, url: str) -> None:
        unsubscribed.append(url)

    monkeypatch.setattr(max_client, "get_me", fake_get_me)
    monkeypatch.setattr(max_client, "has_subscription", fake_has_subscription)
    monkeypatch.setattr(max_client, "subscribe", fake_subscribe)
    monkeypatch.setattr(max_client, "unsubscribe", fake_unsubscribe)
    monkeypatch.setattr(orchestrator_client, "get_deploy", fake_get_deploy)

    connected = await client.post(
        f"/api/projects/{project_id}/integrations/max/connect",
        json={"token": "max-bot-secret-value"},
    )
    assert connected.status_code == 200
    body = connected.json()
    assert body["connected"] is True
    assert body["status"] == "verified"
    assert body["bot_username"] == "omnia_max_bot"
    assert "token" not in body
    assert "secret" not in body

    rotated = await client.post(
        f"/api/projects/{project_id}/integrations/max/connect",
        json={"token": "rotated-max-bot-secret"},
    )
    assert rotated.status_code == 200
    assert verified_tokens == ["max-bot-secret-value", "rotated-max-bot-secret"]

    activated = await client.post(f"/api/projects/{project_id}/integrations/max/activate")
    assert activated.status_code == 200
    active = activated.json()
    assert active["status"] == "active"
    assert active["app_url"] == "https://max-project.example.com"
    assert active["webhook_url"] == "https://max-project.example.com/api/max/webhook"
    assert active["deep_link"] == "https://max.ru/omnia_max_bot"
    assert subscribed and subscribed[0][0] == active["webhook_url"]
    assert "token" not in active
    assert "secret" not in active

    disconnected = await client.delete(f"/api/projects/{project_id}/integrations/max")
    assert disconnected.status_code == 204
    assert unsubscribed == ["https://max-project.example.com/api/max/webhook"]


async def test_max_connection_rejects_non_max_project(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch, template="spa")
    response = await client.post(
        f"/api/projects/{project_id}/integrations/max/connect",
        json={"token": "max-bot-secret-value"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "max_project_required"

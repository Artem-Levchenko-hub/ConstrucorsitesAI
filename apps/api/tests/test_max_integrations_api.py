from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from omnia_api.models.project import Project
from omnia_api.routers import max_accounts as max_accounts_router
from omnia_api.routers import max_studio
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
    if template == "max_miniapp":

        async def verified_npd(_inn: str) -> tuple[str, str | None, dict[str, object]]:
            return "verified", "НПД подтверждён", {"status": True}

        monkeypatch.setattr(max_accounts_router, "_verify_self_employed", verified_npd)
        business = await client.put(
            "/api/max/account/business",
            json={
                "kind": "self_employed",
                "inn": "500100732259",
                "legal_name": "Тестовый владелец",
            },
        )
        assert business.status_code == 200
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


async def test_max_connection_surfaces_tls_trust_failure(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch)

    async def fail_tls(_token: str) -> max_client.MaxBot:
        raise max_client.MaxTlsConfigurationError(
            "TLS-сертификат MAX API не прошёл проверку доверия"
        )

    monkeypatch.setattr(max_client, "get_me", fail_tls)
    response = await client.post(
        f"/api/projects/{project_id}/integrations/max/connect",
        json={"token": "max-bot-secret-value"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "max_api_tls_untrusted",
        "message": "TLS-сертификат MAX API не прошёл проверку доверия",
    }


async def test_max_preview_session_returns_validated_url_without_caching(
    client: httpx.AsyncClient,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch)
    project = await db_session.get(Project, UUID(project_id))
    assert project is not None
    expected_url = (
        f"https://{project.slug}-dev.preview.example/api/omnia/preview-session"
        "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )

    async def fake_create_preview_session(received_project_id: UUID) -> dict[str, str]:
        assert received_project_id == project.id
        return {
            "project_id": str(project.id),
            "bootstrap_url": expected_url,
            "expires_at": "2030-01-01T00:00:00Z",
        }

    monkeypatch.setattr(
        max_studio.orchestrator_client,
        "create_max_preview_session",
        fake_create_preview_session,
    )

    response = await client.post(f"/api/projects/{project_id}/max/preview-session")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "url": expected_url,
        "expires_at": "2030-01-01T00:00:00Z",
    }


async def test_max_preview_session_rejects_foreign_project(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch)
    logged_out = await client.post("/api/auth/logout")
    assert logged_out.status_code == 204
    registered = await client.post(
        "/api/auth/register",
        json={"email": "preview-foreign@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    response = await client.post(f"/api/projects/{project_id}/max/preview-session")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_max_preview_session_rejects_non_max_project(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch, template="spa")

    response = await client.post(f"/api/projects/{project_id}/max/preview-session")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "max_project_required"


@pytest.mark.parametrize(
    "url_template",
    [
        "http://{host}/api/omnia/preview-session?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "https://{slug}-dev.preview.example/other?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "https://{host}/api/omnia/preview-session?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&extra=bad",
        "https://attacker@{host}/api/omnia/preview-session?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ],
)
async def test_max_preview_session_rejects_malformed_orchestrator_url(
    client: httpx.AsyncClient,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    url_template: str,
) -> None:
    project_id = await _register_and_create(client, monkeypatch)
    project = await db_session.get(Project, UUID(project_id))
    assert project is not None

    async def fake_create_preview_session(_project_id: UUID) -> dict[str, str]:
        return {
            "project_id": str(project.id),
            "bootstrap_url": url_template.format(
                slug=project.slug,
                host=f"{project.slug}-dev.preview.example",
            ),
            "expires_at": "2030-01-01T00:00:00Z",
        }

    monkeypatch.setattr(
        max_studio.orchestrator_client,
        "create_max_preview_session",
        fake_create_preview_session,
    )

    response = await client.post(f"/api/projects/{project_id}/max/preview-session")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "orchestrator_unavailable"

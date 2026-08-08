from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from omnia_api.core.config import get_settings
from omnia_api.core.crypto import decrypt_strong, encrypt_strong
from omnia_api.core.errors import ApiError
from omnia_api.models.app_integration import (
    BusinessIntegration,
    ProjectIntegrationBinding,
)
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.models.project import Project
from omnia_api.routers import integration_runtime as integration_runtime_router
from omnia_api.routers import max_accounts as max_accounts_router
from omnia_api.routers import projects as projects_router
from omnia_api.services import integration_providers, llm_client
from omnia_api.services import repo as repo_svc


async def test_runtime_ai_limit_fails_closed_when_redis_is_unavailable(monkeypatch) -> None:
    class BrokenRedis:
        async def incr(self, _key: str) -> int:
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(integration_runtime_router, "get_redis", lambda: BrokenRedis())

    with pytest.raises(ApiError) as raised:
        await integration_runtime_router._enforce_runtime_ai_limits(UUID(int=1), 42)

    assert raised.value.status_code == 503


async def _register_and_create(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    monkeypatch.setattr(repo_svc, "init_repo", lambda *_args: "b" * 40)
    monkeypatch.setattr(projects_router, "enqueue_preview", lambda *_args: None)

    async def fake_publish(*_args, **_kwargs) -> None:
        return None

    async def verified_npd(_inn: str) -> tuple[str, str | None, dict[str, object]]:
        return "verified", "НПД подтверждён", {"status": True}

    monkeypatch.setattr(projects_router, "publish_event", fake_publish)
    monkeypatch.setattr(max_accounts_router, "_verify_self_employed", verified_npd)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "integrations@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201
    # The production image correctly marks the cookie Secure, while ASGITransport
    # uses http://testserver. Reinsert the same signed token as a transport-local
    # cookie so this API test is independent of the deployment cookie policy.
    cookie_name = get_settings().jwt_cookie_name
    session_cookie = registered.cookies.get(cookie_name)
    assert session_cookie
    client.cookies.clear()
    client.cookies.set(cookie_name, session_cookie)
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
        json={"name": "MAX storefront", "template": "max_miniapp"},
    )
    assert created.status_code == 201
    return str(created.json()["id"])


@pytest.mark.asyncio
async def test_catalog_and_connection_never_expose_provider_secrets(
    client: httpx.AsyncClient,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch)

    async def verified(
        provider: str,
        public_values: dict[str, str],
        secret_values: dict[str, str],
    ) -> str:
        assert provider == "yookassa"
        assert public_values == {"shop_id": "123456"}
        assert secret_values == {"secret_key": "live_secret_value"}
        return "Магазин 123456"

    monkeypatch.setattr(integration_providers, "verify_provider", verified)

    catalog = await client.get(f"/api/projects/{project_id}/app-integrations")
    assert catalog.status_code == 200
    catalog_body = catalog.json()
    providers = {item["key"]: item for item in catalog_body["providers"]}
    assert providers["yookassa"]["available"] is True
    assert providers["rkeeper"]["available"] is False
    assert providers["rkeeper"]["requirement"]
    assert catalog_body["connections"] == []
    assert catalog_body["recommended_pack"]["provider_keys"]

    connected = await client.put(
        f"/api/projects/{project_id}/app-integrations/yookassa",
        json={
            "values": {
                "shop_id": "123456",
                "secret_key": "live_secret_value",
            }
        },
    )
    assert connected.status_code == 200
    body = connected.json()
    assert body["status"] == "active"
    assert body["business_scoped"] is True
    assert body["bound_to_project"] is True
    assert body["account_label"] == "Магазин 123456"
    assert body["public_config"] == {"shop_id": "123456"}
    assert body["configured_fields"] == ["secret_key"]
    assert "live_secret_value" not in connected.text

    stored = (
        await db_session.execute(
            select(BusinessIntegration).where(BusinessIntegration.provider == "yookassa")
        )
    ).scalar_one()
    assert "live_secret_value" not in stored.credentials_enc
    assert "live_secret_value" in decrypt_strong(stored.credentials_enc)

    refreshed = await client.get(f"/api/projects/{project_id}/app-integrations")
    assert refreshed.status_code == 200
    assert "live_secret_value" not in refreshed.text
    assert len(refreshed.json()["connections"]) == 1
    assert refreshed.json()["connections"][0]["bound_to_project"] is True

    second = await client.post(
        "/api/projects",
        json={"name": "Second MAX app", "template": "max_miniapp"},
    )
    assert second.status_code == 201
    second_id = second.json()["id"]
    reusable = await client.get(f"/api/projects/{second_id}/app-integrations")
    assert reusable.status_code == 200
    assert reusable.json()["connections"][0]["bound_to_project"] is False
    bound = await client.post(f"/api/projects/{second_id}/app-integrations/yookassa/bind")
    assert bound.status_code == 200
    assert bound.json()["bound_to_project"] is True
    bindings = list(
        (
            await db_session.execute(
                select(ProjectIntegrationBinding).where(
                    ProjectIntegrationBinding.provider == "yookassa"
                )
            )
        ).scalars()
    )
    assert len(bindings) == 2


@pytest.mark.asyncio
async def test_planned_provider_is_honest_and_cannot_be_connected(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch)
    response = await client.put(
        f"/api/projects/{project_id}/app-integrations/rkeeper",
        json={"values": {}},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "integration_connection_failed"
    assert "Delivery_Api" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_verify_marks_broken_connection_without_exposing_credentials(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch)

    async def succeeds(*_args, **_kwargs) -> str:
        return "Счётчик 42"

    monkeypatch.setattr(integration_providers, "verify_provider", succeeds)
    connected = await client.put(
        f"/api/projects/{project_id}/app-integrations/yandex_metrica",
        json={"values": {"counter_id": "42", "oauth_token": "oauth-secret"}},
    )
    assert connected.status_code == 200

    async def fails(*_args, **_kwargs) -> str:
        raise integration_providers.IntegrationCredentialsInvalid(
            "Яндекс Метрика отклонила реквизиты."
        )

    monkeypatch.setattr(integration_providers, "verify_provider", fails)
    checked = await client.post(
        f"/api/projects/{project_id}/app-integrations/yandex_metrica/verify"
    )
    assert checked.status_code == 422
    assert checked.json()["error"]["code"] == "integration_credentials_invalid"

    catalog = await client.get(f"/api/projects/{project_id}/app-integrations")
    connection = catalog.json()["connections"][0]
    assert connection["status"] == "error"
    assert connection["last_error"] == "Яндекс Метрика отклонила реквизиты."
    assert "oauth-secret" not in catalog.text

    disconnected = await client.delete(
        f"/api/projects/{project_id}/app-integrations/yandex_metrica"
    )
    assert disconnected.status_code == 204
    still_saved = await client.get(f"/api/projects/{project_id}/app-integrations")
    assert still_saved.json()["connections"][0]["bound_to_project"] is False

    removed = await client.delete(
        f"/api/projects/{project_id}/app-integrations/yandex_metrica/business"
    )
    assert removed.status_code == 204
    empty = await client.get(f"/api/projects/{project_id}/app-integrations")
    assert empty.json()["connections"] == []


def test_provider_values_are_split_and_unknown_fields_rejected() -> None:
    provider = integration_providers.get_provider("yookassa")
    public_values, secret_values = integration_providers.split_values(
        provider,
        {"shop_id": "100", "secret_key": "secret"},
    )
    assert public_values == {"shop_id": "100"}
    assert secret_values == {"secret_key": "secret"}

    with pytest.raises(integration_providers.IntegrationProviderError):
        integration_providers.split_values(
            provider,
            {"shop_id": "100", "secret_key": "secret", "unexpected": "value"},
        )


def _max_init_data(token: str, user_id: int | str = 42) -> str:
    values = {
        "auth_date": str(int(time.time())),
        "query_id": "runtime-test",
        "user": json.dumps({"id": user_id, "first_name": "Тест"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


@pytest.mark.asyncio
async def test_bound_integration_is_available_to_signed_max_runtime(
    client: httpx.AsyncClient,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch)

    async def verified(*_args, **_kwargs) -> str:
        return "Магазин 123"

    monkeypatch.setattr(integration_providers, "verify_provider", verified)
    connected = await client.put(
        f"/api/projects/{project_id}/app-integrations/yookassa",
        json={"values": {"shop_id": "123", "secret_key": "secret"}},
    )
    assert connected.status_code == 200
    token = "max-bot-token"
    db_session.add(
        MaxIntegration(
            project_id=project_id,
            owner_id=(
                await db_session.execute(
                    select(BusinessIntegration.created_by_user_id).where(
                        BusinessIntegration.provider == "yookassa"
                    )
                )
            ).scalar_one(),
            bot_token_enc=encrypt_strong(token),
            webhook_secret_enc=encrypt_strong("webhook"),
        )
    )
    await db_session.commit()

    runtime = await client.get(
        f"/api/runtime/projects/{project_id}/integrations",
        headers={"X-MAX-Init-Data": _max_init_data(token)},
    )
    assert runtime.status_code == 200
    assert runtime.json()["providers"] == ["yookassa"]
    assert "Оплата" in runtime.json()["capabilities"]
    assert "Встроенный AI" in runtime.json()["capabilities"]
    assert "Sonnet" not in runtime.text

    string_user = await client.get(
        f"/api/runtime/projects/{project_id}/integrations",
        headers={"X-MAX-Init-Data": _max_init_data(token, "42")},
    )
    assert string_user.status_code == 200

    tampered = await client.get(
        f"/api/runtime/projects/{project_id}/integrations",
        headers={"X-MAX-Init-Data": _max_init_data(token) + "x"},
    )
    assert tampered.status_code == 401


@pytest.mark.asyncio
async def test_signed_max_runtime_can_call_managed_ai_without_a_client_key(
    client: httpx.AsyncClient,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await _register_and_create(client, monkeypatch)
    project = await db_session.get(Project, UUID(project_id))
    assert project is not None
    token = "max-ai-bot-token"
    db_session.add(
        MaxIntegration(
            project_id=project.id,
            owner_id=project.owner_id,
            bot_token_enc=encrypt_strong(token),
            webhook_secret_enc=encrypt_strong("webhook"),
        )
    )
    await db_session.commit()

    async def no_limit(_project_id, _max_user_id) -> None:
        return None

    captured: dict[str, object] = {}

    async def fake_complete(messages, model, **kwargs) -> str:
        captured.update({"messages": messages, "model": model, **kwargs})
        return "Добавьте 20 минут спокойной ходьбы и сохраните ранний отход ко сну."

    monkeypatch.setattr(integration_runtime_router, "_enforce_runtime_ai_limits", no_limit)
    monkeypatch.setattr(llm_client, "complete_chat", fake_complete)

    response = await client.post(
        f"/api/runtime/projects/{project.id}/ai",
        headers={"X-MAX-Init-Data": _max_init_data(token)},
        json={
            "message": "Разбери мой день",
            "instructions": "Ты фитнес-тренер",
            "context": {"sleepHours": 7.5, "steps": 8_200},
        },
    )

    assert response.status_code == 200
    assert "ходьбы" in response.json()["answer"]
    assert response.json()["model"] == "managed-ai"
    assert "sonnet" not in response.text.lower()
    assert captured["user_id"] == str(project.owner_id)
    assert captured["project_id"] == str(project.id)
    assert captured["stage"] == "runtime_ai"
    assert "api_key" not in response.text.lower()

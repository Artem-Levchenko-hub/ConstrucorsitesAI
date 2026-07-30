from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from omnia_api.core.crypto import decrypt_strong
from omnia_api.models.app_integration import AppIntegration
from omnia_api.routers import max_accounts as max_accounts_router
from omnia_api.routers import projects as projects_router
from omnia_api.services import integration_providers
from omnia_api.services import repo as repo_svc


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
    assert body["account_label"] == "Магазин 123456"
    assert body["public_config"] == {"shop_id": "123456"}
    assert body["configured_fields"] == ["secret_key"]
    assert "live_secret_value" not in connected.text

    stored = (
        await db_session.execute(
            select(AppIntegration).where(AppIntegration.project_id == project_id)
        )
    ).scalar_one()
    assert "live_secret_value" not in stored.credentials_enc
    assert "live_secret_value" in decrypt_strong(stored.credentials_enc)

    refreshed = await client.get(f"/api/projects/{project_id}/app-integrations")
    assert refreshed.status_code == 200
    assert "live_secret_value" not in refreshed.text
    assert len(refreshed.json()["connections"]) == 1


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

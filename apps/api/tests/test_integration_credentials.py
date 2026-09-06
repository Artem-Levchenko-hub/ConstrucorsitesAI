"""Real PostgreSQL credential lifecycle tests with synthetic OAuth HTTP traffic."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.crypto import decrypt_strong, encrypt_strong
from omnia_api.core.errors import ApiError
from omnia_api.models.account import BusinessProfile
from omnia_api.models.app_integration import BusinessIntegration, ProjectIntegrationBinding
from omnia_api.models.user import User
from omnia_api.services import integration_oauth
from omnia_api.services.integration_credentials import load_credentials
from tests.test_app_integrations_api import _register_and_create

_HTTP_CLIENT = httpx.AsyncClient


async def _connection(session):
    user = User(email="oauth-fixture@example.test")
    business = BusinessProfile(kind="self_employed", inn="500100732259", legal_name="Fixture")
    session.add_all([user, business])
    await session.flush()
    connection = BusinessIntegration(
        business_id=business.id,
        created_by_user_id=user.id,
        provider="yookassa",
        auth_mode="oauth",
        credentials_enc=encrypt_strong(
            json.dumps(
                {
                    "access_token": "synthetic-old-access",
                    "refresh_token": "synthetic-old-refresh",
                }
            )
        ),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    session.add(connection)
    await session.commit()
    return connection


def _http(monkeypatch, handler):
    monkeypatch.setattr(
        integration_oauth,
        "credentials",
        lambda _: integration_oauth.OAuthCredentials("synthetic-client", "synthetic-secret"),
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _HTTP_CLIENT(**kwargs, transport=httpx.MockTransport(handler)),
    )


async def test_shared_rotating_token_refreshed_once_with_stale_identity_map(
    db_session,
    test_engine,
    monkeypatch,
):
    connection = await _connection(db_session)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    first_entered = asyncio.Event()
    release = asyncio.Event()
    rotations = []

    async def provider(request):
        rotations.append(request.content)
        if len(rotations) > 1:
            return httpx.Response(400, json={"error": "invalid_grant"})
        first_entered.set()
        await release.wait()
        return httpx.Response(
            200,
            json={
                "access_token": "synthetic-new-access",
                "refresh_token": "synthetic-new-refresh",
                "expires_in": 3600,
            },
        )

    _http(monkeypatch, provider)
    async with factory() as first, factory() as second:
        stale_first = await first.get(BusinessIntegration, connection.id)
        stale_second = await second.get(BusinessIntegration, connection.id)
        # Both sessions have preloaded the expired token before either refreshes it.
        task_one = asyncio.create_task(load_credentials(first, stale_first))
        await asyncio.wait_for(first_entered.wait(), 3)
        task_two = asyncio.create_task(load_credentials(second, stale_second))
        await asyncio.sleep(0.1)
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(task_one, task_two, return_exceptions=True),
            5,
        )
        assert all(isinstance(result, dict) for result in results), results
        assert results == [
            {"access_token": "synthetic-new-access", "refresh_token": "synthetic-new-refresh"},
            {"access_token": "synthetic-new-access", "refresh_token": "synthetic-new-refresh"},
        ]
        assert len(rotations) == 1
        await first.rollback()
        await second.rollback()
    await db_session.refresh(connection)
    assert connection.status == "active"
    assert json.loads(decrypt_strong(connection.credentials_enc))["refresh_token"] == (
        "synthetic-new-refresh"
    )


@pytest.mark.parametrize(
    "failure", [429, 503, "timeout", "network", "malformed", "list", "invalid_access"]
)
async def test_refresh_temporary_failure_preserves_active_connection(
    db_session, monkeypatch, failure
):
    connection = await _connection(db_session)
    original_ciphertext = connection.credentials_enc

    def provider(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-old-refresh", request=request)
        if failure == "network":
            raise httpx.ConnectError("synthetic-old-refresh", request=request)
        if failure == "malformed":
            return httpx.Response(200, json={})
        if failure == "list":
            return httpx.Response(200, json=[])
        if failure == "invalid_access":
            return httpx.Response(200, json={"access_token": {"unexpected": "object"}})
        return httpx.Response(failure, json={"error_description": "synthetic-old-refresh"})

    _http(monkeypatch, provider)
    with pytest.raises(ApiError) as caught:
        await load_credentials(db_session, connection)
    assert caught.value.status_code == 503
    assert caught.value.code == "integration_provider_unavailable"
    assert "synthetic" not in caught.value.message
    await db_session.refresh(connection)
    assert connection.status == "active"
    assert connection.last_error is None
    assert connection.credentials_enc == original_ciphertext


async def test_confirmed_invalid_refresh_persists_error(db_session, monkeypatch):
    connection = await _connection(db_session)
    _http(monkeypatch, lambda _: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(ApiError) as caught:
        await load_credentials(db_session, connection)
    assert caught.value.code == "integration_credentials_invalid"
    assert caught.value.status_code == 409
    await db_session.refresh(connection)
    assert connection.status == "error"
    assert "synthetic" not in connection.last_error


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
async def test_verify_transient_failure_keeps_binding_ready(
    client, db_session, monkeypatch, failure
):
    project_id = await _register_and_create(client, monkeypatch)
    _http(monkeypatch, lambda _: httpx.Response(200, json={"items": []}))
    connected = await client.put(
        f"/api/projects/{project_id}/app-integrations/yookassa",
        json={"values": {"shop_id": "123", "secret_key": "synthetic-key"}},
    )
    assert connected.status_code == 200

    def provider(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-key", request=request)
        return httpx.Response(failure, json={"error": "synthetic-key"})

    _http(monkeypatch, provider)
    checked = await client.post(f"/api/projects/{project_id}/app-integrations/yookassa/verify")
    assert checked.status_code == 503
    assert "synthetic-key" not in checked.text
    stored = (await db_session.execute(select(BusinessIntegration))).scalar_one()
    binding = (await db_session.execute(select(ProjectIntegrationBinding))).scalar_one()
    assert stored.status == "active"
    assert stored.last_error is None
    assert binding.status == "ready"
    assert binding.enabled is True


async def test_verify_refreshes_expired_oauth_before_profile_request(
    client, db_session, monkeypatch
):
    project_id = await _register_and_create(client, monkeypatch)
    _http(monkeypatch, lambda _: httpx.Response(200, json={"items": []}))
    connected = await client.put(
        f"/api/projects/{project_id}/app-integrations/yookassa",
        json={"values": {"shop_id": "123", "secret_key": "synthetic-key"}},
    )
    assert connected.status_code == 200
    stored = (await db_session.execute(select(BusinessIntegration))).scalar_one()
    stored.auth_mode = "oauth"
    stored.credentials_enc = encrypt_strong(
        json.dumps(
            {
                "access_token": "synthetic-old-access",
                "refresh_token": "synthetic-old-refresh",
            }
        )
    )
    stored.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    def provider(request):
        if request.url.path == "/oauth/v2/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-new-access",
                    "refresh_token": "synthetic-new-refresh",
                    "expires_in": 3600,
                },
            )
        if request.headers.get("authorization") != "Bearer synthetic-new-access":
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json={"account_id": "123"})

    _http(monkeypatch, provider)
    checked = await client.post(f"/api/projects/{project_id}/app-integrations/yookassa/verify")
    assert checked.status_code == 200
    assert checked.json()["status"] == "active"
    assert "synthetic" not in checked.text
    await db_session.refresh(stored)
    assert json.loads(decrypt_strong(stored.credentials_enc))["refresh_token"] == (
        "synthetic-new-refresh"
    )

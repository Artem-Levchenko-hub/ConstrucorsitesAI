"""Exercise the real callback/auth boundary without a database or provider traffic."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Response

from yleum_api.core.db import get_session
from yleum_api.core.deps import set_session_cookie
from yleum_api.core.errors import ApiError, api_error_handler
from yleum_api.core.security import create_access_token
from yleum_api.models.user import User
from yleum_api.routers import app_integrations
from yleum_api.services.integration_oauth import OAuthResult
from yleum_api.services.integration_providers import IntegrationProviderUnavailable


@pytest.fixture
def callback_boundary(monkeypatch):
    owner = SimpleNamespace(id=uuid4(), status="active")
    stranger = SimpleNamespace(id=uuid4(), status="active")
    raw_state = "synthetic-unpredictable-state-for-owner"
    record = SimpleNamespace(
        user_id=owner.id,
        project_id=uuid4(),
        provider="amocrm",
        used_at=None,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )

    class Session:
        commits = 0

        async def execute(self, statement):
            expected = hashlib.sha256(raw_state.encode()).hexdigest()
            found = record if statement.compile().params.get("state_hash_1") == expected else None
            return SimpleNamespace(scalar_one_or_none=lambda: found)

        async def get(self, model, identifier):
            assert model is User
            return next((user for user in (owner, stranger) if user.id == identifier), None)

        async def commit(self):
            self.commits += 1

    session = Session()

    async def database():
        yield session

    connection = SimpleNamespace(user_id=owner.id, provider="amocrm")
    find_connection = AsyncMock(return_value=connection)
    bind = AsyncMock()
    exchange = AsyncMock(return_value=OAuthResult(
        public_config={"base_url": "https://synthetic.amocrm.ru"},
        secret_values={"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"},
        account_label="Synthetic account",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    ))
    monkeypatch.setattr(app_integrations, "_account_connection", find_connection)
    monkeypatch.setattr(app_integrations, "_bind", bind)
    monkeypatch.setattr(app_integrations.integration_oauth, "exchange_code", exchange)
    monkeypatch.setattr(app_integrations, "encrypt_strong", lambda _: "synthetic-ciphertext")
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(app_integrations.router)
    app.dependency_overrides[get_session] = database
    return SimpleNamespace(
        app=app, owner=owner, stranger=stranger, record=record, session=session,
        raw_state=raw_state, exchange=exchange, bind=bind, find_connection=find_connection,
        connection=connection,
    )


async def callback(boundary, *, user=None, provider="amocrm", **params):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=boundary.app), base_url="https://yleum.example.test",
    ) as client:
        if user is not None:
            client.cookies.set("omnia_session", create_access_token(user.id))
        return await client.get(
            f"/api/integrations/oauth/{provider}/callback",
            params={"state": boundary.raw_state, "code": "synthetic-code", **params},
        )


async def test_callback_without_initiator_session_cannot_link_another_crm(callback_boundary):
    boundary = callback_boundary
    response = await callback(boundary)
    assert response.status_code == 401
    assert boundary.record.used_at is None
    boundary.exchange.assert_not_awaited()
    boundary.bind.assert_not_awaited()


async def test_foreign_session_cannot_consume_state_or_bind_its_grant(callback_boundary):
    boundary = callback_boundary
    response = await callback(boundary, user=boundary.stranger)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "integration_oauth_state_invalid"
    assert boundary.record.used_at is None
    boundary.exchange.assert_not_awaited()
    boundary.bind.assert_not_awaited()
    accepted = await callback(boundary, user=boundary.owner)
    assert accepted.status_code == 303


@pytest.mark.parametrize("provider", ["amocrm", "yookassa", "yandex_metrica", "bitrix24"])
async def test_owner_cookie_callback_binds_original_project_and_rejects_replay(
    callback_boundary, provider,
):
    boundary = callback_boundary
    boundary.record.provider = provider
    response = await callback(boundary, user=boundary.owner, provider=provider)
    assert response.status_code == 303
    assert response.headers["location"].endswith(
        f"/max/{boundary.record.project_id}/integrations?oauth=connected"
    )
    boundary.find_connection.assert_awaited_once_with(
        boundary.session, boundary.owner.id, provider,
    )
    boundary.bind.assert_awaited_once_with(
        boundary.session, boundary.record.project_id, boundary.connection,
    )
    assert boundary.connection.status == "active"
    assert boundary.record.used_at is not None
    replay = await callback(boundary, user=boundary.owner, provider=provider)
    assert replay.status_code == 400
    assert boundary.exchange.await_count == 1


@pytest.mark.parametrize("cancel", [{"error": "access_denied"}, {"code": ""}])
async def test_cancel_consumes_state_without_touching_connection(callback_boundary, cancel):
    boundary = callback_boundary
    response = await callback(boundary, user=boundary.owner, **cancel)
    assert response.status_code == 303
    assert response.headers["location"].endswith("oauth=cancelled")
    assert boundary.record.used_at is not None
    boundary.exchange.assert_not_awaited()
    boundary.bind.assert_not_awaited()
    assert (await callback(boundary, user=boundary.owner)).status_code == 400


@pytest.mark.parametrize("invalid", ["expired", "used", "provider", "unknown"])
async def test_invalid_state_never_exchanges_code(callback_boundary, invalid):
    boundary = callback_boundary
    params = {}
    if invalid == "expired":
        boundary.record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif invalid == "used":
        boundary.record.used_at = datetime.now(UTC)
    elif invalid == "provider":
        boundary.record.provider = "yookassa"
    else:
        params["state"] = "synthetic-unknown-state-value"
    response = await callback(boundary, user=boundary.owner, **params)
    assert response.status_code == 400
    boundary.exchange.assert_not_awaited()
    boundary.bind.assert_not_awaited()


async def test_provider_failure_consumes_state_without_replacing_connection(callback_boundary):
    boundary = callback_boundary
    boundary.exchange.side_effect = IntegrationProviderUnavailable("Synthetic outage")
    response = await callback(boundary, user=boundary.owner)
    assert response.status_code == 303
    assert response.headers["location"].endswith("oauth=error")
    assert boundary.record.used_at is not None
    boundary.find_connection.assert_not_awaited()
    boundary.bind.assert_not_awaited()


def test_login_cookie_allows_top_level_get_oauth_return():
    response = Response()
    set_session_cookie(response, "synthetic-session")
    cookie = response.headers["set-cookie"].lower()
    assert "samesite=lax" in cookie
    assert "httponly" in cookie
    assert "path=/" in cookie

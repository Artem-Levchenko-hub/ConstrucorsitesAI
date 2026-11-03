"""Offline authenticated callback regression tests for shared merchant grants."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from tests.test_integration_oauth_callback import callback
from tests.test_integration_oauth_callback import callback_boundary as callback_boundary_fixture
from yleum_api.core.crypto import encrypt_strong
from yleum_api.core.errors import ApiError
from yleum_api.routers import app_integrations
from yleum_api.schemas.app_integration import IntegrationConnectRequest
from yleum_api.services.integration_credentials import load_credentials, lock_account_provider
from yleum_api.services.integration_oauth import OAuthResult
from yleum_api.services.integration_providers import IntegrationProviderError

callback_boundary = callback_boundary_fixture


def _merchant(boundary, *, shop="123", existing=True):
    connection = boundary.connection
    connection.provider = "yookassa"
    connection.grant_revision = 4
    boundary.record.provider = "yookassa"
    boundary.record.oauth_client_key = None
    boundary.record.connection_snapshot = {
        "version": 1, "connection_id": str(connection.id) if existing else None,
        "grant_revision": connection.grant_revision if existing else None,
    }
    boundary.find_connection.return_value = connection if existing else None
    boundary.exchange.return_value = OAuthResult(
        public_config={"shop_id": shop}, secret_values={"access_token": "synthetic-new-access"},
        account_label=f"Магазин {shop}",
    )


async def test_other_shop_cannot_reroute_two_apps_shared_merchant(callback_boundary):
    boundary = callback_boundary
    _merchant(boundary, shop="456")
    previous = vars(boundary.connection).copy()
    response = await callback(boundary, user=boundary.owner, provider="yookassa")
    assert response.status_code == 303
    assert response.headers["location"].endswith("oauth=conflict")
    assert vars(boundary.connection) == previous
    boundary.bind.assert_not_awaited()
    assert boundary.record.used_at is not None


async def test_same_shop_reconnect_rotates_without_an_extra_confirmation(callback_boundary):
    boundary = callback_boundary
    _merchant(boundary)
    response = await callback(boundary, user=boundary.owner, provider="yookassa")
    assert response.headers["location"].endswith("oauth=connected")
    assert boundary.connection.public_config == {"shop_id": "123"}
    assert boundary.connection.grant_revision == 5
    assert boundary.connection.credentials_enc == "synthetic-ciphertext"
    boundary.bind.assert_awaited_once_with(
        boundary.session, boundary.record.project_id, boundary.connection,
    )


@pytest.mark.parametrize("previous_config", [{}, {"shop_id": ""}, {"shop_id": 123}])
async def test_unverified_existing_identity_is_not_silently_replaced(
    callback_boundary, previous_config,
):
    boundary = callback_boundary
    _merchant(boundary)
    boundary.connection.public_config = previous_config
    previous = vars(boundary.connection).copy()
    response = await callback(boundary, user=boundary.owner, provider="yookassa")
    assert response.headers["location"].endswith("oauth=conflict")
    assert vars(boundary.connection) == previous
    boundary.bind.assert_not_awaited()


async def test_provider_error_consumes_state_without_mutating_merchant(callback_boundary):
    boundary = callback_boundary
    _merchant(boundary)
    previous = vars(boundary.connection).copy()
    boundary.exchange.side_effect = IntegrationProviderError("synthetic-private-provider-detail")
    response = await callback(boundary, user=boundary.owner, provider="yookassa")
    assert response.headers["location"].endswith("oauth=error")
    assert vars(boundary.connection) == previous
    assert boundary.record.used_at is not None
    boundary.bind.assert_not_awaited()


@pytest.mark.parametrize("foreign", [False, True])
async def test_foreign_or_missing_owner_session_cannot_consume_yookassa_state(
    callback_boundary, foreign,
):
    boundary = callback_boundary
    _merchant(boundary)
    response = await callback(
        boundary, user=boundary.stranger if foreign else None, provider="yookassa",
    )
    assert response.status_code == (400 if foreign else 401)
    assert boundary.record.used_at is None
    boundary.exchange.assert_not_awaited()
    boundary.bind.assert_not_awaited()


@pytest.mark.parametrize("change", ["created", "id", "revision", "missing", "malformed"])
async def test_stale_snapshot_consumed_before_provider_exchange(callback_boundary, change):
    boundary = callback_boundary
    _merchant(boundary, existing=change != "created")
    if change == "created":
        boundary.find_connection.return_value = boundary.connection
    elif change == "id":
        boundary.connection.id = uuid4()
    elif change == "revision":
        boundary.connection.grant_revision += 1
    elif change == "missing":
        boundary.record.connection_snapshot = None
    else:
        boundary.record.connection_snapshot["version"] = True
    previous = vars(boundary.connection).copy()
    response = await callback(boundary, user=boundary.owner, provider="yookassa")
    assert response.headers["location"].endswith("oauth=conflict")
    assert vars(boundary.connection) == previous
    boundary.exchange.assert_not_awaited()
    boundary.bind.assert_not_awaited()


async def test_project_ownership_is_rechecked_before_exchange(callback_boundary, monkeypatch):
    boundary = callback_boundary
    _merchant(boundary)
    monkeypatch.setattr(app_integrations, "_owned_max_project", AsyncMock(side_effect=ApiError(
        "not_found", "project not found", 404,
    )))
    response = await callback(boundary, user=boundary.owner, provider="yookassa")
    assert response.status_code == 404
    boundary.exchange.assert_not_awaited()
    boundary.bind.assert_not_awaited()


@pytest.mark.parametrize("existing", [True, False])
async def test_start_pins_current_connection_identity(callback_boundary, monkeypatch, existing):
    boundary = callback_boundary
    _merchant(boundary, existing=existing)
    monkeypatch.setattr(app_integrations, "require_existing_max_account_access", lambda _: None)
    monkeypatch.setattr(app_integrations, "assert_integrations_allowed", AsyncMock())
    monkeypatch.setattr(app_integrations, "_oauth_available", lambda _: True)
    monkeypatch.setattr(
        app_integrations.integration_oauth, "new_authorization_client_key", lambda _: None,
    )
    monkeypatch.setattr(
        app_integrations.integration_oauth, "authorization_url",
        lambda provider, state, **kwargs: f"https://yookassa.ru/oauth/v2/authorize?state={state}",
    )
    await app_integrations.start_integration_oauth(
        boundary.record.project_id, "yookassa", boundary.session, boundary.owner,
    )
    saved = boundary.session.added[-1]
    assert saved.connection_snapshot == {
        "version": 1,
        "connection_id": str(boundary.connection.id) if existing else None,
        "grant_revision": 4 if existing else None,
    }


@pytest.mark.parametrize("cancel", [{"error": "access_denied"}, {"code": ""}])
async def test_cancel_and_replay_preserve_existing_merchant(callback_boundary, cancel):
    boundary = callback_boundary
    _merchant(boundary, shop="456")
    previous = vars(boundary.connection).copy()
    response = await callback(boundary, user=boundary.owner, provider="yookassa", **cancel)
    assert response.headers["location"].endswith("oauth=cancelled")
    assert vars(boundary.connection) == previous
    boundary.exchange.assert_not_awaited()
    assert (await callback(boundary, user=boundary.owner, provider="yookassa")).status_code == 400


@pytest.mark.parametrize("shop", ["123", "456"])
async def test_direct_credentials_rotate_only_the_current_shop(
    callback_boundary, monkeypatch, shop,
):
    boundary = callback_boundary
    _merchant(boundary)
    previous = vars(boundary.connection).copy()
    monkeypatch.setattr(app_integrations, "require_existing_max_account_access", lambda _: None)
    monkeypatch.setattr(app_integrations, "assert_integrations_allowed", AsyncMock())
    monkeypatch.setattr(
        app_integrations.integration_providers, "verify_provider",
        AsyncMock(return_value=f"Магазин {shop}"),
    )
    monkeypatch.setattr(app_integrations, "_connection_public", lambda *_: None)
    async def connect():
        return await app_integrations.connect_integration(
            boundary.record.project_id, "yookassa",
            IntegrationConnectRequest(values={"shop_id": shop, "secret_key": "synthetic-key"}),
            boundary.session, boundary.owner,
        )
    if shop == "123":
        await connect()
        assert boundary.connection.public_config == {"shop_id": "123"}
        assert boundary.connection.grant_revision == 5
        assert boundary.connection.auth_mode == "credentials"
        boundary.bind.assert_awaited_once()
    else:
        with pytest.raises(ApiError) as caught:
            await connect()
        assert caught.value.status_code == 409
        assert vars(boundary.connection) == previous
        boundary.bind.assert_not_awaited()


async def test_yookassa_lock_serializes_account_grants_before_a_connection_exists():
    session = SimpleNamespace(execute=AsyncMock())
    owner = uuid4()
    await lock_account_provider(session, owner, "yookassa")
    await lock_account_provider(session, owner, "yookassa")
    await lock_account_provider(session, uuid4(), "yookassa")
    assert session.execute.await_count == 3
    calls = session.execute.await_args_list
    assert "pg_advisory_xact_lock" in str(calls[0].args[0])
    assert calls[0].args[1] == calls[1].args[1]
    assert calls[0].args[1] != calls[2].args[1]


async def test_expired_yookassa_load_marks_reauthorization_without_network(monkeypatch):
    ciphertext = encrypt_strong(json.dumps({
        "access_token": "synthetic-old", "refresh_token": "unsupported-synthetic-refresh",
    }))
    connection = SimpleNamespace(
        id=uuid4(), user_id=uuid4(), provider="yookassa", auth_mode="oauth",
        oauth_client_key=None, public_config={"shop_id": "123"}, credentials_enc=ciphertext,
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1), status="active", last_error=None,
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: connection)),
        commit=AsyncMock(), rollback=AsyncMock(),
    )

    def unexpected_http(**kwargs):
        pytest.fail("Expired YooKassa grants must not send an unsupported refresh request")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected_http)
    with pytest.raises(ApiError) as caught:
        await load_credentials(session, connection)
    assert caught.value.code == "integration_credentials_invalid"
    assert caught.value.status_code == 409
    assert connection.status == "error"
    assert connection.credentials_enc == ciphertext
    assert "synthetic" not in connection.last_error
    session.commit.assert_awaited_once()

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
_HTTP_CLIENT = httpx.AsyncClient


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


@pytest.fixture
def merchant_verification(callback_boundary, monkeypatch):
    boundary = callback_boundary
    _merchant(boundary)
    now = datetime.now(UTC)
    connection = boundary.connection
    connection.auth_mode = "oauth"
    connection.status = "active"
    connection.last_error = None
    connection.created_at = connection.updated_at = now
    connection.verified_at = connection.last_checked_at = now
    connection.public_config = {"shop_id": "123", "shop_mode": "test", "other": "preserved"}
    binding = SimpleNamespace(
        integration_id=connection.id, enabled=True, status="ready", config={}, last_error=None,
    )
    monkeypatch.setattr(app_integrations, "_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(app_integrations, "require_existing_max_account_access", lambda _: None)
    monkeypatch.setattr(app_integrations, "load_credentials", AsyncMock(
        return_value={"access_token": "synthetic-access"},
    ))

    async def verify():
        return await app_integrations.verify_integration(
            boundary.record.project_id, "yookassa", boundary.session, boundary.owner,
        )

    def install(handler):
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _HTTP_CLIENT(
            transport=httpx.MockTransport(handler), **kwargs,
        ))

    return SimpleNamespace(
        boundary=boundary, connection=connection, binding=binding, verify=verify, install=install,
    )


@pytest.mark.parametrize(("fields", "mode"), [
    ({"test": True}, "test"),
    ({"test": False}, "live"),
    ({}, None),
    ({"test": "false", "shop_mode": "live"}, None),
])
async def test_yookassa_verify_updates_or_clears_saved_shop_mode(
    merchant_verification, fields, mode,
):
    case = merchant_verification
    case.connection.public_config["shop_mode"] = "live" if mode == "test" else "test"
    calls = []

    def provider(request):
        calls.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer synthetic-access"
        return httpx.Response(200, json={"account_id": "123", **fields})

    case.install(provider)
    result = await case.verify()
    expected = {"shop_id": "123", "other": "preserved"}
    if mode is not None:
        expected["shop_mode"] = mode
    assert result.public_config == case.connection.public_config == expected
    assert result.bound_to_project and result.binding_status == "ready"
    assert result.status == "active"
    assert case.connection.grant_revision == 4
    assert calls == [("GET", "/v3/me")]


async def test_yookassa_basic_verify_keeps_legacy_request_without_claiming_mode(
    merchant_verification, monkeypatch,
):
    case = merchant_verification
    case.connection.auth_mode = "credentials"
    monkeypatch.setattr(app_integrations, "load_credentials", AsyncMock(
        return_value={"secret_key": "synthetic-key"},
    ))
    calls = []

    def provider(request):
        calls.append((request.method, request.url.path, request.url.params.get("limit")))
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json={"items": [], "test": False})

    case.install(provider)
    result = await case.verify()
    assert result.public_config == {"shop_id": "123", "other": "preserved"}
    assert result.auth_mode == "credentials"
    assert calls == [("GET", "/v3/payments", "1")]


async def test_yookassa_verify_rejects_a_different_shop_without_replacing_metadata(
    merchant_verification,
):
    case = merchant_verification
    previous = case.connection.public_config.copy()
    case.install(lambda request: httpx.Response(200, json={"account_id": "456", "test": False}))
    with pytest.raises(ApiError) as caught:
        await case.verify()
    assert caught.value.status_code == 422
    assert case.connection.public_config == previous
    assert case.connection.status == case.binding.status == "error"


@pytest.mark.parametrize("provider_status", [200, 401])
async def test_stale_yookassa_verify_cannot_update_reconnected_grant(
    merchant_verification, provider_status,
):
    case = merchant_verification
    replacement = {"shop_id": "123", "shop_mode": "live"}

    def provider(request):
        case.connection.grant_revision += 1
        case.connection.public_config = replacement.copy()
        return httpx.Response(provider_status, json={"account_id": "123", "test": True})

    case.install(provider)
    with pytest.raises(ApiError) as caught:
        await case.verify()
    assert caught.value.status_code == 409
    assert case.connection.public_config == replacement
    assert case.connection.status == "active" and case.binding.status == "ready"
    assert case.boundary.session.commits == 0


async def test_yookassa_manual_connect_rejects_client_shop_mode(callback_boundary, monkeypatch):
    boundary = callback_boundary
    _merchant(boundary)
    previous = vars(boundary.connection).copy()
    monkeypatch.setattr(app_integrations, "require_existing_max_account_access", lambda _: None)
    monkeypatch.setattr(app_integrations, "assert_integrations_allowed", AsyncMock())
    verifier = AsyncMock()
    monkeypatch.setattr(app_integrations.integration_providers, "verify_provider", verifier)
    with pytest.raises(ApiError) as caught:
        await app_integrations.connect_integration(
            boundary.record.project_id, "yookassa",
            IntegrationConnectRequest(values={
                "shop_id": "123", "secret_key": "synthetic-key", "shop_mode": "live",
            }),
            boundary.session, boundary.owner,
        )
    assert caught.value.status_code == 400
    verifier.assert_not_awaited()
    assert vars(boundary.connection) == previous


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

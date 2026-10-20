"""Real PostgreSQL grant serialization; all provider responses are synthetic."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_amocrm_oauth_profiles import profiles as profiles_fixture
from yleum_api.core.crypto import decrypt_strong, encrypt_strong
from yleum_api.core.errors import ApiError
from yleum_api.models.app_integration import AccountIntegration, IntegrationOAuthState
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.routers import app_integrations
from yleum_api.services.integration_credentials import load_credentials

_CLIENT = httpx.AsyncClient
profiles = profiles_fixture


async def _owner(session):
    user = User(email="amo-concurrency@example.test")
    session.add(user)
    await session.flush()
    project = Project(owner_id=user.id, name="Synthetic", slug="amo-concurrency",
                      template="max_miniapp")
    session.add(project)
    await session.commit()
    return user, project


async def _state(session, user, project, raw):
    state = IntegrationOAuthState(
        state_hash=hashlib.sha256(raw.encode()).hexdigest(), user_id=user.id,
        project_id=project.id, provider="amocrm", oauth_client_key="amocrm-public-v1",
        connection_snapshot={"version": 1, "connection_id": None, "grant_revision": None},
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    session.add(state)
    await session.commit()
    return state


async def _callback(session, user, raw):
    return await app_integrations.integration_oauth_callback(
        "amocrm", session, user, state=raw, code="synthetic-code", error=None,
        referer="synthetic.amocrm.ru",
    )


def _provider(monkeypatch, handler):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _CLIENT(
        **kwargs, transport=httpx.MockTransport(handler),
    ))


@pytest.mark.parametrize("same_state", [True, False])
async def test_two_callbacks_exchange_once_with_stale_identity_maps(
    db_session, test_engine, monkeypatch, profiles, same_state,
):
    owner, project = await _owner(db_session)
    one = await _state(db_session, owner, project, "first-synthetic-state-value")
    two = one if same_state else await _state(
        db_session, owner, project, "second-synthetic-state-value",
    )
    # Disable public starts after authorization: the persisted profile still wins.
    profiles.integration_amocrm_public_oauth_enabled = False
    entered, release = asyncio.Event(), asyncio.Event()
    exchanges = []

    async def provider(request):
        if request.method == "GET":
            return httpx.Response(200, json={"name": "Synthetic"})
        exchanges.append(json.loads(request.content))
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"access_token": "new-access",
                                        "refresh_token": "new-refresh", "expires_in": 3600})

    _provider(monkeypatch, provider)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as first, factory() as second:
        await first.get(IntegrationOAuthState, one.id)
        await second.get(IntegrationOAuthState, two.id)
        first_task = asyncio.create_task(_callback(first, owner, "first-synthetic-state-value"))
        await asyncio.wait_for(entered.wait(), 5)
        second_task = asyncio.create_task(_callback(
            second, owner,
            "first-synthetic-state-value" if same_state else "second-synthetic-state-value",
        ))
        await asyncio.sleep(0.1)
        release.set()
        outcomes = await asyncio.wait_for(
            asyncio.gather(first_task, second_task, return_exceptions=True), 10,
        )
        assert outcomes[0].headers["location"].endswith("oauth=connected")
        if same_state:
            assert isinstance(outcomes[1], ApiError)
            assert outcomes[1].code == "integration_oauth_state_invalid"
        else:
            assert outcomes[1].headers["location"].endswith("oauth=conflict")
    assert len(exchanges) == 1
    assert exchanges[0]["client_id"] == "synthetic-public"
    assert exchanges[0]["redirect_uri"] == "https://public.example.test/callback"
    connection = (await db_session.execute(select(AccountIntegration))).scalar_one()
    assert connection.oauth_client_key == "amocrm-public-v1"
    assert connection.grant_revision == 1
    assert json.loads(decrypt_strong(connection.credentials_enc))["refresh_token"] == "new-refresh"


async def test_existing_connection_start_rejected_without_state_or_grant_mutation(
    db_session, monkeypatch, profiles,
):
    owner, project = await _owner(db_session)
    connection = AccountIntegration(user_id=owner.id, created_by_user_id=owner.id,
                                    provider="amocrm", credentials_enc="working-ciphertext")
    db_session.add(connection)
    await db_session.commit()
    monkeypatch.setattr(app_integrations, "require_existing_max_account_access", lambda _: None)
    monkeypatch.setattr(app_integrations, "assert_integrations_allowed", AsyncMock())
    with pytest.raises(ApiError) as caught:
        await app_integrations.start_integration_oauth(project.id, "amocrm", db_session, owner)
    assert caught.value.status_code == 409
    assert caught.value.code == "account_connection_replacement_requires_confirmation"
    assert (await db_session.scalar(select(func.count()).select_from(IntegrationOAuthState))) == 0
    assert connection.credentials_enc == "working-ciphertext"


@pytest.mark.parametrize("key,client", [(None, "legacy"), ("amocrm-public-v1", "public")])
async def test_concurrent_amo_refresh_pins_profile_and_rotates_once(
    db_session, test_engine, monkeypatch, profiles, key, client,
):
    owner, _ = await _owner(db_session)
    connection = AccountIntegration(
        user_id=owner.id, created_by_user_id=owner.id, provider="amocrm", auth_mode="oauth",
        oauth_client_key=key, public_config={"base_url": "https://synthetic.amocrm.ru"},
        credentials_enc=encrypt_strong(json.dumps({"access_token": "old", "refresh_token": "old"})),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(connection)
    await db_session.commit()
    profiles.integration_amocrm_public_oauth_enabled = client != "public"
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def provider(request):
        requests.append(json.loads(request.content))
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated",
                                        "expires_in": 3600})

    _provider(monkeypatch, provider)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as first, factory() as second:
        first_stale = await first.get(AccountIntegration, connection.id)
        second_stale = await second.get(AccountIntegration, connection.id)
        task_one = asyncio.create_task(load_credentials(first, first_stale))
        await asyncio.wait_for(entered.wait(), 5)
        task_two = asyncio.create_task(load_credentials(second, second_stale))
        await asyncio.sleep(0.1)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(task_one, task_two), 10)
        assert results == [{"access_token": "new", "refresh_token": "rotated"}] * 2
    assert len(requests) == 1
    assert requests[0]["client_id"] == f"synthetic-{client}"
    await db_session.refresh(connection)
    assert connection.grant_revision == 0
    assert connection.oauth_client_key == key


@pytest.mark.parametrize("first_operation", ["callback", "refresh"])
async def test_callback_conflict_and_refresh_serialize_without_losing_rotation(
    db_session, test_engine, monkeypatch, profiles, first_operation,
):
    owner, project = await _owner(db_session)
    await _state(db_session, owner, project, "pending-synthetic-state-value")
    connection = AccountIntegration(
        user_id=owner.id, created_by_user_id=owner.id, provider="amocrm", auth_mode="oauth",
        public_config={"base_url": "https://synthetic.amocrm.ru"},
        credentials_enc=encrypt_strong(json.dumps({"access_token": "old", "refresh_token": "old"})),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(connection)
    await db_session.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    owned = app_integrations._owned_max_project

    async def delayed_owned(*args):
        if first_operation == "callback":
            entered.set()
            await release.wait()
        return await owned(*args)

    monkeypatch.setattr(app_integrations, "_owned_max_project", delayed_owned)

    async def provider(request):
        calls.append(json.loads(request.content))
        if first_operation == "refresh":
            entered.set()
            await release.wait()
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated",
                                        "expires_in": 3600})

    _provider(monkeypatch, provider)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as callback_session, factory() as refresh_session:
        stale = await refresh_session.get(AccountIntegration, connection.id)

        async def do_callback():
            return await _callback(callback_session, owner, "pending-synthetic-state-value")

        async def do_refresh():
            return await load_credentials(refresh_session, stale)

        operations = (do_callback, do_refresh) if first_operation == "callback" else (
            do_refresh, do_callback,
        )
        first = asyncio.create_task(operations[0]())
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(operations[1]())
        await asyncio.sleep(0.1)
        assert not second.done()
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 10)
    callback_result = results[0 if first_operation == "callback" else 1]
    assert callback_result.headers["location"].endswith("oauth=conflict")
    assert len(calls) == 1
    assert calls[0]["grant_type"] == "refresh_token"
    await db_session.refresh(connection)
    assert json.loads(decrypt_strong(connection.credentials_enc))["refresh_token"] == "rotated"
    assert connection.grant_revision == 0


async def test_owner_boundary_and_connection_created_after_start_preserve_both_grants(
    db_session, monkeypatch, profiles,
):
    owner, project = await _owner(db_session)
    state = await _state(db_session, owner, project, "pending-synthetic-state-value")
    stranger = User(email="amo-other-owner@example.test")
    db_session.add(stranger)
    await db_session.flush()
    connections = [AccountIntegration(
        user_id=user.id, created_by_user_id=user.id, provider="amocrm",
        credentials_enc=f"synthetic-working-{number}",
    ) for number, user in enumerate((owner, stranger))]
    db_session.add_all(connections)
    await db_session.commit()

    def forbidden(request):
        pytest.fail("stale or foreign callback made provider HTTP")

    _provider(monkeypatch, forbidden)
    with pytest.raises(ApiError) as caught:
        await _callback(db_session, stranger, "pending-synthetic-state-value")
    assert caught.value.code == "integration_oauth_state_invalid"
    assert state.used_at is None
    # Separate request transaction; the rejected stranger request releases its lock.
    await db_session.commit()
    response = await _callback(db_session, owner, "pending-synthetic-state-value")
    assert response.headers["location"].endswith("oauth=conflict")
    assert [item.credentials_enc for item in connections] == [
        "synthetic-working-0", "synthetic-working-1",
    ]
    assert (await db_session.scalar(select(func.count()).select_from(AccountIntegration))) == 2


async def test_actual_start_persists_public_profile_and_absence_through_default_switch(
    db_session, monkeypatch, profiles,
):
    owner, project = await _owner(db_session)
    monkeypatch.setattr(app_integrations, "require_existing_max_account_access", lambda _: None)
    monkeypatch.setattr(app_integrations, "assert_integrations_allowed", AsyncMock())
    started = await app_integrations.start_integration_oauth(
        project.id, "amocrm", db_session, owner,
    )
    params = parse_qs(urlparse(started.authorization_url).query)
    assert params["client_id"] == ["synthetic-public"]
    state = (await db_session.execute(select(IntegrationOAuthState))).scalar_one()
    assert state.oauth_client_key == "amocrm-public-v1"
    assert state.connection_snapshot == {
        "version": 1, "connection_id": None, "grant_revision": None,
    }
    profiles.integration_amocrm_public_oauth_enabled = False
    calls = []

    def provider(request):
        if request.method == "GET":
            return httpx.Response(200, json={"name": "Synthetic"})
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "new",
                                        "expires_in": 3600})

    _provider(monkeypatch, provider)
    result = await _callback(db_session, owner, params["state"][0])
    assert result.headers["location"].endswith("oauth=connected")
    assert calls[0]["client_id"] == "synthetic-public"
    assert calls[0]["redirect_uri"] == "https://public.example.test/callback"


@pytest.mark.parametrize("profile_failure", ["unknown", "missing"])
async def test_platform_profile_error_preserves_working_grant_and_status(
    db_session, monkeypatch, profiles, profile_failure,
):
    owner, _ = await _owner(db_session)
    connection = AccountIntegration(
        user_id=owner.id, created_by_user_id=owner.id, provider="amocrm", auth_mode="oauth",
        oauth_client_key="unknown" if profile_failure == "unknown" else "amocrm-public-v1",
        public_config={"base_url": "https://synthetic.amocrm.ru"},
        credentials_enc=encrypt_strong(json.dumps({"access_token": "old", "refresh_token": "old"})),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(connection)
    await db_session.commit()
    ciphertext = connection.credentials_enc
    if profile_failure == "missing":
        profiles.integration_amocrm_public_client_secret = None

    def forbidden(request):
        pytest.fail("bad platform profile initiated provider HTTP")

    _provider(monkeypatch, forbidden)
    with pytest.raises(ApiError) as caught:
        await load_credentials(db_session, connection)
    assert caught.value.code == "integration_provider_unavailable"
    await db_session.refresh(connection)
    assert connection.credentials_enc == ciphertext
    assert connection.status == "active"
    assert connection.last_error is None

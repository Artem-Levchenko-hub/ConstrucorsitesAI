"""PostgreSQL transaction proof for account-wide YooKassa grants, using synthetic HTTP only."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.crypto import encrypt_strong
from yleum_api.core.errors import ApiError
from yleum_api.models.app_integration import (
    AccountIntegration,
    IntegrationOAuthState,
    ProjectIntegrationBinding,
)
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.routers import app_integrations
from yleum_api.services import integration_oauth

_CLIENT = httpx.AsyncClient


@pytest.fixture
def oauth(monkeypatch):
    monkeypatch.setattr(app_integrations, "require_existing_max_account_access", lambda _: None)
    monkeypatch.setattr(app_integrations, "assert_integrations_allowed", AsyncMock())
    monkeypatch.setattr(
        integration_oauth, "credentials",
        lambda _: integration_oauth.OAuthCredentials("synthetic-client", "synthetic-secret"),
    )

    def install(handler):
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _CLIENT(
            **kwargs, transport=httpx.MockTransport(handler),
        ))

    return install


async def _account(session):
    owner = User(email="yookassa-concurrency@example.test")
    session.add(owner)
    await session.flush()
    projects = [Project(
        owner_id=owner.id, name=f"Synthetic {index}", slug=f"yoo-concurrency-{index}",
        template="max_miniapp",
    ) for index in (1, 2)]
    session.add_all(projects)
    await session.commit()
    return owner, projects


async def _start(session, owner, project):
    started = await app_integrations.start_integration_oauth(
        project.id, "yookassa", session, owner,
    )
    return parse_qs(urlparse(started.authorization_url).query)["state"][0]


async def _callback(session, owner, state):
    return await app_integrations.integration_oauth_callback(
        "yookassa", session, owner, state=state, code="synthetic-code", error=None, referer=None,
    )


@pytest.mark.parametrize("same_state", [True, False])
async def test_concurrent_initial_callbacks_exchange_only_the_current_grant(
    db_session, test_engine, oauth, same_state,
):
    owner, projects = await _account(db_session)
    first_state = await _start(db_session, owner, projects[0])
    second_state = first_state if same_state else await _start(db_session, owner, projects[1])
    entered, release = asyncio.Event(), asyncio.Event()
    exchanges = []

    async def provider(request):
        if request.method == "GET":
            return httpx.Response(200, json={"account_id": "123"})
        exchanges.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"access_token": "synthetic-access", "expires_in": 3600})

    oauth(provider)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as first, factory() as second:
        # Both sessions preload the state so SELECT's populate_existing matters.
        first_states = (await first.scalars(select(IntegrationOAuthState))).all()
        second_states = (await second.scalars(select(IntegrationOAuthState))).all()
        assert first_states and second_states
        first_task = asyncio.create_task(_callback(first, owner, first_state))
        await asyncio.wait_for(entered.wait(), 5)
        second_task = asyncio.create_task(_callback(second, owner, second_state))
        try:
            await asyncio.sleep(0.1)
        finally:
            release.set()
        outcomes = await asyncio.wait_for(asyncio.gather(
            first_task, second_task, return_exceptions=True,
        ), 10)
    assert outcomes[0].headers["location"].endswith("oauth=connected")
    if same_state:
        assert isinstance(outcomes[1], ApiError)
        assert outcomes[1].code == "integration_oauth_state_invalid"
    else:
        assert outcomes[1].headers["location"].endswith("oauth=conflict")
    assert len(exchanges) == 1
    connection = (await db_session.execute(select(AccountIntegration))).scalar_one()
    bindings = (await db_session.execute(select(ProjectIntegrationBinding))).scalars().all()
    assert connection.public_config == {"shop_id": "123"}
    assert connection.grant_revision == 1
    assert len(bindings) == 1
    assert bindings[0].project_id == projects[0].id


@pytest.mark.parametrize("shop", ["123", "456"])
async def test_reconnect_preserves_merchant_for_every_bound_app(db_session, oauth, shop):
    owner, projects = await _account(db_session)
    ciphertext = encrypt_strong(json.dumps({"access_token": "synthetic-old"}))
    connection = AccountIntegration(
        user_id=owner.id, created_by_user_id=owner.id, provider="yookassa", auth_mode="oauth",
        public_config={"shop_id": "123"}, credentials_enc=ciphertext,
        verified_at=datetime.now(UTC), grant_revision=3,
    )
    db_session.add(connection)
    await db_session.flush()
    for project in projects:
        await app_integrations._bind(db_session, project.id, connection)
    await db_session.commit()
    raw = await _start(db_session, owner, projects[1])
    oauth(lambda request: httpx.Response(200, json=(
        {"access_token": "synthetic-new", "expires_in": 3600}
        if request.method == "POST" else {"account_id": shop}
    )))
    outcome = await _callback(db_session, owner, raw)
    await db_session.refresh(connection)
    bindings = (await db_session.execute(select(ProjectIntegrationBinding))).scalars().all()
    assert connection.public_config == {"shop_id": "123"}
    assert {binding.project_id for binding in bindings} == {project.id for project in projects}
    assert all(binding.integration_id == connection.id for binding in bindings)
    if shop == "123":
        assert outcome.headers["location"].endswith("oauth=connected")
        assert connection.grant_revision == 4
        assert connection.credentials_enc != ciphertext
    else:
        assert outcome.headers["location"].endswith("oauth=conflict")
        assert connection.grant_revision == 3
        assert connection.credentials_enc == ciphertext

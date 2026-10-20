"""Real ASGI auth boundaries with isolated session rows and no external services."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError

from yleum_api.core.db import get_session
from yleum_api.core.deps import CurrentUserDep
from yleum_api.core.errors import (
    ApiError,
    api_error_handler,
    validation_error_handler,
)
from yleum_api.core.security import create_access_token
from yleum_api.models.account import AuthSession
from yleum_api.models.app_integration import ProjectIntegrationBinding
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.routers import app_integrations, auth, projects, ws
from yleum_api.services.ws_hub import WebSocketHub


@pytest.fixture
def site(monkeypatch):
    user = User(
        id=uuid4(),
        email="transport@example.com",
        status="active",
        session_version=0,
        created_at=datetime.now(UTC),
        is_anon=False,
        role="user",
    )
    login = AuthSession(
        id=uuid4(),
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    project = Project(
        id=uuid4(),
        owner_id=user.id,
        name="Security QA",
        slug="security-qa",
        template="max_miniapp",
    )
    run = GenerationRun(id=uuid4(), project_id=project.id, user_id=user.id, status="running")
    rows = {User: user, AuthSession: login, Project: project, GenerationRun: run}

    class Session:
        async def get(self, model, identifier):
            row = rows.get(model)
            return row if row is not None and row.id == identifier else None

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

        async def scalar(self, _statement):
            return None

        async def delete(self, row):
            app.state.deleted.append(row)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    session = Session()

    async def sessions():
        yield session

    async def initial(socket, _project_id):
        await socket.send_json({"type": "qa.initial"})

    app = FastAPI()
    app.state.qa_run = run
    app.state.deleted = []
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(auth.router)
    app.include_router(ws.router)
    app.include_router(app_integrations.router)
    app.include_router(projects.router)
    app.dependency_overrides[get_session] = sessions

    @app.get("/qa/current-user")
    async def current_user(owner: CurrentUserDep):
        return {"id": str(owner.id)}

    monkeypatch.setattr(ws, "get_engine", lambda: None)
    monkeypatch.setattr(ws, "async_sessionmaker", lambda *_a, **_kw: lambda: session)
    monkeypatch.setattr(ws, "_send_stream_sync", initial)
    monkeypatch.setattr(ws, "hub", WebSocketHub())
    monkeypatch.setattr(ws, "_AUTH_RECHECK_SECONDS", 0.01, raising=False)
    token = create_access_token(user.id, session_id=login.id, session_version=0)
    return app, user, login, project, token


async def test_legacy_account_can_manage_only_its_existing_integration_without_max_admission(
    site,
    monkeypatch,
):
    app, user, _login, project, token = site
    user.email_verified_at = datetime.now(UTC)  # Historical hint, no positive verification receipt.
    binding = ProjectIntegrationBinding(id=uuid4(), project_id=project.id, provider="aitunnel")
    lookup = AsyncMock(return_value=binding)
    monkeypatch.setattr(app_integrations, "_binding", lookup)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://qa",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        denied = await client.post("/api/projects", json={"name": "Cannot bypass admission"})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "email_verification_required"
        managed = await client.delete(f"/api/projects/{project.id}/app-integrations/aitunnel")
        assert managed.status_code == 204
        assert app.state.deleted == [binding]

        project.owner_id = uuid4()
        assert (
            await client.delete(
                f"/api/projects/{project.id}/app-integrations/aitunnel",
            )
        ).status_code == 404
        assert lookup.await_count == 1 and app.state.deleted == [binding]


async def test_new_unverified_registration_cannot_manage_integrations(site, monkeypatch):
    app, user, _login, project, token = site
    user.email_verified_at = None
    lookup = AsyncMock()
    monkeypatch.setattr(app_integrations, "_binding", lookup)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://qa",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        denied = await client.delete(f"/api/projects/{project.id}/app-integrations/aitunnel")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "email_verification_required"
    lookup.assert_not_awaited()
    assert app.state.deleted == []


@pytest.mark.parametrize("product", [None, "general"])
async def test_registration_cannot_self_certify_email_before_persisting_account(
    site,
    monkeypatch,
    product,
):
    app, user, login, *_ = site
    persisted = []

    async def create(_session, **values):
        persisted.append(values)
        user.email_verified_at = values["email_verified_at"]
        return user

    async def open_login(*_args):
        return login

    async def password_hash(_password):
        return "private-test-hash"

    monkeypatch.setattr(auth, "create_account", create)
    monkeypatch.setattr(auth, "open_session", open_login)
    monkeypatch.setattr(auth, "hash_password", password_hash)
    payload = {
        "email": "transport@example.com",
        "password": "private-test-password-123",
        "role": "admin",
    }
    if product is not None:
        payload["product"] = product
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://qa") as c:
        registered = await c.post("/api/auth/register", json=payload)
    assert registered.status_code == 201
    assert "omnia_session" in registered.cookies
    assert len(persisted) == 1 and persisted[0]["email_verified_at"] is None
    assert "role" not in persisted[0] and registered.json()["role"] == "user"
    assert registered.json()["email_verified_at"] is None


class Socket:
    def __init__(self, app, project, token, transport="cookie", *, run_id=None):
        self.app = app
        self.scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "path": f"/api/ws/projects/{project.id}",
            "root_path": "",
            "scheme": "ws",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "query_string": f"token={token}".encode() if transport == "query" else b"",
            "headers": [(b"cookie", f"omnia_session={token}".encode())]
            if transport == "cookie"
            else [],
            "subprotocols": [],
        }
        if run_id:
            self.scope["query_string"] += (
                b"&" if self.scope["query_string"] else b""
            ) + f"run_id={run_id}".encode()
        self.inbound = asyncio.Queue()
        self.inbound.put_nowait({"type": "websocket.connect"})
        self.messages = []
        self.ready = asyncio.Event()
        self.closed = asyncio.Event()

    async def send(self, message):
        self.messages.append(message)
        if message["type"] in {"websocket.accept", "websocket.close"}:
            self.ready.set()
        if message["type"] == "websocket.close":
            self.closed.set()

    async def __aenter__(self):
        self.task = asyncio.create_task(self.app(self.scope, self.inbound.get, self.send))
        await asyncio.wait_for(self.ready.wait(), timeout=1)
        return self

    async def __aexit__(self, *_args):
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task


@pytest.mark.parametrize("transport", ["cookie", "query"])
@pytest.mark.parametrize("invalid", ["revoked", "expired", "version", "disabled", "missing"])
async def test_websocket_rejects_every_session_rejected_by_http(site, transport, invalid):
    app, user, login, project, token = site
    if invalid == "revoked":
        login.revoked_at = datetime.now(UTC)
    elif invalid == "expired":
        login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif invalid == "version":
        user.session_version += 1
    elif invalid == "disabled":
        user.status = "disabled"
    else:
        login.id = uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://qa") as c:
        denied = await c.get("/qa/current-user", headers={"Authorization": f"Bearer {token}"})
    assert denied.status_code == 401
    async with Socket(app, project, token, transport) as socket:
        assert socket.messages == [{"type": "websocket.close", "code": 1008, "reason": ""}]


async def test_valid_owner_socket_accepts_and_other_owner_does_not(site):
    app, _user, _login, project, token = site
    async with Socket(app, project, token) as socket:
        assert socket.messages[0]["type"] == "websocket.accept"
    project.owner_id = uuid4()
    async with Socket(app, project, token) as socket:
        assert socket.messages[0]["type"] == "websocket.close"


async def test_valid_socket_survives_periodic_checks_and_cleans_up_on_disconnect(site):
    app, _user, _login, project, token = site
    tasks_before = asyncio.all_tasks()
    async with Socket(app, project, token) as socket:
        await asyncio.sleep(0.05)
        assert not socket.closed.is_set()
        assert ws.hub._connections.get(project.id)
        socket.inbound.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(socket.task, timeout=0.5)
    assert not ws.hub._connections.get(project.id)
    assert not [
        task
        for task in asyncio.all_tasks() - tasks_before
        if task.get_name().startswith("project-socket-")
    ]


@pytest.mark.parametrize("action", ["logout", "revoke", "version", "disabled", "expired"])
async def test_open_socket_closes_after_session_becomes_invalid_without_client_ping(site, action):
    app, user, login, project, token = site
    async with Socket(app, project, token) as socket:
        assert socket.messages[0]["type"] == "websocket.accept"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://qa",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            if action == "logout":
                assert (await client.post("/api/auth/logout")).status_code == 204
            elif action == "revoke":
                assert (await client.delete(f"/api/auth/sessions/{login.id}")).status_code == 204
            elif action == "version":
                user.session_version += 1
            elif action == "disabled":
                user.status = "disabled"
            else:
                login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await asyncio.wait_for(socket.closed.wait(), timeout=0.5)
        assert socket.messages[-1]["code"] == 1008


async def test_client_pings_cannot_postpone_revocation_check(site):
    app, _user, login, project, token = site
    async with Socket(app, project, token) as socket:
        login.revoked_at = datetime.now(UTC)

        async def ping():
            while not socket.closed.is_set():
                socket.inbound.put_nowait({"type": "websocket.receive", "text": '{"type":"ping"}'})
                await asyncio.sleep(0.001)

        pings = asyncio.create_task(ping())
        try:
            await asyncio.wait_for(socket.closed.wait(), timeout=0.5)
            assert socket.messages[-1]["code"] == 1008
        finally:
            pings.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pings


@pytest.mark.parametrize("failure", ["database", "transport", "timeout"])
async def test_open_socket_fails_closed_when_session_authority_is_unavailable(
    site,
    monkeypatch,
    failure,
):
    app, _user, _login, project, token = site
    async with Socket(app, project, token) as socket:

        async def unavailable(*_args):
            if failure == "database":
                raise SQLAlchemyError("private failure text must not reach the socket")
            if failure == "transport":
                raise OSError("private connection failure must not reach the socket")
            await asyncio.sleep(1)

        monkeypatch.setattr(ws, "resolve_session_user", unavailable)
        monkeypatch.setattr(ws, "_AUTH_CHECK_TIMEOUT_SECONDS", 0.02)
        await asyncio.wait_for(socket.closed.wait(), timeout=0.5)
        assert socket.messages[-1] == {"type": "websocket.close", "code": 1013, "reason": ""}


@pytest.mark.parametrize("stage", ["initial-sync", "initial-replay", "resync", "generation-resync"])
async def test_revocation_supervises_stalled_setup_and_replay_without_releasing_handler(
    site,
    monkeypatch,
    stage,
):
    app, _user, login, project, token = site
    blocked, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    replay = stage in {"initial-replay", "generation-resync"}
    run_id = app.state.qa_run.id if replay else None
    monkeypatch.setattr(
        ws, "get_settings", lambda: SimpleNamespace(use_generation_event_replay=replay)
    )
    calls = 0

    async def block():
        blocked.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def sync(socket, _project_id):
        nonlocal calls
        calls += 1
        if stage == "initial-sync" or (stage == "resync" and calls == 2):
            await block()
        else:
            await socket.send_json({"type": "qa.initial"})

    async def high_water(*_args, **_kwargs):
        return 0

    async def send_replay(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if stage == "initial-replay" or (stage == "generation-resync" and calls == 3):
            await block()

    monkeypatch.setattr(ws, "_send_stream_sync", sync)
    monkeypatch.setattr(ws, "generation_event_high_water", high_water)
    monkeypatch.setattr(ws, "_send_generation_replay", send_replay)
    tasks_before = asyncio.all_tasks()
    try:
        async with Socket(app, project, token, run_id=run_id) as socket:
            assert socket.messages[0]["type"] == "websocket.accept"
            if stage in {"resync", "generation-resync"}:
                message = (
                    {"type": "resync"}
                    if not replay
                    else {
                        "type": "generation.resync",
                        "run_id": str(run_id),
                        "after_seq": 0,
                    }
                )
                socket.inbound.put_nowait(
                    {"type": "websocket.receive", "text": json.dumps(message)}
                )
            await asyncio.wait_for(blocked.wait(), timeout=0.5)
            if stage in {"resync", "generation-resync"}:
                assert ws.hub._connections.get(project.id)
            login.revoked_at = datetime.now(UTC)
            await asyncio.wait_for(socket.closed.wait(), timeout=0.5)
            assert socket.messages[-1]["code"] == 1008
            assert not ws.hub._connections.get(project.id)
            assert not release.is_set()
            await asyncio.wait_for(cancelled.wait(), timeout=0.5)
        await asyncio.sleep(0)
        assert not [
            task
            for task in asyncio.all_tasks() - tasks_before
            if task.get_name().startswith("project-socket-")
        ]
    finally:
        release.set()


@pytest.mark.parametrize(
    "path,payload,private",
    [
        ("register", {"email": "qa@example.com", "password": "QA1"}, "QA1"),
        (
            "register",
            {"email": "qa@example.com", "password": "QA_PRIVATE_NO_DIGIT"},
            "QA_PRIVATE_NO_DIGIT",
        ),
        ("login", {"email": "qa@example.com", "password": "QA_PRIVATE_1" * 20}, "QA_PRIVATE_1"),
        ("password/reset", {"token": "QA_PRIVATE_TOKEN", "password": "QA1"}, "QA_PRIVATE_TOKEN"),
        (
            "register",
            {"email": "qa@example.com", "password": {"private": "QA_PRIVATE_1"}},
            "QA_PRIVATE_1",
        ),
    ],
)
async def test_auth_validation_response_never_reflects_password_or_request_body(
    site, path, payload, private
):
    app, *_ = site
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://qa") as c:
        response = await c.post(f"/api/auth/{path}", json=payload)
    assert response.status_code == 422
    assert private not in response.text
    body = response.json()["error"]
    assert body["code"] == "validation_failed"
    assert all("input" not in item and "ctx" not in item for item in body["details"]["errors"])
    assert json.loads(response.text) == response.json()

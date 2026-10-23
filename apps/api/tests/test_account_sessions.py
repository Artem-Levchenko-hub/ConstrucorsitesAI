"""Login session identity comes from signed credentials, never a device fingerprint."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yleum_api.core import security
from yleum_api.core.db import get_session
from yleum_api.models.account import AuthSession
from yleum_api.models.user import User
from yleum_api.routers import auth
from yleum_api.services import account_sessions


class SessionRows:
    """In-memory persistence boundary; token and session lifecycle remain real."""

    def __init__(self, *rows: AuthSession):
        self.rows = {row.id: row for row in rows}

    async def get(self, model, identifier):
        assert model is AuthSession
        return self.rows.get(identifier)

    def add(self, row):
        row.id = uuid4()
        self.rows[row.id] = row

    async def flush(self):
        pass


@pytest.fixture
def identity(monkeypatch):
    settings = SimpleNamespace(
        jwt_cookie_name="omnia_session",
        jwt_secret=SecretStr("account-session-regression-secret-with-32-bytes"),
        jwt_algorithm="HS256",
        jwt_ttl_days=7,
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(account_sessions, "get_settings", lambda: settings)
    now = datetime.now(UTC)
    user = User(id=uuid4(), status="active", session_version=2)
    row = AuthSession(
        id=uuid4(),
        user_id=user.id,
        user_agent="Old browser",
        ip_address="192.0.2.1",
        created_at=now - timedelta(days=2),
        last_seen_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=1),
    )
    return user, row


def request(token=None, *, transport="cookie"):
    headers = [(b"user-agent", b"Updated browser"), (b"x-forwarded-for", b"192.0.2.2")]
    if token:
        name, value = (
            (b"cookie", f"omnia_session={token}")
            if transport == "cookie"
            else (b"authorization", f"Bearer {token}")
        )
        headers.append((name, value.encode()))
    return Request({"type": "http", "headers": headers, "client": ("127.0.0.1", 1234)})


@pytest.mark.parametrize("transport", ["cookie", "bearer"])
async def test_relogin_reuses_only_authenticated_session_and_renews_activity(identity, transport):
    user, existing = identity
    original_created_at = existing.created_at
    previous_expiry = existing.expires_at
    token = security.create_access_token(user.id, session_id=existing.id, session_version=2)
    store = SessionRows(existing)

    reopened = await account_sessions.open_session(store, user, request(token, transport=transport))

    assert reopened is existing
    assert list(store.rows) == [existing.id]
    assert existing.created_at == original_created_at
    assert existing.last_seen_at > original_created_at + timedelta(days=1)
    assert existing.expires_at > previous_expiry + timedelta(days=5)
    assert existing.user_agent == "Updated browser"
    assert existing.ip_address == "192.0.2.2"


@pytest.mark.parametrize(
    "credential",
    [
        "missing",
        "malformed",
        "foreign_user",
        "foreign_row",
        "missing_row",
        "revoked",
        "expired",
        "old_version",
        "legacy_without_version",
        "legacy_without_sid",
    ],
)
async def test_relogin_does_not_reuse_unproven_or_invalid_session(identity, credential):
    user, existing = identity
    owner_id, session_id, version = user.id, existing.id, 2
    if credential == "foreign_user":
        owner_id = uuid4()
    elif credential == "foreign_row":
        existing.user_id = uuid4()
    elif credential == "missing_row":
        session_id = uuid4()
    elif credential == "revoked":
        existing.revoked_at = datetime.now(UTC)
    elif credential == "expired":
        existing.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif credential == "old_version":
        version = 1
    elif credential == "legacy_without_version":
        version = None
    elif credential == "legacy_without_sid":
        session_id = None
    token = security.create_access_token(owner_id, session_id=session_id, session_version=version)
    if credential == "missing":
        token = None
    elif credential == "malformed":
        token = "not-a-jwt"
    store = SessionRows(existing)
    original_expiry = existing.expires_at
    original_revoked_at = existing.revoked_at

    reopened = await account_sessions.open_session(store, user, request(token))

    assert reopened.id != existing.id
    assert reopened.user_id == user.id
    assert len(store.rows) == 2
    assert existing.expires_at == original_expiry
    assert existing.revoked_at == original_revoked_at


async def test_same_user_agent_and_ip_do_not_merge_independent_browsers(identity):
    user, _existing = identity
    store = SessionRows()

    first = await account_sessions.open_session(store, user, request())
    second = await account_sessions.open_session(store, user, request())

    assert first.user_agent == second.user_agent
    assert first.ip_address == second.ip_address
    assert first.id != second.id
    assert len(store.rows) == 2


async def test_repeated_password_login_keeps_one_session_in_http_response(identity):
    user, existing = identity
    user.email = "session-owner@example.com"
    user.password_hash = await security.hash_password("secret123")
    user.is_anon = False
    user.role = "user"
    user.created_at = existing.created_at

    class LoginSession(SessionRows):
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: user)

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    store = LoginSession(existing)

    async def sessions():
        yield store

    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_session] = sessions
    token = security.create_access_token(user.id, session_id=existing.id, session_version=2)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://session.test"
    ) as client:
        client.cookies.set("omnia_session", token)
        for _ in range(2):
            response = await client.post(
                "/api/auth/login", json={"email": user.email, "password": "secret123"}
            )
            assert response.status_code == 200
            claims = security.decode_access_claims(response.cookies["omnia_session"])
            assert claims.session_id == existing.id
            assert claims.session_version == 2
    assert list(store.rows) == [existing.id]


async def test_session_list_omits_expired_history_without_hiding_other_active_devices(identity):
    user, current = identity
    now = datetime.now(UTC)
    other_active = AuthSession(
        id=uuid4(),
        user_id=user.id,
        user_agent=current.user_agent,
        ip_address=current.ip_address,
        expires_at=now + timedelta(days=1),
    )
    expired = AuthSession(id=uuid4(), user_id=user.id, expires_at=now - timedelta(days=1))
    revoked = AuthSession(
        id=uuid4(),
        user_id=user.id,
        expires_at=now + timedelta(days=1),
        revoked_at=now,
    )
    foreign = AuthSession(id=uuid4(), user_id=uuid4(), expires_at=now + timedelta(days=1))
    token = security.create_access_token(user.id, session_id=current.id, session_version=2)
    engine = create_engine("sqlite://")
    AuthSession.__table__.create(engine)
    try:
        with Session(engine) as stored:
            stored.add_all([current, other_active, expired, revoked, foreign])
            stored.flush()

            class QuerySession:
                async def execute(self, statement):
                    return stored.execute(statement)

            result = await auth.list_sessions(user, QuerySession(), token)

            assert {row.id for row in result} == {current.id, other_active.id}
            assert {row.id for row in result if row.current} == {current.id}
            assert stored.get(AuthSession, expired.id).revoked_at is None
            assert stored.get(AuthSession, other_active.id).revoked_at is None
    finally:
        engine.dispose()

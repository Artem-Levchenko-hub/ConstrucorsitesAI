"""Actual shared authorization rejects credentials without revocable sessions."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr

from yleum_api.core import admin, deps, security
from yleum_api.core.errors import ApiError
from yleum_api.models.account import AuthSession
from yleum_api.models.user import User


class Rows:
    def __init__(self, user, login):
        self.rows = {(User, user.id): user, (AuthSession, login.id): login}
        self.reads = []

    async def get(self, model, identity):
        self.reads.append((model, identity))
        return self.rows.get((model, identity))


@pytest.fixture
def authority(monkeypatch):
    settings = SimpleNamespace(
        jwt_secret=SecretStr("synthetic-session-authority-secret-32-bytes"),
        jwt_algorithm="HS256",
        jwt_ttl_days=7,
        admin_emails_set={"bootstrap@example.org"},
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    # Before the fix this is the actual bootstrap authority; after it, the
    # unused setting cannot grant rights, whether the email is verified or not.
    monkeypatch.setattr(admin, "get_settings", lambda: settings, raising=False)
    user = User(
        id=uuid4(),
        role="user",
        status="active",
        session_version=1,
        email="bootstrap@example.org",
        password_hash="untouched-password-hash",
    )
    login = AuthSession(
        id=uuid4(),
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        revoked_at=None,
    )
    return settings, user, login, Rows(user, login)


def credential(settings, user, login, **overrides):
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "sid": str(login.id),
        "ver": user.session_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    payload.update(overrides)
    payload = {key: value for key, value in payload.items() if value != "__missing__"}
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")


@pytest.mark.parametrize(
    "claims",
    [
        {"sid": "__missing__", "ver": "__missing__"},
        {"sid": "__missing__"},
        {"ver": "__missing__"},
        {"sid": None},
        {"sid": 12},
        {"sid": "invalid"},
        {"ver": None},
        {"ver": True},
        {"ver": False},
        {"ver": -1},
        {"ver": "1"},
    ],
)
@pytest.mark.parametrize("transport", ["cookie", "bearer"])
async def test_incomplete_or_malformed_claims_never_reach_identity_lookup(
    authority, claims, transport
):
    settings, user, login, rows = authority
    token = credential(settings, user, login, **claims)
    cookie, bearer = (token, None) if transport == "cookie" else (None, "Bearer " + token)

    assert security.decode_access_claims(token) is None
    assert deps.extract_access_claims(cookie, bearer) is None
    assert await deps.resolve_session_user(rows, cookie, bearer) is None
    assert rows.reads == []


@pytest.mark.parametrize(
    "invalid", ["no_row", "foreign_owner", "revoked", "expired", "old_version", "suspended"]
)
async def test_session_and_user_authority_remain_mandatory(authority, invalid):
    settings, user, login, rows = authority
    token = credential(settings, user, login)
    if invalid == "no_row":
        rows.rows.pop((AuthSession, login.id))
    elif invalid == "foreign_owner":
        login.user_id = uuid4()
    elif invalid == "revoked":
        login.revoked_at = datetime.now(UTC)
    elif invalid == "expired":
        login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif invalid == "old_version":
        user.session_version += 1
    else:
        user.status = "suspended"

    assert await deps.resolve_session_user(rows, token, None) is None


@pytest.mark.parametrize("transport", ["cookie", "bearer"])
async def test_modern_session_resolves_without_changing_account_or_session(authority, transport):
    settings, user, login, rows = authority
    token = credential(settings, user, login)
    cookie, bearer = (token, None) if transport == "cookie" else (None, "Bearer " + token)
    expires = login.expires_at

    assert await deps.resolve_session_user(rows, cookie, bearer) is user
    assert rows.reads == [(User, user.id), (AuthSession, login.id)]
    assert user.password_hash == "untouched-password-hash" and user.session_version == 1
    assert login.expires_at == expires and login.revoked_at is None


@pytest.mark.parametrize("verified", [False, True])
async def test_allowlisted_email_never_grants_administrator(authority, verified):
    _, user, _, _ = authority
    user.email_verified_at = datetime.now(UTC) if verified else None

    assert admin.is_admin_user(user) is False
    with pytest.raises(ApiError) as rejected:
        await deps.get_admin_user(user)
    assert rejected.value.status_code == 403


async def test_persisted_administrator_does_not_depend_on_email_bootstrap(authority):
    _, user, _, _ = authority
    user.role, user.email = "admin", None
    assert admin.is_admin_user(user) is True
    assert await deps.get_admin_user(user) is user


async def test_actual_current_and_optional_dependencies_use_shared_authority(authority):
    _, user, login, rows = authority
    modern = security.create_access_token(user.id, session_id=login.id, session_version=1)
    assert await deps.get_current_user(rows, modern, None) is user
    assert await deps.get_optional_user(rows, modern, None) is user
    legacy = security.create_access_token(user.id)
    assert await deps.get_optional_user(rows, legacy, None) is None
    with pytest.raises(ApiError) as rejected:
        await deps.get_current_user(rows, legacy, None)
    assert rejected.value.status_code == 401

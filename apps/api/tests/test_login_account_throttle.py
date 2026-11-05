"""Account-wide login admission, independent of the source IP or email case."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from yleum_api.core import ratelimit
from yleum_api.core.config import get_settings
from yleum_api.core.db import get_session
from yleum_api.core.errors import ApiError, api_error_handler
from yleum_api.models.user import User
from yleum_api.routers import auth
from yleum_api.schemas.user import UserPublic


@pytest.fixture
def login_site(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "rate_limit_enabled": True,
            "auth_rate_limit": "1000/minute",
            "auth_ip_rate_limit": "1000/hour",
            "auth_account_rate_limit": "3/minute",
        }
    )
    monkeypatch.setattr(ratelimit, "get_settings", lambda: settings)
    user = User(
        id=uuid4(),
        email="owner@example.com",
        role="user",
        status="active",
        is_anon=False,
        session_version=0,
        password_hash="stored-hash",
        created_at=datetime.now(UTC),
    )
    calls = {"queries": 0, "password_checks": 0}

    class Session:
        async def execute(self, statement):
            calls["queries"] += 1
            email = statement.compile().params.get("email_1", "")
            return SimpleNamespace(
                scalar_one_or_none=lambda: user if email.lower() == user.email else None
            )

        def add(self, row):
            if row.id is None:
                row.id = uuid4()

        async def flush(self):
            pass

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    async def sessions():
        yield Session()

    async def verify(password, _hashed):
        calls["password_checks"] += 1
        return password == "existing-password-without-digit"

    async def dummy():
        calls["password_checks"] += 1

    async def public_user(_session, row):
        return UserPublic.model_validate(row)

    monkeypatch.setattr(auth, "verify_password", verify)
    monkeypatch.setattr(auth, "consume_dummy_verify", dummy)
    monkeypatch.setattr(auth, "_public_user", public_user)
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(auth.router)
    app.dependency_overrides[get_session] = sessions
    return app, user, calls


async def send_login(app, ip, *, email="owner@example.com", password="incorrect-password"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://qa"
    ) as client:
        return await client.post(
            "/api/auth/login",
            headers={"X-Real-IP": ip},
            json={"email": email, "password": password},
        )


@pytest.mark.parametrize("email", ["owner@example.com", "missing@example.com"])
async def test_account_limit_survives_ip_rotation_before_database_or_password_work(
    login_site, email
):
    app, _user, calls = login_site
    for index in range(3):
        response = await send_login(app, f"192.0.2.{index + 1}", email=email)
        assert response.status_code == 401
    before = calls.copy()
    denied = await send_login(app, "198.51.100.1", email=email.upper())
    assert denied.status_code == 429
    assert calls == before
    assert "owner@example.com" not in denied.text and "missing@example.com" not in denied.text


async def test_successful_existing_password_login_clears_account_admission(login_site):
    app, _user, _calls = login_site
    assert (await send_login(app, "192.0.2.10")).status_code == 401
    accepted = await send_login(app, "192.0.2.11", password="existing-password-without-digit")
    assert accepted.status_code == 200
    assert "omnia_session=" in accepted.headers["set-cookie"]
    assert (await send_login(app, "192.0.2.12")).status_code == 401
    assert (await send_login(app, "192.0.2.13")).status_code == 401


async def test_one_blocked_account_does_not_block_other_accounts(login_site):
    app, _user, _calls = login_site
    for index in range(3):
        assert (await send_login(app, f"192.0.2.{index + 1}")).status_code == 401
    assert (await send_login(app, "198.51.100.1")).status_code == 429
    response = await send_login(app, "198.51.100.2", email="another@example.com")
    assert response.status_code == 401


async def test_explicitly_disabled_rate_limiting_remains_disabled(login_site, monkeypatch):
    app, _user, _calls = login_site
    disabled = ratelimit.get_settings().model_copy(update={"rate_limit_enabled": False})
    monkeypatch.setattr(ratelimit, "get_settings", lambda: disabled)
    for _ in range(5):
        assert (await send_login(app, "192.0.2.1")).status_code == 401


async def test_account_key_is_casefolded_and_does_not_store_plaintext_email(
    login_site, monkeypatch
):
    captured = []
    monkeypatch.setattr(ratelimit._limiter, "hit", lambda _item, key: captured.append(key) or True)
    await ratelimit.rate_limit_login_account("Owner@Example.com")
    await ratelimit.rate_limit_login_account("owner@example.com")
    assert captured[0] == captured[1]
    assert "owner" not in captured[0].lower() and "example.com" not in captured[0].lower()


async def test_valid_recovery_clears_account_limit_but_invalid_token_does_not(
    login_site, monkeypatch
):
    from yleum_api.services import account_credentials

    app, user, _calls = login_site
    for index in range(3):
        assert (await send_login(app, f"192.0.2.{index + 1}")).status_code == 401

    async def token_user(_session, raw, _purpose, _now):
        if raw.startswith("invalid"):
            raise ApiError("token_invalid", "invalid token", 400)
        return user

    async def hash_new(_password):
        return "new-hash"

    async def apply_change(*_args, **_kwargs):
        pass

    monkeypatch.setattr(auth, "_consume_email_token", token_user)
    monkeypatch.setattr(auth, "hash_password", hash_new)
    monkeypatch.setattr(account_credentials, "apply_password_change", apply_change)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://qa"
    ) as client:
        invalid = await client.post(
            "/api/auth/password/reset",
            json={
                "token": "invalid" + "x" * 40,
                "password": "A new recovery phrase 2026",
            },
        )
        assert invalid.status_code == 400
        assert (await send_login(app, "198.51.100.2")).status_code == 429
        valid = await client.post(
            "/api/auth/password/reset",
            json={
                "token": "valid" + "x" * 40,
                "password": "A new recovery phrase 2026",
            },
        )
        assert valid.status_code == 200
    assert (await send_login(app, "198.51.100.3")).status_code == 401

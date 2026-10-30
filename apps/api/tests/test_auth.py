from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.security import create_access_token
from yleum_api.models.account import AuthSession
from yleum_api.models.user import User
from yleum_api.services.readiness import ReadinessReport


async def test_register_creates_user_and_sets_session_cookie(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "secret123"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert "id" in body
    assert "omnia_session" in r.cookies


async def test_register_rejects_short_password(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/register",
        json={"email": "a@b.com", "password": "abc1"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"


async def test_register_rejects_password_without_digit(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/register",
        json={"email": "a@b.com", "password": "alllettersnodigit"},
    )
    assert r.status_code == 422


async def test_register_rejects_invalid_email(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "secret123"},
    )
    assert r.status_code == 422


async def test_register_rejects_duplicate_email(client: httpx.AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": "secret123"}
    first = await client.post("/api/auth/register", json=payload)
    assert first.status_code == 201
    second = await client.post("/api/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


async def test_login_with_correct_credentials_sets_cookie(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/auth/register",
        json={"email": "bob@example.com", "password": "secret123"},
    )
    client.cookies.clear()
    r = await client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "secret123"},
    )
    assert r.status_code == 200
    assert "omnia_session" in r.cookies
    assert r.json()["email"] == "bob@example.com"


@pytest.mark.parametrize(
    ("password", "equivalent_password"),
    [
        pytest.param("secret123", "secret123", id="short"),
        pytest.param("a" * 71 + "1", "a" * 71 + "1extra", id="exactly-72-bytes"),
        pytest.param("a" * 72 + "1A", "a" * 72 + "1B", id="ascii-suffix"),
        pytest.param("я" * 36 + "1A", "я" * 36 + "1B", id="unicode-suffix"),
    ],
)
async def test_legacy_bcrypt_login_preserves_hash_and_existing_session(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    password: str,
    equivalent_password: str,
) -> None:
    legacy_context = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12)
    legacy_hash = legacy_context.hash(password)
    user = User(
        email="legacy@example.com",
        password_hash=legacy_hash,
        status="active",
        is_anon=False,
        session_version=3,
    )
    db_session.add(user)
    await db_session.flush()
    existing_session = AuthSession(
        user_id=user.id, expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(existing_session)
    await db_session.commit()
    existing_cookie = create_access_token(
        user.id, session_id=existing_session.id, session_version=user.session_version,
    )

    for accepted_password in (password, equivalent_password):
        login = await client.post(
            "/api/auth/login", json={"email": user.email, "password": accepted_password},
        )
        assert login.status_code == 200, login.text
        assert "omnia_session" in login.cookies

    await db_session.refresh(user)
    await db_session.refresh(existing_session)
    assert user.password_hash == legacy_hash
    assert user.session_version == 3
    assert existing_session.revoked_at is None
    client.cookies.clear()
    client.cookies.set("omnia_session", existing_cookie)
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_registration_and_login_preserve_a_long_password_suffix(
    client: httpx.AsyncClient,
) -> None:
    password = "🔐" * 127 + "1"
    registered = await client.post(
        "/api/auth/register", json={"email": "long@example.com", "password": password},
    )
    assert registered.status_code == 201
    client.cookies.clear()

    altered = await client.post(
        "/api/auth/login",
        json={"email": "long@example.com", "password": "🔐" * 127 + "2"},
    )
    assert altered.status_code == 401
    correct = await client.post(
        "/api/auth/login", json={"email": "long@example.com", "password": password},
    )
    assert correct.status_code == 200


async def test_login_wrong_password_returns_401(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/auth/register",
        json={"email": "carol@example.com", "password": "secret123"},
    )
    r = await client.post(
        "/api/auth/login",
        json={"email": "carol@example.com", "password": "wrongpass1"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_login_unknown_email_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/login",
        json={"email": "ghost@example.com", "password": "secret123"},
    )
    assert r.status_code == 401


async def test_me_returns_current_user(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/auth/register",
        json={"email": "dave@example.com", "password": "secret123"},
    )
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "dave@example.com"


async def test_me_without_cookie_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_me_with_invalid_cookie_returns_401(client: httpx.AsyncClient) -> None:
    client.cookies.set("omnia_session", "not-a-jwt")
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_logout_clears_cookie_and_subsequent_me_returns_401(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/api/auth/register",
        json={"email": "eve@example.com", "password": "secret123"},
    )
    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204
    me = await client.get("/api/auth/me")
    assert me.status_code == 401


async def test_health_endpoint_is_public(client: httpx.AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "yleum_api.main.get_settings",
        lambda: SimpleNamespace(omnia_release_sha="a7c4fc22"),
        raising=False,
    )
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "release_sha": "a7c4fc22"}


async def test_readiness_endpoint_reports_component_state(
    client: httpx.AsyncClient,
    monkeypatch,
) -> None:
    async def healthy() -> ReadinessReport:
        return ReadinessReport(
            checks={"database": "ok", "redis": "ok", "worker": "ok"},
            dependencies={
                "worker_release_sha": "a7c4fc22",
                "orchestrator_release_sha": "a7c4fc22",
            },
        )

    monkeypatch.setattr("yleum_api.services.readiness.probe_readiness", healthy)
    monkeypatch.setattr(
        "yleum_api.main.get_settings",
        lambda: SimpleNamespace(omnia_release_sha="a7c4fc22"),
    )
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "api",
        "release_sha": "a7c4fc22",
        "checks": {"database": "ok", "redis": "ok", "worker": "ok"},
        "dependencies": {
            "worker_release_sha": "a7c4fc22",
            "orchestrator_release_sha": "a7c4fc22",
        },
    }


async def test_readiness_endpoint_fails_when_worker_heartbeat_is_missing(
    client: httpx.AsyncClient,
    monkeypatch,
) -> None:
    async def degraded() -> ReadinessReport:
        return ReadinessReport(
            checks={"database": "ok", "redis": "ok", "worker": "failed"},
            dependencies={
                "worker_release_sha": "unknown",
                "orchestrator_release_sha": "a7c4fc22",
            },
        )

    monkeypatch.setattr("yleum_api.services.readiness.probe_readiness", degraded)
    monkeypatch.setattr(
        "yleum_api.main.get_settings",
        lambda: SimpleNamespace(omnia_release_sha="a7c4fc22"),
    )
    response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["worker"] == "failed"

from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import SecretStr

from yleum_api.core.config import get_settings
from yleum_api.core.deps import get_optional_user
from yleum_api.core.security import hash_password
from yleum_api.main import app
from yleum_api.models.user import User
from yleum_api.routers import monitoring_access

BASE = "/api/admin/monitoring"
SESSION = BASE + "/session"
PASSWORD = "local monitoring test password only"


async def test_separate_viewer_session_grants_only_monitoring_and_can_be_revoked(
    client, monkeypatch,
):
    sessions: dict[str, str] = {}

    async def save(key: str, value: str, *, ex: int) -> None:
        assert ex == 28800
        sessions[key] = value

    async def delete(key: str) -> None:
        sessions.pop(key, None)

    monkeypatch.setattr(monitoring_access, "get_redis", lambda: SimpleNamespace(
        set=AsyncMock(side_effect=save), get=AsyncMock(side_effect=sessions.get),
        delete=AsyncMock(side_effect=delete),
    ))
    settings = get_settings()
    secret = await hash_password(PASSWORD)
    monkeypatch.setattr(settings, "monitoring_viewer_password_hash", SecretStr(secret))
    form = {"username": "monitoring", "password": PASSWORD}
    origin = {"Origin": settings.web_base_url.rstrip("/")}

    assert (await client.get(SESSION)).status_code == 401
    denied = {"Origin": "https://other.example.test"}
    assert (await client.post(SESSION, json=form, headers=denied)).status_code == 403
    assert (await client.post(
        SESSION, json={**form, "password": "wrong test password"}, headers=origin,
    )).status_code == 401

    reply = await client.post(SESSION, json=form, headers=origin)
    assert reply.status_code == 204
    name = "__Host-yleum_monitoring"
    value = reply.cookies[name]
    cookie = {"Cookie": f"{name}={value}"}
    attributes = reply.headers["set-cookie"].lower()
    assert "; secure" in attributes and "; httponly" in attributes and "; path=/" in attributes
    assert "; domain=" not in attributes and value not in "".join(sessions)
    assert (await client.get(SESSION, headers=cookie)).json() == {"role": "viewer"}
    assert (await client.get(BASE + "/catalog", headers=cookie)).status_code == 200
    assert (await client.get(BASE + "/filters", headers=cookie)).status_code == 200
    assert (await client.get(BASE + "/metrics/1", headers=cookie)).status_code == 200
    assert (await client.get("/api/admin/access", headers=cookie)).status_code == 401
    assert (await client.get("/api/auth/me", headers=cookie)).status_code == 401
    assert (await client.get("/internal/monitoring/metrics", headers=cookie)).status_code == 404
    assert (await client.delete(SESSION, headers={**cookie, **denied})).status_code == 403
    assert (await client.delete(SESSION, headers={**cookie, **origin})).status_code == 204
    assert (await client.get(BASE + "/catalog", headers=cookie)).status_code == 401


async def test_admin_fallback_and_viewer_rotation(client, monkeypatch):
    settings = get_settings()
    old = "previous fixture hash"
    monkeypatch.setattr(settings, "monitoring_viewer_password_hash", SecretStr("new fixture hash"))
    token = "x" * 43
    cookie = {"Cookie": f"__Host-yleum_monitoring={token}"}
    prior_session = monitoring_access._fingerprint(("monitoring", old))
    monkeypatch.setattr(monitoring_access, "get_redis", lambda: SimpleNamespace(
        get=AsyncMock(return_value=prior_session),
    ))
    assert (await client.get(SESSION, headers=cookie)).status_code == 401

    user = User(email="monitoring-platform@example.test", role="user")
    app.dependency_overrides[get_optional_user] = lambda: user
    assert (await client.get(BASE + "/catalog")).status_code == 403
    user.role = "admin"
    assert (await client.get(SESSION, headers=cookie)).json() == {"role": "admin"}

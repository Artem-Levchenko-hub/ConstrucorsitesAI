"""Non-DB falsifying checks for request-scoped credential boundaries."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_session
from yleum_api.core.deps import get_optional_user
from yleum_api.core.errors import ApiError
from yleum_api.models.user import User
from yleum_api.routers import account_credentials as routes
from yleum_api.routers import auth
from yleum_api.services import account_credentials as credentials


async def test_issuance_refuses_email_that_changed_while_waiting_for_user_lock():
    user_id = uuid4()
    stale = User(id=user_id, email="old@example.com", status="active", is_anon=False)
    locked = User(id=user_id, email="new@example.com", status="active", is_anon=False)
    added = []

    class Session:
        async def execute(self, statement):
            return SimpleNamespace(scalar_one_or_none=lambda: locked)

        def add(self, item):
            added.append(item)

        async def commit(self):
            pass

    with pytest.raises(ApiError):
        await auth._issue_email_token(Session(), stale, "reset_password", ttl=timedelta(minutes=30))
    assert added == []


@pytest.mark.parametrize("kind", ["email", "password"])
@pytest.mark.parametrize("principal", ["other", "owner", "anonymous"])
async def test_public_capability_only_clears_target_principal_cookie(monkeypatch, kind, principal):
    target = User(id=uuid4(), email="target@example.com", status="active", is_anon=False)
    other = User(id=uuid4(), email="other@example.com", status="active", is_anon=False)
    incoming = target if principal == "owner" else other if principal == "other" else None

    class Session:
        async def commit(self):
            pass

    async def session():
        yield Session()

    async def optional():
        return incoming

    async def confirm(*args):
        return target.id

    async def consume(*args):
        return target

    async def apply(*args, **kwargs):
        return None

    async def hashed(*args):
        return "test-hash"

    monkeypatch.setattr(credentials, "confirm_email_change", confirm)
    monkeypatch.setattr(credentials, "apply_password_change", apply)
    monkeypatch.setattr(auth, "_consume_email_token", consume)
    monkeypatch.setattr(auth, "hash_password", hashed)
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(auth.router)
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_optional_user] = optional
    path = (
        "/api/account/credentials/email/confirm" if kind == "email" else "/api/auth/password/reset"
    )
    body = {"token": "x" * 64}
    if kind == "password":
        body["password"] = "replacement456"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            path, json=body, headers={"origin": get_settings().web_base_url.rstrip("/")}
        )
    assert response.status_code == 200
    assert ("set-cookie" in response.headers) is (principal == "owner")
    if kind == "email":
        assert response.json() == {"status": "completed", "reauthenticate": principal == "owner"}

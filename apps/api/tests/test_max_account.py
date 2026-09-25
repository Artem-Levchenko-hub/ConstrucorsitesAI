from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import FREE_GENERATION_LIMIT, get_settings
from yleum_api.models.user import User
from yleum_api.models.wallet import Wallet
from yleum_api.routers import auth as auth_router
from yleum_api.routers import max_accounts as max_accounts_router
from yleum_api.routers import projects as projects_router
from yleum_api.services import repo as repo_svc

pytestmark = pytest.mark.asyncio

MAX_REGISTRATION = {
    "email": "owner@example.com",
    "password": "secret123",
    "product": "max",
    "terms_accepted": True,
    "privacy_accepted": True,
    "personal_data_accepted": True,
    "document_version": get_settings().legal_document_version,
}


def _stub_project_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo_svc, "init_repo", lambda *_args: "a" * 40)
    monkeypatch.setattr(projects_router, "enqueue_preview", lambda *_args: None)

    async def no_publish(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(projects_router, "publish_event", no_publish)


async def test_max_registration_requires_separate_legal_acceptances(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "owner@example.com",
            "password": "secret123",
            "product": "max",
            "document_version": get_settings().legal_document_version,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "legal_acceptance_required"


async def test_max_project_requires_verified_email_only(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verification_tokens: list[str] = []

    async def capture_verification(_user, raw_token: str) -> None:
        verification_tokens.append(raw_token)

    monkeypatch.setattr(auth_router, "_send_verification", capture_verification)
    registration = await client.post("/api/auth/register", json=MAX_REGISTRATION)
    assert registration.status_code == 201
    assert registration.json()["email_verified_at"] is None
    assert len(verification_tokens) == 1

    blocked = await client.post(
        "/api/projects",
        json={"name": "Blocked MAX", "template": "max_miniapp"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "email_verification_required"

    verified = await client.post(
        "/api/auth/email/verify",
        json={"token": verification_tokens[0]},
    )
    assert verified.status_code == 200

    _stub_project_creation(monkeypatch)
    project = await client.post(
        "/api/projects",
        json={"name": "No requisites", "template": "max_miniapp"},
    )
    assert project.status_code == 201

    access = await client.get("/api/max/account/access")
    assert access.status_code == 200
    assert access.json()["can_create_project"] is True
    assert access.json()["email_verified"] is True
    assert "business" not in access.json()


async def test_account_never_accepts_owner_requisites(
    client: httpx.AsyncClient,
) -> None:
    registered = await client.post(
        "/api/auth/register",
        json={"email": "no-requisites@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    # The old requisites endpoints are gone: nothing on the platform asks for
    # ИНН, ОГРН or a legal name, and nothing stores them.
    saved = await client.put(
        "/api/max/account/business",
        json={"kind": "self_employed", "inn": "500100732259", "legal_name": "Иванов"},
    )
    assert saved.status_code in {404, 405}
    queue = await client.get("/api/max/account/admin/businesses")
    assert queue.status_code in {403, 404}

    access = await client.get("/api/max/account/access")
    assert access.status_code == 200
    assert set(access.json()) == {
        "authenticated",
        "email_verified",
        "email_delivery_configured",
        "can_create_project",
        "reason",
        "legal_document_version",
        "payments_configured",
    }


async def test_registered_user_can_create_max_project_with_email_and_password_only(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_project_creation(monkeypatch)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "general@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201
    project = await client.post(
        "/api/projects",
        json={"name": "No duplicate legal form", "template": "max_miniapp"},
    )
    assert project.status_code == 201


async def test_max_free_generation_limit_belongs_to_the_user(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_project_creation(monkeypatch)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "quota@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201
    project = await client.post(
        "/api/projects",
        json={"name": "Quota MAX", "template": "max_miniapp"},
    )
    assert project.status_code == 201

    user = (
        await db_session.execute(select(User).where(User.email == "quota@example.com"))
    ).scalar_one()
    user.free_generations_used = FREE_GENERATION_LIMIT
    wallet = await db_session.get(Wallet, user.id)
    assert wallet is not None
    wallet.balance_rub = Decimal("0")
    await db_session.commit()

    blocked = await client.post(
        f"/api/projects/{project.json()['id']}/prompt",
        json={
            "prompt": "Собери приложение",
            "skip_clarify": True,
            "idempotency_key": "max-user-quota-1",
        },
    )
    assert blocked.status_code == 402
    assert blocked.json()["error"]["code"] == "wallet_empty"


async def test_admin_access_flag_requires_the_admin_role(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        max_accounts_router,
        "_is_admin",
        lambda user: user.email == "admin@example.com",
    )
    registered = await client.post(
        "/api/auth/register",
        json={"email": "admin@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    access = await client.get("/api/max/account/admin/access")
    assert access.status_code == 200
    assert access.json() == {"is_admin": True}

    monkeypatch.setattr(max_accounts_router, "_is_admin", lambda user: False)
    denied = await client.get("/api/max/account/admin/access")
    assert denied.status_code == 403

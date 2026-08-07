from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.account import BusinessMember, BusinessProfile
from omnia_api.models.user import User
from omnia_api.models.wallet import Wallet
from omnia_api.routers import auth as auth_router
from omnia_api.routers import max_accounts as max_accounts_router
from omnia_api.routers import projects as projects_router
from omnia_api.services import repo as repo_svc

pytestmark = pytest.mark.asyncio

MAX_REGISTRATION = {
    "email": "owner@example.com",
    "password": "secret123",
    "product": "max",
    "terms_accepted": True,
    "privacy_accepted": True,
    "personal_data_accepted": True,
    "document_version": "2026-07-30",
}


async def test_max_registration_requires_separate_legal_acceptances(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "owner@example.com",
            "password": "secret123",
            "product": "max",
            "document_version": "2026-07-30",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "legal_acceptance_required"


async def test_signed_in_user_gets_max_demo_before_email_and_business(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verification_tokens: list[str] = []

    async def capture_verification(_user, raw_token: str) -> None:
        verification_tokens.append(raw_token)

    monkeypatch.setattr(auth_router, "_send_verification", capture_verification)
    monkeypatch.setattr(repo_svc, "init_repo", lambda *_args: "a" * 40)
    monkeypatch.setattr(projects_router, "enqueue_preview", lambda *_args: None)

    async def no_publish(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(projects_router, "publish_event", no_publish)
    registration = await client.post("/api/auth/register", json=MAX_REGISTRATION)
    assert registration.status_code == 201
    assert registration.json()["email_verified_at"] is None
    assert len(verification_tokens) == 1

    project = await client.post(
        "/api/projects",
        json={"name": "Instant MAX demo", "template": "max_miniapp"},
    )
    assert project.status_code == 201

    access = await client.get("/api/max/account/access")
    assert access.status_code == 200
    assert access.json()["can_create_project"] is True
    assert access.json()["can_launch"] is False
    assert access.json()["launch_reason"] == "email_verification_required"
    assert access.json()["demo"] == {
        "limit": 1,
        "used": 0,
        "remaining": 1,
        "available": True,
        "upgrade_path": "/billing/plan",
    }
    launch_action = await client.post(
        f"/api/projects/{project.json()['id']}/integrations/max/connect",
        json={"token": "not-sent-to-max-before-business-check"},
    )
    assert launch_action.status_code == 403
    assert launch_action.json()["error"]["code"] == "email_verification_required"

    verified = await client.post(
        "/api/auth/email/verify",
        json={"token": verification_tokens[0]},
    )
    assert verified.status_code == 200

    access = await client.get("/api/max/account/access")
    assert access.json()["can_create_project"] is True
    assert access.json()["launch_reason"] == "business_profile_required"


async def test_verified_self_employed_owner_can_create_max_project(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def verified_npd(_inn: str) -> tuple[str, str | None, dict[str, object]]:
        return "verified", "НПД подтверждён", {"status": True}

    monkeypatch.setattr(max_accounts_router, "_verify_self_employed", verified_npd)
    monkeypatch.setattr(repo_svc, "init_repo", lambda *_args: "a" * 40)
    monkeypatch.setattr(projects_router, "enqueue_preview", lambda *_args: None)

    async def no_publish(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(projects_router, "publish_event", no_publish)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "npd@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    business = await client.put(
        "/api/max/account/business",
        json={
            "kind": "self_employed",
            "inn": "500100732259",
            "legal_name": "Иванов Иван Иванович",
        },
    )
    assert business.status_code == 200
    assert business.json()["status"] == "verified"

    access = await client.get("/api/max/account/access")
    assert access.status_code == 200
    assert access.json()["can_create_project"] is True
    assert access.json()["can_launch"] is False
    assert access.json()["launch_reason"] == "subscription_entitlement_required"

    project = await client.post(
        "/api/projects",
        json={"name": "NPD MAX", "template": "max_miniapp"},
    )
    assert project.status_code == 201


async def test_general_registration_can_create_but_not_launch_max(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo_svc, "init_repo", lambda *_args: "a" * 40)
    monkeypatch.setattr(projects_router, "enqueue_preview", lambda *_args: None)

    async def no_publish(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(projects_router, "publish_event", no_publish)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "general@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201
    project = await client.post(
        "/api/projects",
        json={"name": "Pre-verification demo", "template": "max_miniapp"},
    )
    assert project.status_code == 201
    access = await client.get("/api/max/account/access")
    assert access.json()["can_launch"] is False
    assert access.json()["launch_reason"] == "business_profile_required"


async def test_max_demo_limit_is_server_enforced_per_account(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def verified_npd(_inn: str) -> tuple[str, str | None, dict[str, object]]:
        return "verified", "НПД подтверждён", {"status": True}

    monkeypatch.setattr(max_accounts_router, "_verify_self_employed", verified_npd)
    monkeypatch.setattr(repo_svc, "init_repo", lambda *_args: "a" * 40)
    monkeypatch.setattr(projects_router, "enqueue_preview", lambda *_args: None)

    async def no_publish(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(projects_router, "publish_event", no_publish)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "quota@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201
    business = await client.put(
        "/api/max/account/business",
        json={
            "kind": "self_employed",
            "inn": "500100732259",
            "legal_name": "Иванов Иван Иванович",
        },
    )
    assert business.status_code == 200
    project = await client.post(
        "/api/projects",
        json={"name": "Quota MAX", "template": "max_miniapp"},
    )
    assert project.status_code == 201

    user = (
        await db_session.execute(select(User).where(User.email == "quota@example.com"))
    ).scalar_one()
    user.max_demo_generations_used = 1
    wallet = await db_session.get(Wallet, user.id)
    assert wallet is not None
    wallet.balance_rub = Decimal("0")
    await db_session.commit()

    blocked = await client.post(
        f"/api/projects/{project.json()['id']}/prompt",
        json={
            "prompt": "Собери приложение",
            "skip_clarify": True,
            "idempotency_key": "max-business-quota-1",
        },
    )
    assert blocked.status_code == 402
    assert blocked.json()["error"]["code"] == "max_demo_exhausted"
    assert blocked.json()["error"]["details"] == {
        "limit": 1,
        "upgrade_path": "/billing/plan",
    }


async def test_admin_can_list_and_decide_pending_businesses(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
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
    admin = (
        await db_session.execute(select(User).where(User.email == "admin@example.com"))
    ).scalar_one()
    profile = BusinessProfile(
        kind="legal_entity",
        inn="7816246925",
        ogrn="1187847020949",
        legal_name='ООО "КОРТЭЛ"',
        status="pending",
        verification_source="manual",
        verification_note="Ожидает проверки",
    )
    db_session.add(profile)
    await db_session.flush()
    db_session.add(
        BusinessMember(
            business_id=profile.id,
            user_id=admin.id,
            role="owner",
        )
    )
    await db_session.commit()

    queue = await client.get("/api/max/account/admin/businesses")
    assert queue.status_code == 200
    assert queue.json()[0]["owner_email"] == "admin@example.com"
    assert queue.json()[0]["status"] == "pending"
    access = await client.get("/api/max/account/admin/access")
    assert access.status_code == 200
    assert access.json() == {"is_admin": True}

    decision = await client.post(
        "/api/max/account/business/7816246925/decision",
        json={"approved": True, "note": "Реквизиты проверены"},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "verified"

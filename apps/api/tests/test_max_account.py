from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.models.account import LegalAcceptance
from yleum_api.models.generation_run import GenerationRun
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


@pytest.mark.parametrize("product", [None, "general"])
async def test_general_registration_cannot_bypass_max_email_and_consents(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    product: str | None,
) -> None:
    _stub_project_creation(monkeypatch)
    registered = await client.post(
        "/api/auth/register",
        json={"email": "general@example.com", "password": "secret123"}
        | ({"product": product} if product else {}),
    )
    assert registered.status_code == 201
    assert registered.json()["email_verified_at"] is None
    project = await client.post(
        "/api/projects",
        json={"name": "No duplicate legal form", "template": "max_miniapp"},
    )
    assert project.status_code == 403
    assert project.json()["error"]["code"] == "email_verification_required"


async def test_general_owner_can_explicitly_complete_max_onboarding(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_project_creation(monkeypatch)
    registered = await client.post(
        "/api/auth/register", json={
            "email": "general@example.com", "password": "secret123", "role": "admin",
        },
    )
    assert registered.status_code == 201
    user = (await db_session.scalars(select(User))).one()
    assert user.role == "user"  # A signup payload cannot self-assign verification authority.
    # Reproduce the old general signup's unsupported verification timestamp.
    user.email_verified_at = datetime.now(UTC)
    await db_session.commit()
    access = (await client.get("/api/max/account/access")).json()
    assert access["email_verified"] is False and access["can_create_project"] is False
    verification_tokens = []

    async def capture_verification(_user, raw_token):
        verification_tokens.append(raw_token)

    monkeypatch.setattr(auth_router, "_send_verification", capture_verification)
    assert (await client.post("/api/auth/email/verify/request")).status_code == 202
    assert len(verification_tokens) == 1
    assert (await client.post(
        "/api/auth/email/verify", json={"token": verification_tokens[0]},
    )).status_code == 200
    denied = await client.post(
        "/api/projects", json={"name": "Missing legal", "template": "max_miniapp"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "legal_acceptance_required"
    assert (await client.get("/api/max/account/access")).json()["can_create_project"] is False

    payload = {key: MAX_REGISTRATION[key] for key in (
        "terms_accepted", "privacy_accepted", "personal_data_accepted", "document_version",
    )}
    for invalid, code, status in [
        ({**payload, "privacy_accepted": False}, "legal_acceptance_required", 422),
        ({**payload, "document_version": "outdated"}, "legal_version_outdated", 409),
    ]:
        response = await client.post("/api/max/account/acceptances", json=invalid)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert (await db_session.scalars(select(LegalAcceptance))).all() == []
    for _ in range(2):
        accepted = await client.post("/api/max/account/acceptances", json=payload)
        assert accepted.status_code == 200
        assert accepted.json()["accepted"] is True
    rows = (await db_session.scalars(select(LegalAcceptance))).all()
    assert len(rows) == 3
    assert {row.document_type for row in rows} == {"terms", "privacy", "personal_data"}
    assert all(row.user_id == user.id for row in rows)
    assert all(row.document_version == get_settings().legal_document_version for row in rows)
    assert (await client.get("/api/max/account/access")).json()["can_create_project"] is True
    created = await client.post(
        "/api/projects", json={"name": "After explicit consent", "template": "max_miniapp"},
    )
    assert created.status_code == 201


async def test_max_consent_action_needs_authentication(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/max/account/acceptances", json=MAX_REGISTRATION)
    assert response.status_code == 401


async def test_max_free_generation_limit_belongs_to_the_user(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_project_creation(monkeypatch)
    verification_tokens = []

    async def capture_verification(_user, raw_token):
        verification_tokens.append(raw_token)

    async def destroy_runtime(_project_id, _slug):
        return {"state": "destroyed"}

    monkeypatch.setattr(auth_router, "_send_verification", capture_verification)
    monkeypatch.setattr("yleum_api.services.orchestrator_client.destroy", destroy_runtime)
    registered = await client.post(
        "/api/auth/register",
        json={**MAX_REGISTRATION, "email": "quota@example.com"},
    )
    assert registered.status_code == 201
    assert (await client.post(
        "/api/auth/email/verify", json={"token": verification_tokens[0]},
    )).status_code == 200
    project = await client.post(
        "/api/projects",
        json={"name": "Quota MAX", "template": "max_miniapp"},
    )
    assert project.status_code == 201

    user = (
        await db_session.execute(select(User).where(User.email == "quota@example.com"))
    ).scalar_one()
    user_id = user.id
    user.free_creations_carryover = 1
    wallet = await db_session.get(Wallet, user.id)
    assert wallet is not None
    wallet.balance_rub = Decimal("0")
    await db_session.commit()

    async def assert_creation_exhausted(project_id: str, key: str) -> None:
        blocked = await client.post(
            f"/api/projects/{project_id}/prompt",
            json={
                "prompt": "Собери приложение",
                "skip_clarify": True,
                "idempotency_key": key,
            },
        )
        assert blocked.status_code == 402
        error = blocked.json()["error"]
        assert error["code"] == "entitlement_exceeded"
        assert error["details"]["entitlement"] == "included_creations"
        assert error["details"]["period"] == "lifetime"
        assert error["details"]["used"] == 1
        assert error["details"]["remaining"] == 0
        # Match the production request-session rollback before the next request.
        await db_session.rollback()

    project_id = project.json()["id"]
    await assert_creation_exhausted(project_id, "max-user-quota-1")
    await assert_creation_exhausted(project_id, "max-user-quota-1")

    deleted = await client.delete(f"/api/projects/{project_id}")
    assert deleted.status_code == 204, deleted.text
    assert await db_session.scalar(
        select(User.free_creations_carryover).where(User.id == user_id)
    ) == 1
    replacement = await client.post(
        "/api/projects",
        json={"name": "Replacement MAX", "template": "max_miniapp"},
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["id"] != project_id
    await assert_creation_exhausted(replacement.json()["id"], "max-user-quota-2")
    assert (await db_session.scalars(select(GenerationRun))).all() == []


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

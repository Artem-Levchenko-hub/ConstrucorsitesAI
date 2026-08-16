from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.account import BusinessMember, BusinessProfile
from omnia_api.models.admin_audit import AdminAuditEvent
from omnia_api.models.user import User

pytestmark = pytest.mark.asyncio


async def _register(
    client: httpx.AsyncClient,
    email: str,
) -> dict[str, object]:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123"},
    )
    assert response.status_code == 201
    return response.json()


async def test_regular_user_cannot_open_admin_api(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "regular@example.com")

    response = await client.get("/api/admin/users")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_admin_can_manage_account_and_business_with_audit(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    target_data = await _register(client, "target@example.com")
    target = await db_session.get(User, target_data["id"])
    assert target is not None
    target.email_verified_at = None
    business = BusinessProfile(
        kind="legal_entity",
        inn="7816246925",
        ogrn="1187847020949",
        legal_name='ООО "КОРТЭЛ"',
        status="pending",
        verification_source="manual",
    )
    db_session.add(business)
    await db_session.flush()
    db_session.add(
        BusinessMember(
            business_id=business.id,
            user_id=target.id,
            role="owner",
        )
    )
    await db_session.commit()

    admin_data = await _register(client, "admin@example.com")
    admin = await db_session.get(User, admin_data["id"])
    assert admin is not None
    admin.role = "admin"
    await db_session.commit()

    listed = await client.get("/api/admin/users")
    assert listed.status_code == 200
    target_before = next(
        item for item in listed.json() if item["email"] == "target@example.com"
    )
    assert target_before["is_admin"] is False
    assert target_before["email_verified_at"] is None
    assert target_before["business"]["status"] == "pending"

    updated = await client.patch(
        f"/api/admin/users/{target.id}",
        json={
            "role": "admin",
            "email_verified": True,
            "status": "active",
            "business_verified": True,
            "note": "Проверено владельцем",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "admin"
    assert updated.json()["is_admin"] is True
    assert updated.json()["email_verified_at"] is not None
    assert updated.json()["business"]["status"] == "verified"

    audit = await client.get("/api/admin/audit")
    assert audit.status_code == 200
    assert audit.json()[0]["actor_email"] == "admin@example.com"
    assert audit.json()[0]["target_email"] == "target@example.com"
    assert audit.json()[0]["details"]["after"]["role"] == "admin"
    events = list((await db_session.execute(select(AdminAuditEvent))).scalars())
    assert len(events) == 1


async def test_admin_cannot_suspend_self(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    admin_data = await _register(client, "admin@example.com")
    admin = await db_session.get(User, admin_data["id"])
    assert admin is not None
    admin.role = "admin"
    await db_session.commit()

    response = await client.patch(
        f"/api/admin/users/{admin.id}",
        json={"status": "suspended"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def test_admin_can_manage_account_with_audit(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    target_data = await _register(client, "target@example.com")
    target = await db_session.get(User, target_data["id"])
    assert target is not None
    target.email_verified_at = None
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
    # An account is an email and a password: the admin view carries no requisites.
    assert "business" not in target_before

    updated = await client.patch(
        f"/api/admin/users/{target.id}",
        json={
            "role": "admin",
            "email_verified": True,
            "status": "active",
            "note": "Проверено владельцем",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "admin"
    assert updated.json()["is_admin"] is True
    assert updated.json()["email_verified_at"] is not None

    audit = await client.get("/api/admin/audit")
    assert audit.status_code == 200
    assert audit.json()[0]["actor_email"] == "admin@example.com"
    assert audit.json()[0]["target_email"] == "target@example.com"
    assert audit.json()[0]["details"]["after"]["role"] == "admin"
    assert "business_status" not in audit.json()[0]["details"]["after"]

    stored = (await db_session.execute(select(AdminAuditEvent))).scalars().all()
    assert len(stored) == 1


async def test_admin_cannot_verify_a_business_that_no_longer_exists(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    target_data = await _register(client, "target2@example.com")
    admin_data = await _register(client, "admin2@example.com")
    admin = await db_session.get(User, admin_data["id"])
    assert admin is not None
    admin.role = "admin"
    await db_session.commit()

    # `business_verified` is not a known change any more, so the request is
    # rejected as "no account change" instead of touching a retired table.
    response = await client.patch(
        f"/api/admin/users/{target_data['id']}",
        json={"business_verified": True},
    )
    assert response.status_code == 422

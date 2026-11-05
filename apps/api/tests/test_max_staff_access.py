"""Exercise real sessions, project grants and concurrent queue writes on PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.db import get_session
from yleum_api.core.errors import ApiError
from yleum_api.core.security import create_access_token
from yleum_api.models.account import AuthSession
from yleum_api.models.max_staff_checkout import MaxStaffPaymentAttempt
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.schemas.max_management import OwnerRecordPublic, OwnerRecordsPagePublic
from yleum_api.services import max_staff_source


async def test_staff_project_invite_payment_queue_claim_and_revoke(test_engine, monkeypatch):
    from yleum_api.main import create_app

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as db:
        owner, barista, operator, unverified = [
            User(
                email=f"{name}@example.test",
                email_verified_at=now if name != "unverified" else None,
            )
            for name in ("owner", "barista", "operator", "unverified")
        ]
        db.add_all([owner, barista, operator, unverified])
        await db.flush()
        cafe = Project(name="Кофе", slug="coffee-qa", owner_id=owner.id, template="max_miniapp")
        other = Project(
            name="Другая точка", slug="other-qa", owner_id=owner.id, template="max_miniapp"
        )
        db.add_all([cafe, other])
        auth = [
            AuthSession(user_id=user.id, expires_at=now + timedelta(hours=1))
            for user in (owner, barista, operator, unverified)
        ]
        db.add_all(auth)
        await db.commit()
    bearer = [
        {
            "Authorization": (
                "Bearer "
                + create_access_token(
                    session.user_id,
                    session_id=session.id,
                    session_version=0,
                )
            )
        }
        for session in auth
    ]
    owner_header, barista_header, operator_header, unverified_header = bearer

    app = create_app()

    async def session_dep():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_dep
    records: dict[UUID, OwnerRecordPublic] = {}

    async def list_source(_db, project, query):
        assert project.id == cafe.id
        return OwnerRecordsPagePublic(
            records=[record for record in records.values() if record.resource == query.resource],
        )

    async def get_source(_db, project, resource, record_id):
        record = records.get(record_id)
        if project.id != cafe.id or record is None or record.resource != resource:
            raise ApiError("not_found", "Запись не найдена", 404)
        return record

    monkeypatch.setattr(max_staff_source, "list_staff_source_records", list_source)
    monkeypatch.setattr(max_staff_source, "get_staff_source_record", get_source)
    confirm = AsyncMock(side_effect=ApiError("unsupported", "Нужна совместимая публикация", 409))
    monkeypatch.setattr(max_staff_source, "confirm_staff_source", confirm)
    project_path = f"/api/projects/{cafe.id}"
    team = f"{project_path}/max/team"
    staff = f"{project_path}/max/staff"
    accept = "/api/max/staff/invitations/accept"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        unprepared = await client.put(
            team + "/settings",
            headers=owner_header,
            json={"leads_enabled": True, "orders_mode": "yookassa", "prep_minutes": 7},
        )
        assert unprepared.status_code == 409
        assert (await client.get(team, headers=owner_header)).json()["settings"][
            "orders_mode"
        ] is None
        confirm.side_effect = None
        configured = await client.put(
            team + "/settings",
            headers=owner_header,
            json={"leads_enabled": True, "orders_mode": "yookassa", "prep_minutes": 7},
        )
        assert configured.status_code == 200, configured.text

        async def invite(user, name, permissions):
            reply = await client.post(
                team + "/invitations",
                headers=owner_header,
                json={
                    "email": user.email,
                    "name": name,
                    "permissions": permissions,
                },
            )
            assert reply.status_code == 201, reply.text
            assert reply.headers["cache-control"] == "no-store"
            return {"token": reply.json()["token"]}

        invitation = await invite(barista, "Бариста", ["orders"])
        denied = await client.post(accept, json=invitation, headers=unverified_header)
        assert denied.status_code == 403 and "staff_verification_required" in denied.text
        denied = await client.post(accept, json=invitation, headers=operator_header)
        assert denied.status_code == 403 and "staff_invite_account_mismatch" in denied.text
        assert (
            await client.post(accept, json=invitation, headers=barista_header)
        ).status_code == 200
        replay = await client.post(accept, json=invitation, headers=barista_header)
        assert replay.status_code == 200 and replay.json()["project_id"] == str(cafe.id)
        invitation = await invite(operator, "Оператор", ["leads"])
        assert (
            await client.post(accept, json=invitation, headers=operator_header)
        ).status_code == 200

        assert (await client.get(project_path, headers=barista_header)).status_code == 404
        assert (await client.get(team, headers=barista_header)).status_code == 404
        assert (
            await client.get(
                f"/api/projects/{other.id}/max/staff",
                headers=barista_header,
            )
        ).status_code == 403
        assert (
            await client.get(f"{staff}/records?resource=orders", headers=operator_header)
        ).status_code == 403
        assert (
            await client.get(f"{staff}/records?resource=leads", headers=barista_header)
        ).status_code == 403

        created = datetime.now(UTC)
        for resource in ("orders", "orders", "orders", "leads"):
            record_id = uuid4()
            fields = {
                "id": record_id,
                "resource": resource,
                "created_at": created,
                "revision": "a" * 64,
                "status": "new",
                "customer_name": "Покупатель",
                "contact_email": "customer@example.test",
            }
            if resource == "orders":
                fields.update(
                    {
                        "items": [
                            {
                                "catalog_id": "coffee",
                                "title": "Латте",
                                "quantity": 1,
                                "unit_price_kopecks": 24000,
                            }
                        ],
                        "total_kopecks": 24000,
                        "currency": "RUB",
                    }
                )
            records[record_id] = OwnerRecordPublic.model_validate(fields)
        paid, test, unpaid, lead = records
        async with factory() as db:
            for order_id, test_mode in ((paid, False), (test, True)):
                db.add(
                    MaxStaffPaymentAttempt(
                        project_id=cafe.id,
                        owner_id=owner.id,
                        order_id=order_id,
                        actor_id=7,
                        shop_id="qa",
                        auth_mode="basic",
                        grant_revision=1,
                        amount_kopecks=24000,
                        currency="RUB",
                        binding_id=uuid4(),
                        integration_id=uuid4(),
                        request_body={},
                        provider_payment_id=str(order_id),
                        status="succeeded",
                        is_test=test_mode,
                        paid_at=created,
                        verified_at=created,
                    )
                )
            await db.commit()
        reply = await client.get(f"{staff}/records?resource=orders", headers=barista_header)
        assert reply.status_code == 200, reply.text
        orders = {entry["id"]: entry for entry in reply.json()["records"]}
        assert set(orders) == {str(paid), str(test)} and str(unpaid) not in orders
        assert orders[str(paid)]["payment"] == "paid" and orders[str(test)]["payment"] == "test"
        assert "contact_email" not in orders[str(paid)]
        leads = await client.get(f"{staff}/records?resource=leads", headers=operator_header)
        assert leads.json()["records"][0]["contact_email"] == "customer@example.test"
        assert leads.json()["records"][0]["id"] == str(lead)

        granted = await client.patch(
            f"{team}/members/{operator.id}",
            headers=owner_header,
            json={"permissions": ["leads", "orders"]},
        )
        assert granted.status_code == 200

        def action(resource, name, revision="a" * 64, **extra):
            return {
                "resource": resource,
                "action": name,
                "expected_revision": revision,
                "operation_key": str(uuid4()),
                **extra,
            }

        assert (
            await client.patch(
                f"{staff}/records/{test}",
                headers=barista_header,
                json=action("orders", "claim"),
            )
        ).status_code == 409
        url = f"{staff}/records/{paid}"
        calls = [
            (barista_header, action("orders", "claim")),
            (operator_header, action("orders", "claim")),
        ]
        claims = await asyncio.gather(
            *(client.patch(url, headers=h, json=body) for h, body in calls)
        )
        assert sorted(response.status_code for response in claims) == [200, 409]
        winner = 0 if claims[0].status_code == 200 else 1
        headers, claim = calls[winner]
        claimed = claims[winner].json()
        assert (await client.patch(url, headers=headers, json=claim)).json() == claimed
        ready = await client.patch(
            url,
            headers=headers,
            json=action("orders", "ready", claimed["revision"]),
        )
        assert ready.status_code == 200 and ready.json()["status"] == "ready"
        completed = await client.patch(
            url,
            headers=headers,
            json=action("orders", "hand_over", ready.json()["revision"]),
        )
        assert completed.status_code == 200 and completed.json()["status"] == "completed"
        activity = await client.get(f"{url}/history?resource=orders", headers=owner_header)
        assert [event["action"] for event in activity.json()] == ["hand_over", "ready", "claim"]
        assert {event["actor_name"] for event in activity.json()} == {
            "Бариста" if winner == 0 else "Оператор"
        }
        assert (
            await client.get(f"{url}/history?resource=orders", headers=headers)
        ).status_code == 403
        assigned = barista if winner == 0 else operator
        assert (
            await client.delete(f"{team}/members/{assigned.id}", headers=owner_header)
        ).status_code == 204
        assert (await client.patch(url, headers=headers, json=claim)).status_code == 403
        assert (
            await client.get(f"{staff}/records?resource=orders", headers=headers)
        ).status_code == 403

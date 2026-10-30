"""Disposable PostgreSQL support acceptance; transport stubs never call Redis/SMTP."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.security import create_access_token
from yleum_api.models.support import (
    SupportEmailOutbox,
    SupportMessage,
    SupportRateBucket,
    SupportTicket,
)
from yleum_api.models.user import User
from yleum_api.schemas.support import CreateMessage, CreateTicket
from yleum_api.services import support_email, support_tickets
from yleum_api.services.transactional_email import EmailDeliveryFailed, EmailDeliveryNotConfigured


@pytest.fixture(autouse=True)
def isolate_support_transport(monkeypatch):
    from yleum_api.routers import support

    async def discard(*args, **kwargs):
        return None

    monkeypatch.setattr(support, "publish_support_change", discard)


def headers(user=None, token=None):
    values = {"origin": get_settings().web_base_url.rstrip("/")}
    if user is not None:
        values["authorization"] = f"Bearer {create_access_token(inspect(user).identity[0])}"
    if token:
        values["X-Support-Token"] = token
    return values


async def account(session, email, role="user", status="active"):
    user = User(email=email, role=role, status=status, is_anon=False)
    session.add(user)
    await session.commit()
    return user


def guest_payload(**overrides):
    return {
        "request_id": str(uuid4()),
        "subject": "Помощь с проектом",
        "message": "Первое сообщение",
        "email": "guest@example.com",
        "privacy_accepted": True,
        "guest_token": uuid4().hex + uuid4().hex,
        "source_path": "/max?secret=discarded#fragment",
        **overrides,
    }


async def test_owner_guest_and_admin_boundaries(client, db_session):
    alice = await account(db_session, "alice@example.com")
    bob = await account(db_session, "bob@example.com")
    admin = await account(db_session, "admin@example.com", "admin")
    body = guest_payload(email="spoofed@example.com")
    created = await client.post("/api/support/tickets", json=body, headers=headers(alice))
    assert created.status_code == 200, created.text
    ticket = created.json()
    assert ticket["requester_email"] == "alice@example.com"
    assert ticket["source_path"] == "/max"
    assert ticket["messages"][0]["body"] == body["message"]
    assert "guest_token" not in created.text
    path = f"/api/support/tickets/{ticket['id']}"
    for auth in (headers(bob), headers(admin), headers(token=body["guest_token"]), headers()):
        assert (await client.get(path, headers=auth)).status_code == 404
        reply = await client.post(
            path + "/messages",
            headers=auth,
            json={"request_id": str(uuid4()), "message": "Forbidden"},
        )
        assert reply.status_code == 404
    assert (await client.get("/api/admin/support/tickets", headers=headers(bob))).status_code == 403
    assert (
        await client.get("/api/admin/support/tickets/" + ticket["id"], headers=headers(admin))
    ).status_code == 200
    listed = (await client.get("/api/support/tickets", headers=headers(alice))).json()
    assert listed["total"] == 1
    assert (await client.get("/api/support/tickets", headers=headers(bob))).json()["total"] == 0

    guest_body = guest_payload(email="alice@example.com")
    guest = (await client.post("/api/support/tickets", json=guest_body, headers=headers())).json()
    guest_path = "/api/support/tickets/" + guest["id"]
    assert (await client.get(guest_path, headers=headers(alice))).status_code == 404
    assert (
        await client.get(guest_path, headers=headers(token=guest_body["guest_token"]))
    ).status_code == 200
    assert (
        await client.get(guest_path, headers=headers(token=body["guest_token"]))
    ).status_code == 404
    assert (await client.get("/api/support/tickets")).status_code == 401


async def test_all_active_persisted_admins_receive_once_and_retry_conflicts(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(
        get_settings(),
        "admin_emails",
        "bootstrap@example.com,missing@example.com,inactive@example.com",
    )
    await account(db_session, "one@example.com", "admin")
    await account(db_session, "two@example.com", "admin")
    await account(db_session, "bootstrap@example.com")
    await account(db_session, "inactive@example.com", "admin", "blocked")
    body = guest_payload()
    first = await client.post("/api/support/tickets", json=body, headers=headers())
    second = await client.post("/api/support/tickets", json=body, headers=headers())
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert (await db_session.scalar(select(func.count()).select_from(SupportMessage))) == 1
    rows = (await db_session.scalars(select(SupportEmailOutbox))).all()
    assert {row.recipient_email for row in rows} == {
        "one@example.com",
        "two@example.com",
        "bootstrap@example.com",
        "guest@example.com",
    }
    body["message"] = "Different payload"
    assert (
        await client.post("/api/support/tickets", json=body, headers=headers())
    ).status_code == 409
    assert (await db_session.scalar(select(func.count()).select_from(SupportEmailOutbox))) == 4


async def test_archive_boundary_reopen_assignment_conflict_and_reply_mail(client, db_session):
    admin = await account(db_session, "staff@example.com", "admin")
    body = guest_payload()
    created = (await client.post("/api/support/tickets", json=body, headers=headers())).json()
    path = "/api/admin/support/tickets/" + created["id"]
    reply_body = {"request_id": str(uuid4()), "message": "<script>Safe plain text</script>"}
    reply = await client.post(path + "/messages", json=reply_body, headers=headers(admin))
    assert reply.status_code == 200
    assert reply.json()["status"] == "in_progress"
    assert reply.json()["assigned_admin_email"] == admin.email
    assert reply.json()["version"] == 2
    repeated = await client.post(path + "/messages", json=reply_body, headers=headers(admin))
    assert repeated.json()["message_count"] == 2
    assert repeated.json()["version"] == 2
    assert (
        await client.post(
            path + "/messages", json={**reply_body, "message": "changed"}, headers=headers(admin)
        )
    ).status_code == 409
    await db_session.rollback()
    assert (
        await client.patch(
            path, json={"status": "done", "expected_version": 1}, headers=headers(admin)
        )
    ).status_code == 409
    await db_session.rollback()
    done = await client.patch(
        path, json={"status": "done", "expected_version": 2}, headers=headers(admin)
    )
    assert done.json()["status"] == "done"
    assert done.json()["archived"] is False
    ticket = await db_session.get(SupportTicket, UUID(created["id"]))
    ticket.resolved_at = datetime.now(UTC) - timedelta(hours=24, seconds=1)
    await db_session.commit()
    active = (await client.get("/api/admin/support/tickets", headers=headers(admin))).json()
    archived = (
        await client.get("/api/admin/support/tickets?archived=true", headers=headers(admin))
    ).json()
    assert active["total"] == 0 and archived["total"] == 1
    reopened = await client.post(
        "/api/support/tickets/" + created["id"] + "/messages",
        json={"request_id": str(uuid4()), "message": "Новый вопрос"},
        headers=headers(token=body["guest_token"]),
    )
    assert reopened.json()["status"] == "new"
    assert reopened.json()["resolved_at"] is None
    assert reopened.json()["assigned_admin_email"] is None
    rows = (await db_session.scalars(select(SupportEmailOutbox))).all()
    assert [row.recipient_email for row in rows].count("guest@example.com") == 2
    assert [row.recipient_email for row in rows].count("staff@example.com") == 2


async def test_origin_honeypot_consent_limits_and_search(client, db_session):
    admin = await account(db_session, "staff@example.com", "admin")
    body = guest_payload()
    assert (await client.post("/api/support/tickets", json=body)).status_code == 403
    assert (
        await client.post(
            "/api/support/tickets", json=body, headers={"origin": "https://evil.example"}
        )
    ).status_code == 403
    for invalid in ({"website": "spam"}, {"privacy_accepted": False}, {"guest_token": None}):
        result = await client.post(
            "/api/support/tickets", json={**body, **invalid}, headers=headers()
        )
        assert result.status_code in (400, 422)
    for i in range(5):
        result = await client.post(
            "/api/support/tickets", json=guest_payload(subject=f"Question {i}"), headers=headers()
        )
        assert result.status_code == 200, result.text
    limited = await client.post("/api/support/tickets", json=guest_payload(), headers=headers())
    assert limited.status_code == 429
    await db_session.rollback()
    listed = (
        await client.get(
            "/api/admin/support/tickets?search=Question&limit=2&offset=2", headers=headers(admin)
        )
    ).json()
    assert listed["total"] == 5 and len(listed["items"]) == 2
    assert listed["new_count"] == 5
    searched = (
        await client.get("/api/admin/support/tickets?search=Question%203", headers=headers(admin))
    ).json()
    assert searched["total"] == 1
    buckets = (await db_session.scalars(select(SupportRateBucket))).all()
    assert all("guest@example.com" not in row.key and "127.0.0.1" not in row.key for row in buckets)


async def test_transaction_failure_leaves_no_ticket_or_email(db_session, monkeypatch):
    await account(db_session, "staff@example.com", "admin")
    original = support_tickets._enqueue

    async def fail_after_enqueue(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(support_tickets, "_enqueue", fail_after_enqueue)
    with pytest.raises(RuntimeError):
        await support_tickets.create_ticket(
            db_session, CreateTicket(**guest_payload()), user=None, ip="192.0.2.1"
        )
    await db_session.rollback()
    for model in (SupportTicket, SupportMessage, SupportEmailOutbox, SupportRateBucket):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0


async def test_concurrent_create_and_reply_ids_have_one_effect(test_engine):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    data = CreateTicket(**guest_payload())

    async def create():
        async with factory() as session:
            ticket, changed = await support_tickets.create_ticket(
                session, data, user=None, ip="192.0.2.2"
            )
            return ticket.id, changed

    first, second = await asyncio.gather(create(), create())
    assert first[0] == second[0]
    assert sorted([first[1], second[1]]) == [False, True]
    reply = CreateMessage(request_id=uuid4(), message="One reply")

    async def respond():
        async with factory() as session:
            ticket, changed = await support_tickets.add_message(
                session, first[0], reply, user=None, token=data.guest_token, ip="192.0.2.2"
            )
            return ticket.version, changed

    replies = await asyncio.gather(respond(), respond())
    assert sorted(changed for _, changed in replies) == [False, True]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(SupportMessage)) == 2


@pytest.mark.parametrize(
    "failure,expected",
    [
        (EmailDeliveryFailed("safe", retry_safe=True), "pending"),
        (EmailDeliveryFailed("ambiguous"), "uncertain"),
        (EmailDeliveryNotConfigured(), "pending"),
    ],
)
async def test_smtp_failures_keep_committed_ticket_and_safe_delivery_state(
    test_engine, db_session, monkeypatch, failure, expected
):
    await account(db_session, "staff@example.com", "admin")
    ticket, _ = await support_tickets.create_ticket(
        db_session, CreateTicket(**guest_payload()), user=None, ip="192.0.2.3"
    )
    ticket_id = ticket.id

    async def fail(**kwargs):
        raise failure

    monkeypatch.setattr(support_email, "send_transactional_email", fail)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    while await support_email.deliver_one_support_email(factory):
        pass
    await db_session.rollback()
    assert await db_session.get(SupportTicket, ticket_id) is not None
    row = (
        await db_session.scalars(
            select(SupportEmailOutbox).where(SupportEmailOutbox.kind == "acknowledgement")
        )
    ).one()
    assert row.status == expected and row.attempts == 1
    assert not await support_email.deliver_one_support_email(factory)


async def test_parallel_delivery_claims_each_recipient_once_and_rechecks_role(
    test_engine, db_session, monkeypatch
):
    first = await account(db_session, "first@example.com", "admin")
    second = await account(db_session, "second@example.com", "admin")
    await support_tickets.create_ticket(
        db_session, CreateTicket(**guest_payload()), user=None, ip="192.0.2.4"
    )
    first.role = "user"
    await db_session.commit()
    deliveries = []

    async def capture(**kwargs):
        deliveries.append(kwargs)
        await asyncio.sleep(0.02)

    monkeypatch.setattr(support_email, "send_transactional_email", capture)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    await asyncio.gather(*(support_email.deliver_one_support_email(factory) for _ in range(4)))
    assert sorted(mail["recipient"] for mail in deliveries) == ["guest@example.com", second.email]
    await db_session.rollback()
    rows = (await db_session.scalars(select(SupportEmailOutbox))).all()
    assert sorted(row.status for row in rows) == ["cancelled", "sent", "sent"]
    assert deliveries[0]["message_id"] in {row.id for row in rows}


async def test_stale_sending_is_uncertain_and_old_rate_buckets_are_bounded(db_session):
    await account(db_session, "staff@example.com", "admin")
    await support_tickets.create_ticket(
        db_session, CreateTicket(**guest_payload()), user=None, ip="192.0.2.5"
    )
    row = (
        await db_session.scalars(
            select(SupportEmailOutbox).where(SupportEmailOutbox.kind == "acknowledgement")
        )
    ).one()
    row.status = "sending"
    row.claimed_at = datetime.now(UTC) - timedelta(minutes=11)
    db_session.add_all(
        [
            SupportRateBucket(
                key=f"expired-{i}", window=datetime.now(UTC) - timedelta(days=3), count=1
            )
            for i in range(1001)
        ]
    )
    await db_session.commit()
    await support_email.maintain_support_outbox(db_session)
    assert row.status == "uncertain"
    remaining = await db_session.scalar(
        select(func.count())
        .select_from(SupportRateBucket)
        .where(SupportRateBucket.key.like("expired-%"))
    )
    assert remaining == 1


async def test_socket_identity_revocation_and_guest_scope(test_engine, db_session):
    from yleum_api.routers.support import _socket_identity

    admin = await account(db_session, "staff@example.com", "admin")
    token = create_access_token(admin.id, session_version=0)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    assert (await _socket_identity(factory, token, None, None, None))[0] == "admins"
    admin.role = "user"
    await db_session.commit()
    assert (await _socket_identity(factory, token, None, None, None))[0] == f"user:{admin.id}"
    admin.session_version = 1
    await db_session.commit()
    assert await _socket_identity(factory, token, None, None, None) is None
    body = CreateTicket(**guest_payload())
    ticket, _ = await support_tickets.create_ticket(db_session, body, user=None, ip="192.0.2.6")
    assert await _socket_identity(factory, None, None, ticket.id, body.guest_token) == (
        f"guest:{ticket.id}",
        None,
    )
    assert await _socket_identity(factory, None, None, uuid4(), body.guest_token) is None


async def test_parallel_status_updates_detect_stale_version(test_engine, db_session):
    from yleum_api.core.errors import ApiError

    admin = await account(db_session, "staff@example.com", "admin")
    ticket, _ = await support_tickets.create_ticket(
        db_session, CreateTicket(**guest_payload()), user=None, ip="192.0.2.7"
    )
    admin_id, ticket_id = admin.id, ticket.id
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def change(status):
        async with factory() as session:
            actor = await session.get(User, admin_id)
            try:
                row, _ = await support_tickets.change_status(
                    session, ticket_id, user=actor, status=status, expected_version=1
                )
                return row.status
            except ApiError as error:
                return error.status_code

    results = await asyncio.gather(change("done"), change("in_progress"))
    assert results.count(409) == 1
    assert len([result for result in results if result in {"done", "in_progress"}]) == 1


async def test_safe_retry_keeps_message_id_and_contact_address(
    test_engine, db_session, monkeypatch
):
    admin = await account(db_session, "staff@example.com", "admin")
    owner = await account(db_session, "original@example.com")
    ticket, _ = await support_tickets.create_ticket(
        db_session, CreateTicket(**guest_payload()), user=owner, ip="192.0.2.8"
    )
    await support_tickets.add_message(
        db_session,
        ticket.id,
        CreateMessage(request_id=uuid4(), message="Staff reply"),
        user=admin,
        token=None,
        ip="192.0.2.8",
        staff=True,
    )
    owner.email = "changed@example.com"
    rows = (await db_session.scalars(select(SupportEmailOutbox))).all()
    for row in rows:
        if row.recipient_admin_id or row.kind == "acknowledgement":
            row.status = "cancelled"
    await db_session.commit()
    deliveries = []

    async def send(**kwargs):
        deliveries.append(kwargs)
        if len(deliveries) == 1:
            raise EmailDeliveryFailed("safe connection failure", retry_safe=True)

    monkeypatch.setattr(support_email, "send_transactional_email", send)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    assert await support_email.deliver_one_support_email(factory)
    await db_session.rollback()
    item = (
        await db_session.scalars(
            select(SupportEmailOutbox).where(
                SupportEmailOutbox.recipient_admin_id.is_(None),
                SupportEmailOutbox.kind == "message",
            )
        )
    ).one()
    item.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    assert await support_email.deliver_one_support_email(factory)
    assert len(deliveries) == 2
    assert deliveries[0]["message_id"] == deliveries[1]["message_id"]
    assert deliveries[0]["recipient"] == deliveries[1]["recipient"] == "original@example.com"


async def test_owned_ticket_deletion_cascades_but_archive_preserves_history(db_session):
    user = await account(db_session, "owner@example.com")
    admin = await account(db_session, "staff@example.com", "admin")
    ticket, _ = await support_tickets.create_ticket(
        db_session, CreateTicket(**guest_payload()), user=user, ip="192.0.2.9"
    )
    await support_tickets.change_status(
        db_session, ticket.id, user=admin, status="done", expected_version=1
    )
    ticket.resolved_at = datetime.now(UTC) - timedelta(days=2)
    await db_session.commit()
    assert support_tickets.archived(ticket)
    assert (await support_tickets.detail(db_session, ticket)).message_count == 1
    await db_session.delete(user)
    await db_session.commit()
    for model in (SupportTicket, SupportMessage, SupportEmailOutbox):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0


async def test_account_export_contains_only_owned_support_history(db_session):
    import json

    from yleum_api.services.account_export import export_personal_account_data

    owner = await account(db_session, "owner@example.com")
    other = await account(db_session, "other@example.com")
    own, _ = await support_tickets.create_ticket(
        db_session,
        CreateTicket(**guest_payload(subject="Owned ticket", message="Owned conversation")),
        user=owner,
        ip="192.0.2.10",
    )
    await support_tickets.create_ticket(
        db_session,
        CreateTicket(**guest_payload(subject="Foreign ticket", message="Foreign conversation")),
        user=other,
        ip="192.0.2.10",
    )
    guest = guest_payload(
        email="owner@example.com",
        subject="Guest private ticket",
        message="Guest private conversation",
    )
    await support_tickets.create_ticket(
        db_session, CreateTicket(**guest), user=None, ip="192.0.2.10"
    )
    exported = await export_personal_account_data(db_session, owner)
    assert [item["id"] for item in exported["support_tickets"]] == [str(own.id)]
    assert [item["body"] for item in exported["support_messages"]] == ["Owned conversation"]
    serialized = json.dumps(exported)
    for private in (
        "Foreign ticket",
        "Foreign conversation",
        "Guest private ticket",
        "Guest private conversation",
        guest["guest_token"],
        support_tickets.digest(guest["guest_token"]),
        "guest_token_hash",
        "create_request_key",
        "create_payload_hash",
        "actor_key",
        "assigned_admin_id",
    ):
        assert private not in serialized


async def test_message_order_uses_insert_time_after_older_read_transaction(test_engine, db_session):
    from sqlalchemy import text

    data = CreateTicket(**guest_payload())
    ticket, _ = await support_tickets.create_ticket(db_session, data, user=None, ip="192.0.2.11")
    ticket_id = ticket.id
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as older_reader:
        await older_reader.execute(text("SELECT 1"))
        await asyncio.sleep(0.01)
        await support_tickets.add_message(
            db_session,
            ticket_id,
            CreateMessage(request_id=uuid4(), message="First committed reply"),
            user=None,
            token=data.guest_token,
            ip="192.0.2.11",
        )
        await support_tickets.add_message(
            older_reader,
            ticket_id,
            CreateMessage(request_id=uuid4(), message="Second committed reply"),
            user=None,
            token=data.guest_token,
            ip="192.0.2.11",
        )
    result = await support_tickets.detail(db_session, ticket)
    assert [message.body for message in result.messages] == [
        "Первое сообщение",
        "First committed reply",
        "Second committed reply",
    ]

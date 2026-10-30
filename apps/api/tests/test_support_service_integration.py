"""Native PostgreSQL acceptance for the shared widget/email support domain."""

import asyncio
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_support_integration import (
    account,
    guest_payload,
    headers,
    isolate_support_transport,
)
from tests.test_support_service import mail
from yleum_api.core.errors import ApiError
from yleum_api.models.support import (
    SupportEmailOutbox,
    SupportInboundReceipt,
    SupportInboxCursor,
    SupportMessage,
)
from yleum_api.schemas.support import CreateMessage, CreateTicket
from yleum_api.services import support_email, support_inbox, support_tickets

assert isolate_support_transport


@pytest.fixture(autouse=True)
def isolate_inbound_realtime(monkeypatch):
    async def discard(*args, **kwargs):
        return None

    monkeypatch.setattr(support_inbox, "publish_support_change", discard)


async def test_admin_customer_has_separate_receipt_and_operator_notification(db_session):
    admin = await account(db_session, "staff@example.com", "admin")
    data = CreateTicket(**guest_payload())
    ticket, _ = await support_tickets.create_ticket(db_session, data, user=admin, ip="192.0.2.1")
    due = ticket.first_response_due_at
    assert due is not None and ticket.first_responded_at is None
    rows = (await db_session.scalars(select(SupportEmailOutbox))).all()
    assert {(row.recipient_email, row.kind, row.recipient_admin_id) for row in rows} == {
        (admin.email, "acknowledgement", None),
        (admin.email, "message", admin.id),
    }
    await support_tickets.create_ticket(db_session, data, user=admin, ip="192.0.2.1")
    assert await db_session.scalar(select(func.count()).select_from(SupportEmailOutbox)) == 2
    await support_tickets.change_status(
        db_session, ticket.id, user=admin, status="done", expected_version=1
    )
    assert ticket.first_responded_at is None and ticket.first_response_due_at == due
    await support_tickets.add_message(
        db_session,
        ticket.id,
        CreateMessage(request_id=uuid4(), message="Automatic customer follow-up"),
        user=admin,
        token=None,
        ip="192.0.2.1",
    )
    assert ticket.first_responded_at is None
    await support_tickets.add_message(
        db_session,
        ticket.id,
        CreateMessage(request_id=uuid4(), message="Human operator reply"),
        user=admin,
        token=None,
        ip="192.0.2.1",
        staff=True,
    )
    assert ticket.first_responded_at is not None and ticket.first_response_due_at == due


async def test_guest_ack_recovers_exact_ticket_and_never_serializes_capability(
    test_engine,
    db_session,
    monkeypatch,
):
    data = CreateTicket(**guest_payload())
    ticket, _ = await support_tickets.create_ticket(db_session, data, user=None, ip="192.0.2.2")
    ack = (await db_session.scalars(select(SupportEmailOutbox))).one()
    assert data.guest_token not in ack.delivery_payload_encrypted
    assert (
        data.guest_token not in (await support_tickets.detail(db_session, ticket)).model_dump_json()
    )
    captured = []

    async def send(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(support_email, "send_transactional_email", send)
    await support_email.deliver_one_support_email(
        async_sessionmaker(test_engine, expire_on_commit=False)
    )
    assert f"#support={ticket.id}&token={data.guest_token}" in captured[0]["text"]
    assert captured[0]["inline_logo"] is False
    assert captured[0]["reply_to"] == "support@yleum.ru"
    assert await support_tickets.get_ticket(db_session, ticket.id, token=data.guest_token)
    with pytest.raises(ApiError):
        await support_tickets.get_ticket(db_session, uuid4(), token=data.guest_token)


async def test_overdue_filter_excludes_legacy_and_stops_only_after_human_reply(client, db_session):
    admin = await account(db_session, "staff@example.com", "admin")
    current, _ = await support_tickets.create_ticket(
        db_session, CreateTicket(**guest_payload()), user=admin, ip="192.0.2.3"
    )
    legacy, _ = await support_tickets.create_ticket(
        db_session, CreateTicket(**guest_payload()), user=admin, ip="192.0.2.3"
    )
    current.first_response_due_at = datetime.now(UTC) - timedelta(minutes=1)
    legacy.first_response_due_at = None
    await db_session.commit()
    listed = await client.get("/api/admin/support/tickets?overdue=true", headers=headers(admin))
    assert [item["id"] for item in listed.json()["items"]] == [str(current.id)]
    assert listed.json()["items"][0]["first_response_overdue"] is True
    await support_tickets.change_status(
        db_session, current.id, user=admin, status="in_progress", expected_version=1
    )
    assert current.first_responded_at is None
    await support_tickets.add_message(
        db_session,
        current.id,
        CreateMessage(request_id=uuid4(), message="Answer"),
        user=admin,
        token=None,
        ip="192.0.2.3",
        staff=True,
    )
    assert (
        await client.get("/api/admin/support/tickets?overdue=true", headers=headers(admin))
    ).json()["total"] == 0


async def test_inbox_replay_and_sender_equality_cannot_claim_registered_account(db_session):
    registered = await account(db_session, "guest@example.com")
    first = await support_inbox.import_support_mail(
        db_session, mail(), delivery_key="uid1", support="support@yleum.ru"
    )
    first_id = first.id
    assert first.owner_id is None and first.owner_id != registered.id
    assert (
        await support_inbox.import_support_mail(
            db_session, mail(), delivery_key="uid2", support="support@yleum.ru"
        )
        is None
    )
    assert await db_session.scalar(select(func.count()).select_from(SupportMessage)) == 1
    assert await db_session.scalar(select(func.count()).select_from(SupportInboundReceipt)) == 1
    await db_session.refresh(registered)
    with pytest.raises(ApiError):
        await support_tickets.get_ticket(db_session, first_id, user=registered)


async def test_inbox_concurrent_import_has_one_committed_conversation(test_engine):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def receive(key):
        async with factory() as session:
            return await support_inbox.import_support_mail(
                session, mail(), delivery_key=key, support="support@yleum.ru"
            )

    results = await asyncio.gather(receive("uid1"), receive("uid2"))
    assert sum(row is not None for row in results) == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(SupportMessage)) == 1


async def test_inbox_wrapped_quoted_capability_never_reaches_rows_or_public_detail(db_session):
    capability = "c" * 64
    original, _ = await support_tickets.create_ticket(
        db_session,
        CreateTicket(**guest_payload(guest_token=capability)),
        user=None,
        ip="192.0.2.12",
    )
    message = BytesParser(policy=policy.default).parsebytes(mail())
    message.set_content(
        f"Спасибо\n> https://yleum.ru/max#support={original.id}&token= \n"
        f"> {capability[:32]} \n> {capability[32:]}",
        params={"format": "flowed"},
    )
    imported = await support_inbox.import_support_mail(
        db_session,
        message.as_bytes(),
        delivery_key="flowed-uid",
        support="support@yleum.ru",
    )
    dto = (await support_tickets.detail(db_session, imported)).model_dump_json()
    assert capability not in dto
    persisted = (
        await db_session.scalars(
            select(SupportMessage).where(SupportMessage.ticket_id == imported.id)
        )
    ).one()
    assert capability not in "".join(persisted.body.split()).replace(">", "")


async def test_html_only_mail_creates_normal_customer_ticket_with_safe_text(db_session):
    message = BytesParser(policy=policy.default).parsebytes(mail())
    message.set_content("<p>Помогите с публикацией</p><script>unsafe()</script>", subtype="html")
    imported = await support_inbox.import_support_mail(
        db_session,
        message.as_bytes(),
        delivery_key="html-uid",
        support="support@yleum.ru",
    )
    dto = await support_tickets.detail(db_session, imported)
    assert [item.body for item in dto.messages] == ["Помогите с публикацией"]
    assert dto.first_response_due_at is not None


async def test_inbox_crash_before_commit_rolls_back_receipt_and_ticket(db_session, monkeypatch):
    original = support_tickets._enqueue

    async def crash(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected precommit failure")

    monkeypatch.setattr(support_tickets, "_enqueue", crash)
    with pytest.raises(RuntimeError):
        await support_inbox.import_support_mail(
            db_session, mail(), delivery_key="uid1", support="support@yleum.ru"
        )
    await db_session.rollback()
    assert await db_session.scalar(select(func.count()).select_from(SupportInboundReceipt)) == 0
    assert await db_session.scalar(select(func.count()).select_from(SupportMessage)) == 0
    monkeypatch.setattr(support_tickets, "_enqueue", original)
    assert await support_inbox.import_support_mail(
        db_session, mail(), delivery_key="uid1", support="support@yleum.ru"
    )


async def test_concurrent_first_inbox_poll_retains_earliest_cursor_and_replays_safely(
    test_engine,
    db_session,
    monkeypatch,
):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr(support_inbox, "inbox_configured", lambda: True)
    starts = iter((9, 10))
    monkeypatch.setattr(
        support_inbox,
        "_fetch_mail",
        lambda cursors: [
            ("mailbox-epoch", next(starts), None, "support@yleum.ru"),
        ],
    )
    await asyncio.gather(
        support_inbox.import_support_inbox(factory), support_inbox.import_support_inbox(factory)
    )
    cursor = await db_session.get(SupportInboxCursor, "mailbox-epoch")
    assert cursor.last_uid == 9
    assert await db_session.scalar(select(func.count()).select_from(SupportMessage)) == 0
    monkeypatch.setattr(
        support_inbox,
        "_fetch_mail",
        lambda cursors: [
            ("mailbox-epoch", 10, mail(), "support@yleum.ru"),
        ],
    )
    await support_inbox.import_support_inbox(factory)
    await support_inbox.import_support_inbox(factory)
    await db_session.refresh(cursor)
    assert cursor.last_uid == 10
    assert await db_session.scalar(select(func.count()).select_from(SupportMessage)) == 1


async def test_inbox_signed_reply_is_customer_only_and_guessed_sup_cannot_append(db_session):
    admin = await account(db_session, "staff@example.com", "admin")
    first = await support_inbox.import_support_mail(
        db_session, mail(), delivery_key="uid1", support="support@yleum.ru"
    )
    signature = support_inbox.customer_thread_reference(first)
    reply_id = f"<{uuid4().hex}.{signature}@yleum.ru>"
    message = mail(
        **{"From": admin.email, "Message-ID": "<reply1@example.com>", "In-Reply-To": reply_id}
    )
    reply = await support_inbox.import_support_mail(
        db_session, message, delivery_key="uid2", support="support@yleum.ru"
    )
    assert reply.id == first.id and reply.first_responded_at is None
    assert all(
        row.author == "customer" for row in (await db_session.scalars(select(SupportMessage))).all()
    )
    guessed = mail(Subject=f"Re: SUP-{first.number}", **{"Message-ID": "<guess@example.com>"})
    new = await support_inbox.import_support_mail(
        db_session, guessed, delivery_key="uid3", support="support@yleum.ru"
    )
    assert new.id != first.id
    bad = BytesParser(policy=policy.default).parsebytes(message)
    bad.replace_header("In-Reply-To", f"<{uuid4().hex}.support.{first.id.hex}.{'a' * 16}@yleum.ru>")
    bad.replace_header("Message-ID", "<spoof@example.com>")
    assert (
        await support_inbox.import_support_mail(
            db_session, bad.as_bytes(), delivery_key="uid4", support="support@yleum.ru"
        )
        is None
    )

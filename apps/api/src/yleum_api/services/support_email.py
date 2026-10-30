"""Support-only SMTP outbox, isolated from commerce worker privileges."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yleum_api.core.admin import is_admin_user
from yleum_api.core.config import get_settings
from yleum_api.models.support import (
    SupportEmailOutbox,
    SupportMessage,
    SupportRateBucket,
    SupportTicket,
)
from yleum_api.models.user import User
from yleum_api.services.email_templates import support_email
from yleum_api.services.support_inbox import (
    customer_thread_reference,
    import_support_inbox,
    inbox_configured,
)
from yleum_api.services.support_tickets import digest, open_guest_delivery
from yleum_api.services.transactional_email import (
    EmailDeliveryFailed,
    EmailDeliveryNotConfigured,
    send_transactional_email,
)

log = structlog.get_logger(__name__)


async def maintain_support_outbox(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(SupportEmailOutbox)
        .where(
            SupportEmailOutbox.status == "sending",
            SupportEmailOutbox.claimed_at <= now - timedelta(minutes=10),
        )
        .values(status="uncertain")
    )
    expired = (
        select(SupportRateBucket.key, SupportRateBucket.window)
        .where(SupportRateBucket.window < now - timedelta(hours=48))
        .limit(1000)
    )
    await session.execute(
        delete(SupportRateBucket).where(
            tuple_(SupportRateBucket.key, SupportRateBucket.window).in_(expired)
        )
    )
    await session.commit()


async def deliver_one_support_email(factory: async_sessionmaker[AsyncSession]) -> bool:
    """Claim and commit before SMTP; an ambiguous send can never become pending."""
    async with factory() as session:
        item = (
            await session.scalars(
                select(SupportEmailOutbox)
                .where(
                    SupportEmailOutbox.status == "pending",
                    SupportEmailOutbox.next_attempt_at <= datetime.now(UTC),
                )
                .order_by(SupportEmailOutbox.next_attempt_at, SupportEmailOutbox.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
        ).one_or_none()
        if item is None:
            return False
        item.status = "sending"
        item.claimed_at = datetime.now(UTC)
        item.attempts += 1
        item_id = item.id
        await session.commit()

    async with factory() as session:
        item = await session.get(SupportEmailOutbox, item_id)
        if item is None or item.status != "sending":
            return True
        message = await session.get(SupportMessage, item.message_id)
        ticket = await session.get(SupportTicket, message.ticket_id) if message else None
        if message is None or ticket is None:
            return True
        staff = item.recipient_admin_id is not None
        if staff:
            admin = await session.get(User, item.recipient_admin_id)
            if (
                admin is None
                or admin.status != "active"
                or admin.is_anon
                or not is_admin_user(admin)
                or not admin.email
                or admin.email.lower() != item.recipient_email.lower()
            ):
                item.status = "cancelled"
                await session.commit()
                return True
        url = get_settings().web_base_url.rstrip("/")
        if staff:
            url += f"/admin/max?tab=support&ticket={ticket.id}"
        else:
            url += f"/max?support_ticket={ticket.id}"
            if item.delivery_payload_encrypted and ticket.owner_id is None:
                token = open_guest_delivery(ticket.id, item.delivery_payload_encrypted)
                if digest(token) != ticket.guest_token_hash:
                    item.status = "cancelled"
                    await session.commit()
                    return True
                url = get_settings().web_base_url.rstrip("/")
                url += f"/max#support={ticket.id}&token={token}"
        replies_enabled = inbox_configured()
        content = support_email(
            reference=f"SUP-{ticket.number}",
            subject=ticket.subject,
            body=message.body,
            url=url,
            staff=staff,
            acknowledgement=item.kind == "acknowledgement",
            created_at=ticket.created_at,
            first_response_due_at=ticket.first_response_due_at,
            first_responded_at=ticket.first_responded_at,
            email_replies=replies_enabled,
            logo_url=get_settings().web_base_url.rstrip("/") + "/brand/logo-mark.svg",
        )
        # Stable across safe retries and later inbox enablement; staff notices carry no capability.
        suffix = customer_thread_reference(ticket) if not staff else None
        recipient, attempts = item.recipient_email, item.attempts
        # Release the read transaction and connection while SMTP performs network IO.
        await session.rollback()

    next_status, retry_at = "sent", None
    try:
        await send_transactional_email(
            recipient=recipient,
            subject=content.subject,
            text=content.text,
            html=content.html,
            message_id=item_id,
            sender=get_settings().support_smtp_from,
            reply_to=get_settings().legal_support_email,
            inline_logo=False,
            auto_submitted=True,
            message_id_suffix=suffix,
        )
    except EmailDeliveryNotConfigured:
        next_status, retry_at = "pending", datetime.now(UTC) + timedelta(minutes=5)
    except EmailDeliveryFailed as exc:
        if not exc.retry_safe:
            next_status = "uncertain"
        elif attempts >= 8:
            next_status = "failed"
        else:
            next_status, retry_at = (
                "pending",
                datetime.now(UTC) + timedelta(seconds=min(3600, 30 * 2 ** min(attempts, 7))),
            )
    except Exception:
        # Unexpected errors may occur after SMTP acceptance. Never assume retry safety.
        next_status = "uncertain"
        log.warning("support_email_unexpected_failure")
    async with factory() as session:
        values: dict[str, object] = {"status": next_status}
        if retry_at is not None:
            values["next_attempt_at"] = retry_at
        if next_status == "sent":
            values["sent_at"] = datetime.now(UTC)
        await session.execute(
            update(SupportEmailOutbox)
            .where(SupportEmailOutbox.id == item_id, SupportEmailOutbox.status == "sending")
            .values(**values)
        )
        await session.commit()
    return True


async def run_support_email_forever() -> None:
    """Called only by the general worker, with its own thread/event-loop engine."""
    engine = create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        connect_args={"timeout": 5.0, "command_timeout": 10.0},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        while True:
            try:
                async with factory() as session:
                    await maintain_support_outbox(session)
                processed = 0
                while processed < 50 and await deliver_one_support_email(factory):
                    processed += 1
                await import_support_inbox(factory)
            except Exception:
                # Fixed diagnostic only: exception strings can contain SQL values/contacts.
                log.warning("support_email_tick_failed")
            await asyncio.sleep(10)
    finally:
        await engine.dispose()

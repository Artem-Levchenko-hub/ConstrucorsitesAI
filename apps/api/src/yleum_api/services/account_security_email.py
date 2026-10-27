"""Durable completed account changes; never stores tokens or passwords."""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yleum_api.core.config import get_settings
from yleum_api.models.account_security import AccountSecurityEmail
from yleum_api.services.email_templates import account_security_email
from yleum_api.services.transactional_email import (
    EmailDeliveryFailed,
    EmailDeliveryNotConfigured,
    send_transactional_email,
)

log = structlog.get_logger(__name__)


async def deliver_one_account_security_email(factory: async_sessionmaker[AsyncSession]) -> bool:
    async with factory() as session, session.begin():
        await session.execute(
            update(AccountSecurityEmail)
            .where(
                AccountSecurityEmail.status == "sending",
                AccountSecurityEmail.claimed_at <= datetime.now(UTC) - timedelta(minutes=10),
            )
            .values(status="uncertain")
        )
        event = await session.scalar(
            select(AccountSecurityEmail)
            .where(
                AccountSecurityEmail.status == "pending",
                AccountSecurityEmail.available_at <= datetime.now(UTC),
            )
            .order_by(AccountSecurityEmail.available_at, AccountSecurityEmail.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if event is None:
            return False
        event.status = "sending"
        event.claimed_at = datetime.now(UTC)
        event.attempts += 1
        event_id, recipient, kind, attempts = (
            event.id,
            event.recipient_email,
            event.kind,
            event.attempts,
        )
    # Claim committed before SMTP; recipient is frozen at the credential mutation.
    mail = account_security_email(
        kind=kind, recovery_url=get_settings().web_base_url.rstrip("/") + "/forgot-password"
    )
    state = "sent"
    try:
        await send_transactional_email(
            recipient=recipient,
            subject=mail.subject,
            text=mail.text,
            html=mail.html,
            message_id=event_id,
        )
    except EmailDeliveryNotConfigured:
        state = "pending"
    except EmailDeliveryFailed as exc:
        state = ("pending" if attempts < 8 else "failed") if exc.retry_safe else "uncertain"
    except Exception:
        state = "uncertain"
        log.warning("account_security_email_unexpected_failure")
    async with factory() as session, session.begin():
        await session.execute(
            update(AccountSecurityEmail)
            .where(AccountSecurityEmail.id == event_id, AccountSecurityEmail.status == "sending")
            .values(
                status=state,
                available_at=datetime.now(UTC) + timedelta(minutes=5),
                sent_at=datetime.now(UTC) if state == "sent" else None,
            )
        )
    return True


async def run_account_security_email_forever() -> None:
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
                for _ in range(50):
                    if not await deliver_one_account_security_email(factory):
                        break
            except Exception:
                log.warning("account_security_email_tick_failed")
            await asyncio.sleep(10)
    finally:
        await engine.dispose()

"""Transactional support domain. REST callers publish only after these commits."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import Text, and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.support import (
    SupportAudit,
    SupportEmailOutbox,
    SupportMessage,
    SupportRateBucket,
    SupportTicket,
)
from yleum_api.models.user import User
from yleum_api.schemas.support import (
    CreateMessage,
    CreateTicket,
    Message,
    SupportStatus,
    Ticket,
    TicketDetail,
)


def registered(user: User | None) -> User | None:
    return (
        user
        if user is not None and user.status == "active" and not user.is_anon and user.email
        else None
    )


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def actor_key(user: User | None, token: str | None) -> str:
    if user is not None:
        return f"user:{user.id}"
    return f"guest:{digest(token or '')}"


def archived(ticket: SupportTicket, now: datetime | None = None) -> bool:
    return bool(
        ticket.status == "done"
        and ticket.resolved_at is not None
        and ticket.resolved_at <= (now or datetime.now(UTC)) - timedelta(hours=24)
    )


async def _serialize(
    session: AsyncSession, tickets: list[SupportTicket], *, staff: bool
) -> list[Ticket]:
    if not tickets:
        return []
    ids = [ticket.id for ticket in tickets]
    counts = {
        key: value
        for key, value in (
            await session.execute(
                select(SupportMessage.ticket_id, func.count())
                .where(SupportMessage.ticket_id.in_(ids))
                .group_by(SupportMessage.ticket_id)
            )
        ).all()
    }
    previews = {
        key: value
        for key, value in (
            await session.execute(
                select(SupportMessage.ticket_id, SupportMessage.body)
                .where(SupportMessage.ticket_id.in_(ids))
                .distinct(SupportMessage.ticket_id)
                .order_by(
                    SupportMessage.ticket_id,
                    SupportMessage.created_at.desc(),
                    SupportMessage.id.desc(),
                )
            )
        ).all()
    }
    admins: dict[UUID, str | None] = {}
    if staff:
        assigned = [ticket.assigned_admin_id for ticket in tickets if ticket.assigned_admin_id]
        if assigned:
            admins = {
                key: value
                for key, value in (
                    await session.execute(select(User.id, User.email).where(User.id.in_(assigned)))
                ).all()
            }
    return [
        Ticket(
            id=t.id,
            reference=f"SUP-{t.number}",
            subject=t.subject,
            status=cast(SupportStatus, t.status),
            version=t.version,
            requester_email=t.requester_email,
            created_at=t.created_at,
            updated_at=t.updated_at,
            resolved_at=t.resolved_at,
            archived=archived(t),
            source_path=t.source_path,
            assigned_admin_email=admins.get(t.assigned_admin_id) if t.assigned_admin_id else None,
            message_count=counts.get(t.id, 0),
            last_message_preview=previews.get(t.id, "")[:200],
        )
        for t in tickets
    ]


async def detail(
    session: AsyncSession, ticket: SupportTicket, *, staff: bool = False
) -> TicketDetail:
    base = (await _serialize(session, [ticket], staff=staff))[0]
    messages = (
        await session.scalars(
            select(SupportMessage)
            .where(SupportMessage.ticket_id == ticket.id)
            .order_by(SupportMessage.created_at, SupportMessage.id)
        )
    ).all()
    return TicketDetail(
        **base.model_dump(),
        messages=[
            Message(
                id=m.id,
                author=cast(Literal["customer", "support"], m.author),
                body=m.body,
                created_at=m.created_at,
            )
            for m in messages
        ],
    )


async def get_ticket(
    session: AsyncSession,
    ticket_id: UUID,
    *,
    user: User | None = None,
    token: str | None = None,
    staff: bool = False,
    lock: bool = False,
) -> SupportTicket:
    query = select(SupportTicket).where(SupportTicket.id == ticket_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    ticket = (await session.scalars(query)).one_or_none()
    allowed = staff or (ticket is not None and user is not None and ticket.owner_id == user.id)
    if ticket is not None and ticket.owner_id is None and token and ticket.guest_token_hash:
        allowed = allowed or hmac.compare_digest(ticket.guest_token_hash, digest(token))
    if ticket is None or not allowed:
        raise ApiError("not_found", "Обращение не найдено", 404)
    return ticket


async def list_tickets(
    session: AsyncSession,
    *,
    user: User | None = None,
    staff: bool = False,
    status: SupportStatus | None = None,
    archive: bool = False,
    search: str = "",
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Ticket], int, int]:
    query = select(SupportTicket)
    if staff:
        is_archived = and_(
            SupportTicket.status == "done",
            SupportTicket.resolved_at <= datetime.now(UTC) - timedelta(hours=24),
        )
        query = query.where(is_archived if archive else ~is_archived)
        if status:
            query = query.where(SupportTicket.status == status)
        if search.strip():
            term = (
                "%"
                + search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            query = query.where(
                or_(
                    SupportTicket.subject.ilike(term, escape="\\"),
                    SupportTicket.requester_email.ilike(term, escape="\\"),
                    ("SUP-" + SupportTicket.number.cast(Text)).ilike(term, escape="\\"),
                )
            )
    elif user is None:
        raise ApiError("unauthorized", "Войдите в аккаунт", 401)
    else:
        query = query.where(SupportTicket.owner_id == user.id)
    total = int((await session.scalar(select(func.count()).select_from(query.subquery()))) or 0)
    rows = list(
        (
            await session.scalars(
                query.order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    new_count = (
        int(
            (
                await session.scalar(
                    select(func.count())
                    .select_from(SupportTicket)
                    .where(SupportTicket.status == "new")
                )
            )
            or 0
        )
        if staff
        else 0
    )
    return await _serialize(session, rows, staff=staff), total, new_count


async def _request_lock(session: AsyncSession, key: str) -> None:
    lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})


async def _admit(session: AsyncSession, *, actor: str, contact: str, ip: str, reply: bool) -> None:
    now = datetime.now(UTC)
    window = now.replace(minute=0, second=0, microsecond=0)
    prefix = "reply" if reply else "create"
    # Hash contacts/IPs with the deployment secret, not a reversible low-entropy digest.
    secret = get_settings().jwt_secret.get_secret_value().encode()

    def private(value: str) -> str:
        return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()

    limits = {
        f"{prefix}:actor:{private(actor)}": 60 if reply else 5,
        f"{prefix}:contact:{private(contact.lower())}": 60 if reply else 5,
        f"{prefix}:ip:{private(ip)}": 120 if reply else 20,
        f"{prefix}:global": 3000 if reply else 500,
    }
    for key, limit in sorted(limits.items()):
        statement = insert(SupportRateBucket).values(key=key, window=window, count=1)
        admitted = await session.scalar(
            statement.on_conflict_do_update(
                index_elements=[SupportRateBucket.key, SupportRateBucket.window],
                set_={"count": SupportRateBucket.count + 1},
                where=SupportRateBucket.count < limit,
            ).returning(SupportRateBucket.count)
        )
        if admitted is None:
            raise ApiError("rate_limited", "Слишком много сообщений. Попробуйте позже.", 429)


async def _enqueue(
    session: AsyncSession, ticket: SupportTicket, message: SupportMessage, *, staff: bool
) -> None:
    if staff:
        session.add(
            SupportEmailOutbox(message_id=message.id, recipient_email=ticket.requester_email)
        )
        return
    recipients = (
        await session.scalars(
            select(User).where(
                User.status == "active",
                User.is_anon.is_(False),
                User.email.is_not(None),
                or_(
                    User.role == "admin",
                    func.lower(User.email).in_(get_settings().admin_emails_set),
                ),
            )
        )
    ).all()
    for admin in recipients:
        assert admin.email is not None
        session.add(
            SupportEmailOutbox(
                message_id=message.id,
                recipient_email=admin.email.lower(),
                recipient_admin_id=admin.id,
            )
        )


async def create_ticket(
    session: AsyncSession, data: CreateTicket, *, user: User | None, ip: str
) -> tuple[SupportTicket, bool]:
    user = registered(user)
    if data.website:
        raise ApiError("bad_request", "Не удалось отправить обращение", 400)
    if user is None and (not data.email or not data.privacy_accepted or not data.guest_token):
        raise ApiError("bad_request", "Укажите email и подтвердите согласие", 422)
    email = cast(str, user.email if user else str(data.email))
    actor = actor_key(user, data.guest_token)
    key = digest(f"{actor}:{data.request_id}")
    payload = digest(
        json.dumps(
            [data.subject, data.message, email.lower(), data.source_path], ensure_ascii=False
        )
    )
    await _request_lock(session, key)
    existing = (
        await session.scalars(select(SupportTicket).where(SupportTicket.create_request_key == key))
    ).one_or_none()
    if existing is not None:
        if existing.create_payload_hash != payload:
            raise ApiError("conflict", "Этот запрос уже использован с другим содержимым", 409)
        return existing, False
    await _admit(session, actor=actor, contact=email, ip=ip, reply=False)
    ticket = SupportTicket(
        owner_id=user.id if user else None,
        guest_token_hash=digest(data.guest_token or "") if user is None else None,
        requester_email=email,
        subject=data.subject,
        create_request_key=key,
        create_payload_hash=payload,
        source_path=data.source_path,
    )
    session.add(ticket)
    await session.flush()
    message = SupportMessage(
        ticket_id=ticket.id,
        actor_key=actor,
        request_id=data.request_id,
        payload_hash=digest(data.message),
        author="customer",
        body=data.message,
    )
    session.add(message)
    await session.flush()
    await _enqueue(session, ticket, message, staff=False)
    await session.commit()
    return ticket, True


def _transition(
    session: AsyncSession, ticket: SupportTicket, status: SupportStatus, admin: User | None
) -> None:
    old = ticket.status
    ticket.status = status
    ticket.resolved_at = datetime.now(UTC) if status == "done" else None
    if admin is not None and status == "in_progress":
        ticket.assigned_admin_id = admin.id
    if old != status:
        session.add(
            SupportAudit(
                ticket_id=ticket.id,
                admin_id=admin.id if admin else None,
                old_status=old,
                new_status=status,
                version=ticket.version,
            )
        )


async def add_message(
    session: AsyncSession,
    ticket_id: UUID,
    data: CreateMessage,
    *,
    user: User | None,
    token: str | None,
    ip: str,
    staff: bool = False,
) -> tuple[SupportTicket, bool]:
    ticket = await get_ticket(session, ticket_id, user=user, token=token, staff=staff, lock=True)
    actor = actor_key(user, token)
    prior = (
        await session.scalars(
            select(SupportMessage).where(
                SupportMessage.ticket_id == ticket.id,
                SupportMessage.actor_key == actor,
                SupportMessage.request_id == data.request_id,
            )
        )
    ).one_or_none()
    if prior:
        if prior.payload_hash != digest(data.message):
            raise ApiError("conflict", "Этот запрос уже использован с другим содержимым", 409)
        return ticket, False
    await _admit(session, actor=actor, contact=ticket.requester_email, ip=ip, reply=True)
    message = SupportMessage(
        ticket_id=ticket.id,
        actor_key=actor,
        request_id=data.request_id,
        payload_hash=digest(data.message),
        author="support" if staff else "customer",
        body=data.message,
    )
    session.add(message)
    ticket.version += 1
    ticket.updated_at = datetime.now(UTC)
    if staff and ticket.status == "new":
        _transition(session, ticket, "in_progress", user)
    elif not staff and ticket.status == "done":
        _transition(session, ticket, "new", None)
    await session.flush()
    await _enqueue(session, ticket, message, staff=staff)
    await session.commit()
    return ticket, True


async def change_status(
    session: AsyncSession,
    ticket_id: UUID,
    *,
    user: User,
    status: SupportStatus,
    expected_version: int,
) -> tuple[SupportTicket, bool]:
    ticket = await get_ticket(session, ticket_id, staff=True, lock=True)
    if ticket.version != expected_version:
        raise ApiError("conflict", "Обращение изменилось. Обновите его и повторите действие.", 409)
    if ticket.status == status:
        return ticket, False
    ticket.version += 1
    ticket.updated_at = datetime.now(UTC)
    _transition(session, ticket, status, user)
    await session.commit()
    return ticket, True

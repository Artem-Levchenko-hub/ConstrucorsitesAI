from __future__ import annotations

import asyncio
import json
import re
from contextlib import suppress
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.admin import is_admin_user
from yleum_api.core.config import get_settings
from yleum_api.core.db import get_engine
from yleum_api.core.deps import AdminUserDep, OptionalUserDep, SessionDep, resolve_session_user
from yleum_api.core.errors import ApiError
from yleum_api.core.ratelimit import _client_ip
from yleum_api.models.support import SupportTicket
from yleum_api.schemas.support import (
    AdminTicketList,
    ChangeStatus,
    CreateMessage,
    CreateTicket,
    SupportContext,
    SupportStatus,
    TicketDetail,
    TicketList,
)
from yleum_api.services import support_tickets as domain
from yleum_api.services.support_realtime import publish_support_change, support_hub

router = APIRouter(prefix="/api", tags=["support"])
_AUTH_RECHECK_SECONDS = 15.0
SupportToken = Annotated[str | None, Header(alias="X-Support-Token", max_length=64)]


def allowed_origin(origin: str | None) -> bool:
    settings = get_settings()
    allowed = {
        settings.web_base_url.rstrip("/"),
        *(item.rstrip("/") for item in settings.cors_origins_list),
    }
    return origin is not None and origin != "null" and origin in allowed


def require_origin(request: Request) -> None:
    if not allowed_origin(request.headers.get("origin")):
        raise ApiError("forbidden", "Недопустимый источник запроса", 403)


async def _result(
    session: AsyncSession,
    ticket: SupportTicket,
    changed: bool,
    *,
    staff: bool,
    reason: str,
    author: str | None = None,
) -> TicketDetail:
    if changed:
        await publish_support_change(ticket, reason=reason, author=author)
    return await domain.detail(session, ticket, staff=staff)


@router.get("/support/context", response_model=SupportContext)
async def context(user: OptionalUserDep) -> SupportContext:
    account = domain.registered(user)
    return SupportContext(
        user_id=account.id if account else None,
        email=account.email if account else None,
        is_admin=is_admin_user(account) if account else False,
    )


@router.post("/support/tickets", response_model=TicketDetail)
async def create_ticket(
    data: CreateTicket, request: Request, session: SessionDep, user: OptionalUserDep
) -> TicketDetail:
    require_origin(request)
    ticket, changed = await domain.create_ticket(session, data, user=user, ip=_client_ip(request))
    return await _result(session, ticket, changed, staff=False, reason="created", author="customer")


@router.get("/support/tickets", response_model=TicketList)
async def customer_list(
    session: SessionDep,
    user: OptionalUserDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> TicketList:
    items, total, _ = await domain.list_tickets(
        session, user=domain.registered(user), limit=limit, offset=offset
    )
    return TicketList(items=items, total=total)


@router.get("/support/tickets/{ticket_id}", response_model=TicketDetail)
async def customer_detail(
    ticket_id: UUID, session: SessionDep, user: OptionalUserDep, token: SupportToken = None
) -> TicketDetail:
    ticket = await domain.get_ticket(session, ticket_id, user=domain.registered(user), token=token)
    return await domain.detail(session, ticket)


@router.post("/support/tickets/{ticket_id}/messages", response_model=TicketDetail)
async def customer_message(
    ticket_id: UUID,
    data: CreateMessage,
    request: Request,
    session: SessionDep,
    user: OptionalUserDep,
    token: SupportToken = None,
) -> TicketDetail:
    require_origin(request)
    ticket, changed = await domain.add_message(
        session, ticket_id, data, user=domain.registered(user), token=token, ip=_client_ip(request)
    )
    return await _result(session, ticket, changed, staff=False, reason="message", author="customer")


@router.get("/admin/support/tickets", response_model=AdminTicketList)
async def admin_list(
    session: SessionDep,
    admin: AdminUserDep,
    status: SupportStatus | None = None,
    archived: bool = False,
    search: Annotated[str, Query(max_length=160)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> AdminTicketList:
    items, total, new_count = await domain.list_tickets(
        session,
        staff=True,
        status=status,
        archive=archived,
        search=search,
        limit=limit,
        offset=offset,
    )
    return AdminTicketList(items=items, total=total, new_count=new_count)


@router.get("/admin/support/tickets/{ticket_id}", response_model=TicketDetail)
async def admin_detail(ticket_id: UUID, session: SessionDep, admin: AdminUserDep) -> TicketDetail:
    ticket = await domain.get_ticket(session, ticket_id, staff=True)
    return await domain.detail(session, ticket, staff=True)


@router.post("/admin/support/tickets/{ticket_id}/messages", response_model=TicketDetail)
async def admin_message(
    ticket_id: UUID, data: CreateMessage, request: Request, session: SessionDep, admin: AdminUserDep
) -> TicketDetail:
    require_origin(request)
    ticket, changed = await domain.add_message(
        session, ticket_id, data, user=admin, token=None, ip=_client_ip(request), staff=True
    )
    return await _result(session, ticket, changed, staff=True, reason="message", author="support")


@router.patch("/admin/support/tickets/{ticket_id}", response_model=TicketDetail)
async def admin_status(
    ticket_id: UUID, data: ChangeStatus, request: Request, session: SessionDep, admin: AdminUserDep
) -> TicketDetail:
    require_origin(request)
    ticket, changed = await domain.change_status(
        session, ticket_id, user=admin, status=data.status, expected_version=data.expected_version
    )
    return await _result(session, ticket, changed, staff=True, reason="status")


async def _socket_identity(
    factory: async_sessionmaker[AsyncSession],
    cookie: str | None,
    authorization: str | None,
    ticket_id: UUID | None,
    guest_token: str | None,
) -> tuple[str, UUID | None] | None:
    async with factory() as session:
        user = domain.registered(await resolve_session_user(session, cookie, authorization))
        if ticket_id is not None:
            try:
                ticket = await domain.get_ticket(session, ticket_id, token=guest_token)
            except ApiError:
                return None
            return f"guest:{ticket.id}", None
        if user is None:
            return None
        return ("admins" if is_admin_user(user) else f"user:{user.id}"), user.id


@router.websocket("/ws/support")
async def support_socket(ws: WebSocket) -> None:
    if not allowed_origin(ws.headers.get("origin")):
        await ws.close(code=1008)
        return
    await ws.accept()
    channel: str | None = None
    queue: asyncio.Queue[dict[str, Any]] | None = None
    pending: list[asyncio.Task[Any]] = []
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=5)
        if len(raw) > 1024:
            await ws.close(code=1008)
            return
        frame = json.loads(raw)
        if not isinstance(frame, dict) or frame.get("type") != "subscribe":
            await ws.close(code=1008)
            return
        ticket_id = UUID(frame["ticket_id"]) if frame.get("ticket_id") else None
        token = frame.get("guest_token")
        if ticket_id and (not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token)):
            await ws.close(code=1008)
            return
        cookie = ws.cookies.get(get_settings().jwt_cookie_name)
        authorization = ws.headers.get("authorization")
        factory = async_sessionmaker(get_engine(), expire_on_commit=False)
        identity = await asyncio.wait_for(
            _socket_identity(factory, cookie, authorization, ticket_id, token), timeout=5
        )
        if identity is None:
            await ws.close(code=1008)
            return
        channel = identity[0]
        queue = support_hub.subscribe(channel)
        await ws.send_json({"type": "support.ready"})
        loop = asyncio.get_running_loop()
        next_check = loop.time() + _AUTH_RECHECK_SECONDS
        incoming = asyncio.create_task(ws.receive_text())
        event = asyncio.create_task(queue.get())
        pending = [incoming, event]
        while True:
            timeout = max(0.0, next_check - loop.time())
            done, _ = await asyncio.wait(
                pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if loop.time() >= next_check:
                current = await asyncio.wait_for(
                    _socket_identity(factory, cookie, authorization, ticket_id, token), timeout=5
                )
                if current != identity:
                    await ws.close(code=1008)
                    return
                next_check = loop.time() + _AUTH_RECHECK_SECONDS
            if incoming in done:
                payload = incoming.result()
                if len(payload) > 1024:
                    await ws.close(code=1008)
                    return
                await ws.send_json({"type": "support.pong"})
                incoming = asyncio.create_task(ws.receive_text())
            if event in done:
                await ws.send_json(event.result())
                event = asyncio.create_task(queue.get())
            pending = [incoming, event]
    except (ValueError, TypeError, KeyError, TimeoutError):
        with suppress(RuntimeError):
            await ws.close(code=1008)
    except WebSocketDisconnect:
        pass
    finally:
        if channel is not None and queue is not None:
            support_hub.unsubscribe(channel, queue)
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task

"""Explicit TLS mailbox ingestion. Email identities never grant ticket/staff authority."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import imaplib
import re
import secrets
import ssl
from dataclasses import dataclass
from email import policy
from email.message import MIMEPart
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
from uuid import UUID, uuid5

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.models.support import (
    SupportInboundReceipt,
    SupportInboxCursor,
    SupportTicket,
)
from yleum_api.models.user import User
from yleum_api.schemas.support import CreateMessage, CreateTicket
from yleum_api.services import support_tickets as domain
from yleum_api.services.support_realtime import publish_support_change

MAX_BYTES = 262144
_NAMESPACE = UUID("ce80b2c5-52cb-44dd-b10f-68dd59071480")
_THREAD = re.compile(r"support\.([0-9a-f]{32})\.([A-Za-z0-9_-]{16})")
_QUOTED_THREAD = re.compile(
    r"support\.[\s>]*(?:[0-9a-f][\s>]*){32}\.[\s>]*(?:[A-Za-z0-9_-][\s>]*){16}",
    re.IGNORECASE,
)
_QUOTED_CAPABILITY = re.compile(r"(?:[0-9a-f][\s>]*){64}", re.IGNORECASE)
_EMAIL = TypeAdapter(EmailStr)


@dataclass(frozen=True)
class IncomingMail:
    sender: str
    recipient: str
    subject: str
    body: str
    message_key: str
    thread_reference: str | None = None


def inbox_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.support_imap_host
        and settings.support_imap_user
        and settings.support_imap_password
        and settings.support_imap_mailbox
        and settings.support_imap_recipient
        and settings.support_imap_recipient.lower() == settings.legal_support_email.lower()
    )


def _thread_signature(ticket: SupportTicket) -> str:
    # Scope includes current ownership; rotating the deployment secret revokes email replies.
    scope = f"support-email:{ticket.id}:{ticket.guest_token_hash or ticket.owner_id}"
    signature = hmac.new(
        get_settings().jwt_secret.get_secret_value().encode(), scope.encode(), hashlib.sha256
    ).digest()[:12]
    return base64.urlsafe_b64encode(signature).decode().rstrip("=")


def customer_thread_reference(ticket: SupportTicket) -> str:
    return f"support.{ticket.id.hex}.{_thread_signature(ticket)}"


def valid_thread_reference(ticket: SupportTicket, reference: str) -> bool:
    return hmac.compare_digest(customer_thread_reference(ticket), reference)


def _recipient(addresses: list[str], support: str) -> str | None:
    for _, address in getaddresses(addresses):
        address = address.lower()
        if address == support.lower():
            return address
    return None


def _text_parts(message: MIMEPart[str, str], content_type: str) -> list[str]:
    if (
        message.get_filename()
        or message.get_content_disposition() == "attachment"
        or message.get_content_maintype() == "message"
    ):
        return []
    if message.get_content_maintype() == "multipart":
        return [text for child in message.iter_parts() for text in _text_parts(child, content_type)]
    if message.get_content_type() == content_type:
        content = message.get_content()
        return [content] if isinstance(content, str) else []
    return []


class _HTMLText(HTMLParser):
    """Extract visible text only. HTMLParser performs no network/file lookups."""

    _hidden = frozenset({"head", "script", "style", "template", "noscript", "svg", "iframe"})
    _blocks = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "blockquote"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._hidden:
            self.hidden_depth += 1
        elif self.hidden_depth == 0 and tag in self._blocks:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._hidden and self.hidden_depth:
            self.hidden_depth -= 1
        elif self.hidden_depth == 0 and tag in self._blocks:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0:
            self.chunks.append(data)


def _message_text(message: MIMEPart[str, str]) -> str:
    plain = "\n".join(_text_parts(message, "text/plain")).strip()
    if plain:
        return plain
    parser = _HTMLText()
    parser.feed("\n".join(_text_parts(message, "text/html")))
    parser.close()
    return "\n".join(line.strip() for line in "".join(parser.chunks).splitlines() if line.strip())


def _redact_delivery_capabilities(text: str) -> str:
    # Conservative by design: SHA256-like values are also hidden. Whitespace
    # and quote wrapping must not turn a delivery secret into persisted content.
    text = _QUOTED_THREAD.sub("[почтовая ссылка скрыта]", text)
    return _QUOTED_CAPABILITY.sub("[секрет скрыт]", text)


def parse_support_mail(raw: bytes, support: str) -> IncomingMail | None:
    """Reject unbounded/automated MIME; attachments and HTML are never ingested."""
    if len(raw) > MAX_BYTES:
        return None
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        parts = []
        for part in message.walk():
            parts.append(part)
            if len(parts) > 30 or part.defects:
                return None
        recipient = _recipient(message.get_all("To", []) + message.get_all("Cc", []), support)
        senders = getaddresses(message.get_all("From", []))
        if recipient is None or len(senders) != 1:
            return None
        sender = str(_EMAIL.validate_python(senders[0][1])).lower()
        sender_local = sender.split("@", 1)[0]
        if (
            sender == support.lower()
            or sender_local.startswith(("mailer-daemon", "postmaster"))
            or message.get("List-ID")
            or message.get("List-Unsubscribe")
            or str(message.get("Precedence", "")).lower() in {"bulk", "list", "junk"}
            or str(message.get("Auto-Submitted", "no")).lower() != "no"
            or str(message.get("Return-Path", "")).strip() == "<>"
        ):
            return None
        if any(
            part.get_content_type()
            in {"multipart/report", "message/delivery-status", "message/disposition-notification"}
            for part in parts
        ):
            return None
        body = _message_text(message)
        if not body or len(body) > 8000:
            return None
        # Mail clients quote the receipt, including its recovery link. Never turn a
        # delivery capability into persisted conversation text or a public DTO.
        body = _redact_delivery_capabilities(body)
        if any(part.defects for part in parts):
            return None
        subject = str(message.get("Subject", "Обращение по email")).strip() or "Обращение по email"
        if len(subject) > 160 or any(ord(char) < 32 for char in subject):
            return None
        subject = _redact_delivery_capabilities(subject)
        identity = str(message.get("Message-ID", "")).strip()
        if len(identity) > 998:
            return None
        # Without Message-ID only byte-identical delivery replays deduplicate.
        key = domain.digest(f"{sender}:{identity or hashlib.sha256(raw).hexdigest()}")
        reference_headers = str(message.get("In-Reply-To", "")) or str(
            message.get("References", "")
        )
        references = re.findall(
            r"<[0-9a-f]{32}\.(support\.[0-9a-f]{32}\.[A-Za-z0-9_-]{16})@yleum\.ru>",
            reference_headers,
        )
        reference = references[-1] if references else None
        if "support." in reference_headers and reference is None:
            return None
        return IncomingMail(sender, recipient, subject, body, key, reference)
    except (ValueError, TypeError, LookupError, UnicodeError, ValidationError, RecursionError):
        return None


async def import_support_mail(
    session: AsyncSession, raw: bytes, *, delivery_key: str, support: str
) -> SupportTicket | None:
    """Receipt and conversation commit together; concurrent/crash replays have one effect."""
    parsed = parse_support_mail(raw, support)
    if parsed is None:
        return None
    key = domain.digest(delivery_key)
    admitted = await session.scalar(
        insert(SupportInboundReceipt)
        .values(
            delivery_key=key,
            message_key=parsed.message_key,
        )
        .on_conflict_do_nothing()
        .returning(SupportInboundReceipt.delivery_key)
    )
    if admitted is None:
        await session.rollback()
        return None
    if parsed.thread_reference:
        match = _THREAD.fullmatch(parsed.thread_reference)
        ticket = await session.get(SupportTicket, UUID(hex=match[1])) if match else None
        if ticket is None or not valid_thread_reference(ticket, parsed.thread_reference):
            await session.commit()  # Invalid guessed capability cannot become a fresh ticket.
            return None
        # Cryptographic email capability authorizes only a customer message, never a staff reply.
        # Use the normal domain lock/admission/idempotency path after this narrow boundary check.
        owner = (
            domain.registered(await session.get(User, ticket.owner_id)) if ticket.owner_id else None
        )
        guest_payload = (
            await domain.guest_delivery_payload(session, ticket.id) if not ticket.owner_id else None
        )
        if (ticket.owner_id and owner is None) or (not ticket.owner_id and not guest_payload):
            await session.commit()
            return None
        token = domain.open_guest_delivery(ticket.id, guest_payload) if guest_payload else None
        ticket, changed = await domain.add_message(
            session,
            ticket.id,
            CreateMessage(request_id=uuid5(_NAMESPACE, parsed.message_key), message=parsed.body),
            user=owner,
            token=token,
            ip="support-inbox",
            commit=False,
        )
    else:
        # Unverified From must never claim a registered account or an existing guest ticket.
        ticket, changed = await domain.create_ticket(
            session,
            CreateTicket(
                request_id=uuid5(_NAMESPACE, parsed.message_key),
                subject=parsed.subject,
                message=parsed.body,
                email=parsed.sender,
                privacy_accepted=True,
                guest_token=secrets.token_hex(32),
                source_path="/contact",
            ),
            user=None,
            ip="support-inbox",
            commit=False,
        )
    await session.execute(
        update(SupportInboundReceipt)
        .where(SupportInboundReceipt.delivery_key == key)
        .values(ticket_id=ticket.id)
    )
    await session.commit()
    if changed:
        await publish_support_change(ticket, reason="message", author="customer")
    return ticket


def _fetch_mail(cursors: dict[str, int]) -> list[tuple[str, int, bytes | None, str]]:
    """TLS verification precedes login; partial PEEK fetch bounds server-controlled payload."""
    settings = get_settings()
    assert settings.support_imap_host and settings.support_imap_user
    assert settings.support_imap_password and settings.support_imap_mailbox
    result: list[tuple[str, int, bytes | None, str]] = []
    with imaplib.IMAP4_SSL(
        settings.support_imap_host,
        settings.support_imap_port,
        ssl_context=ssl.create_default_context(),
        timeout=15,
    ) as inbox:
        inbox.login(settings.support_imap_user, settings.support_imap_password.get_secret_value())
        status, _ = inbox.select(settings.support_imap_mailbox, readonly=True)
        if status != "OK":
            return []
        _, validity = inbox.response("UIDVALIDITY")
        if not validity or not validity[0] or not validity[0].isdigit():
            return []
        scope = domain.digest(
            f"{settings.support_imap_host}:{settings.support_imap_user}:"
            f"{settings.support_imap_mailbox}"
        )
        mailbox = f"{scope}:{int(validity[0])}"
        previous = cursors.get(mailbox)
        if previous is None:
            scope_seen = any(key.startswith(scope + ":") for key in cursors)
            if settings.support_imap_start_uid is None and not scope_seen:
                _, next_uid = inbox.response("UIDNEXT")
                if not next_uid or not next_uid[0] or not next_uid[0].isdigit():
                    return []
                return [
                    (
                        mailbox,
                        max(0, int(next_uid[0]) - 1),
                        None,
                        str(settings.support_imap_recipient),
                    )
                ]
            # After a UIDVALIDITY reset, replay UID1 and deduplicate durable receipts.
            previous = 0 if scope_seen else (settings.support_imap_start_uid or 1) - 1
        status, data = inbox.uid("search", "UID", f"{previous + 1}:*")
        if status != "OK" or not data or not data[0]:
            return []
        for uid in sorted(int(value) for value in data[0].split() if int(value) > previous)[:50]:
            status, content = inbox.uid("fetch", str(uid), f"(BODY.PEEK[]<0.{MAX_BYTES + 1}>)")
            if status != "OK":
                break  # Never move the durable cursor past an unreadable message.
            fetched = False
            for item in content:
                if isinstance(item, tuple) and isinstance(item[1], bytes):
                    result.append((mailbox, uid, item[1], str(settings.support_imap_recipient)))
                    fetched = True
                    break
            if not fetched:
                break
    return result


async def import_support_inbox(factory: async_sessionmaker[AsyncSession]) -> None:
    if not inbox_configured():
        return
    async with factory() as session:
        cursors = {
            key: value
            for key, value in (
                await session.execute(
                    select(SupportInboxCursor.mailbox_key, SupportInboxCursor.last_uid)
                )
            ).all()
        }
    for mailbox, uid, raw, support in await asyncio.to_thread(_fetch_mail, cursors):
        async with factory() as session:
            if raw is not None:
                await import_support_mail(
                    session, raw, delivery_key=f"{mailbox}:{uid}", support=support
                )
            # After a crash before this commit, the receipt deduplicates the replay.
            statement = insert(SupportInboxCursor).values(mailbox_key=mailbox, last_uid=uid)
            # Concurrent first polls may observe different UIDNEXT values. Retain
            # the earlier start; a harmless replay is safer than skipping a new mail.
            checkpoint = (
                func.least(SupportInboxCursor.last_uid, uid)
                if raw is None
                else func.greatest(SupportInboxCursor.last_uid, uid)
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[SupportInboxCursor.mailbox_key],
                    set_={"last_uid": checkpoint},
                )
            )
            await session.commit()

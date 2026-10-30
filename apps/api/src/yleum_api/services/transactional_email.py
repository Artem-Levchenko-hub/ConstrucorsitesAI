from __future__ import annotations

import asyncio
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from yleum_api.core.config import get_settings

log = structlog.get_logger(__name__)


class EmailDeliveryNotConfigured(RuntimeError):
    pass


class EmailDeliveryFailed(RuntimeError):
    def __init__(self, message: str, *, retry_safe: bool = False, category: str = "unknown"):
        super().__init__(message)
        self.retry_safe = retry_safe
        self.category = category


def email_delivery_configured() -> bool:
    """Письмо уйдёт, только если есть и адрес сервера, и учётные данные.

    Половинчатая настройка опаснее пустой: интерфейс по одному лишь хосту решал,
    что канал работает, и обещал человеку письмо, которое релей отвергает.
    """
    settings = get_settings()
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def _smtp_code(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return None


def _failure_details(exc: OSError) -> tuple[str, int | None]:
    """Only fixed categories and protocol numbers may leave this boundary."""
    code = None
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = {_smtp_code(reply[0]) for reply in exc.recipients.values()}
        if len(codes) == 1:
            code = codes.pop()
    elif isinstance(exc, smtplib.SMTPResponseException):
        code = _smtp_code(exc.smtp_code)

    # Specific subclasses must precede SMTPException/OSError fallbacks.
    for error_type, category in (
        (ssl.SSLCertVerificationError, "tls_certificate"),
        (ssl.SSLError, "tls"),
        (smtplib.SMTPAuthenticationError, "authentication"),
        (smtplib.SMTPRecipientsRefused, "recipient_refused"),
        (smtplib.SMTPSenderRefused, "sender_refused"),
        (smtplib.SMTPDataError, "data_rejected"),
        (smtplib.SMTPConnectError, "connection"),
        (smtplib.SMTPHeloError, "greeting"),
        (smtplib.SMTPNotSupportedError, "unsupported"),
        (smtplib.SMTPServerDisconnected, "disconnected"),
        (TimeoutError, "timeout"),
        (smtplib.SMTPException, "smtp"),
    ):
        if isinstance(exc, error_type):
            return category, code
    return "network", code


async def send_transactional_email(
    *,
    recipient: str,
    subject: str,
    text: str,
    html: str | None = None,
    message_id: UUID | None = None,
    sender: str | None = None,
    reply_to: str | None = None,
    inline_logo: bool = True,
    auto_submitted: bool = False,
    message_id_suffix: str | None = None,
) -> None:
    settings = get_settings()
    smtp_host = settings.smtp_host
    # Проверка на сам хост оставлена отдельно: она же сужает тип для smtplib.
    if not smtp_host or not email_delivery_configured():
        raise EmailDeliveryNotConfigured("SMTP delivery is not configured")

    message = EmailMessage()
    message["From"] = sender or settings.smtp_from
    if reply_to:
        message["Reply-To"] = reply_to
    if auto_submitted:
        message["Auto-Submitted"] = "auto-generated"
        message["X-Auto-Response-Suppress"] = "All"
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime.now(UTC))
    # Public namespace; never derive the ID from hostnames, users or auth tokens.
    suffix = f".{message_id_suffix}" if message_id_suffix else ""
    message["Message-ID"] = f"<{(message_id or uuid4()).hex}{suffix}@yleum.ru>"
    message.set_content(text)
    if html is not None:
        message.add_alternative(html, subtype="html")
    if html is not None and inline_logo:
        logo = Path(__file__).parent.parent / "assets" / "email" / "technical-logo.png"
        body = message.get_body(preferencelist=("html",))
        assert body is not None
        body.add_related(
            logo.read_bytes(),
            maintype="image",
            subtype="png",
            cid="<yleum-logo>",
            disposition="inline",
            filename="yleum.png",
        )

    submission_started = False
    accepted = False

    def _send() -> None:
        nonlocal submission_started, accepted
        with smtplib.SMTP(smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_user and settings.smtp_password:
                smtp.login(
                    settings.smtp_user,
                    settings.smtp_password.get_secret_value(),
                )
            submission_started = True
            smtp.send_message(message)
            accepted = True

    try:
        await asyncio.to_thread(_send)
    except OSError as exc:  # SMTPException is also an OSError.
        category, smtp_code = _failure_details(exc)
        if accepted:
            # A failed QUIT cannot undo a confirmed DATA acceptance.
            return
        log.warning("transactional_email_delivery_failed", category=category, smtp_code=smtp_code)
        # Server replies can contain addresses or credentials. Keep them out of
        # both structured logs and the public exception's formatted chain.
        definitive = isinstance(
            exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError)
        )
        raise EmailDeliveryFailed(
            "SMTP delivery failed",
            retry_safe=not submission_started or definitive,
            category=category,
        ) from None

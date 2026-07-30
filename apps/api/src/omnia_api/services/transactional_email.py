from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from omnia_api.core.config import get_settings


class EmailDeliveryNotConfigured(RuntimeError):
    pass


class EmailDeliveryFailed(RuntimeError):
    pass


async def send_transactional_email(*, recipient: str, subject: str, text: str) -> None:
    settings = get_settings()
    smtp_host = settings.smtp_host
    if not smtp_host:
        raise EmailDeliveryNotConfigured("SMTP delivery is not configured")

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)

    def _send() -> None:
        with smtplib.SMTP(smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(
                    settings.smtp_user,
                    settings.smtp_password.get_secret_value(),
                )
            smtp.send_message(message)

    try:
        await asyncio.to_thread(_send)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryFailed("SMTP delivery failed") from exc

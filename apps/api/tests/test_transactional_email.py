from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from yleum_api.services import transactional_email as email_service


class SMTPStub:
    def __init__(self) -> None:
        self.context: ssl.SSLContext | None = None
        self.failure: Exception | None = None
        self.authenticated = False
        self.messages: list[EmailMessage] = []

    def __enter__(self) -> SMTPStub:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def starttls(self, *, context: ssl.SSLContext | None = None) -> None:
        self.context = context
        if self.failure is not None:
            raise self.failure

    def login(self, user: str, password: str) -> None:
        self.authenticated = True

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


@pytest.fixture
def mail_settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    settings = SimpleNamespace(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_starttls=True,
        smtp_user="sender@example.com",
        smtp_password=SecretStr("dummy-smtp-password"),
        smtp_from="Yleum <sender@example.com>",
    )
    monkeypatch.setattr(email_service, "get_settings", lambda: settings)
    return settings


@pytest.mark.asyncio
async def test_smtp_requires_verified_server_identity_before_authentication(
    monkeypatch: pytest.MonkeyPatch, mail_settings: SimpleNamespace
) -> None:
    smtp = SMTPStub()
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: smtp)

    await email_service.send_transactional_email(
        recipient="recipient@example.com", subject="Code", text="Verification message"
    )

    assert smtp.context is not None
    assert smtp.context.verify_mode == ssl.CERT_REQUIRED
    assert smtp.context.check_hostname is True
    assert smtp.authenticated is True
    assert len(smtp.messages) == 1
    assert smtp.messages[0]["From"] == mail_settings.smtp_from
    assert smtp.messages[0]["To"] == "recipient@example.com"


@pytest.mark.asyncio
async def test_invalid_smtp_certificate_prevents_authentication_and_delivery(
    monkeypatch: pytest.MonkeyPatch, mail_settings: SimpleNamespace
) -> None:
    smtp = SMTPStub()
    smtp.failure = ssl.SSLCertVerificationError("invalid certificate")
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: smtp)

    with pytest.raises(email_service.EmailDeliveryFailed, match="SMTP delivery failed"):
        await email_service.send_transactional_email(
            recipient="recipient@example.com", subject="Code", text="Verification message"
        )

    assert smtp.authenticated is False
    assert smtp.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["smtp_host", "smtp_user", "smtp_password"])
async def test_incomplete_smtp_configuration_does_not_open_connection(
    monkeypatch: pytest.MonkeyPatch, mail_settings: SimpleNamespace, missing: str
) -> None:
    setattr(mail_settings, missing, None)

    def unexpected_connection(*args: object, **kwargs: object) -> None:
        pytest.fail("SMTP connection opened with incomplete credentials")

    monkeypatch.setattr(smtplib, "SMTP", unexpected_connection)
    with pytest.raises(email_service.EmailDeliveryNotConfigured):
        await email_service.send_transactional_email(
            recipient="recipient@example.com", subject="Code", text="Verification message"
        )

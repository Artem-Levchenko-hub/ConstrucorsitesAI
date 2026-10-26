from __future__ import annotations

import smtplib
import socket
import ssl
import traceback
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

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


@pytest.mark.asyncio
async def test_outbound_messages_have_utc_date_and_unique_private_message_ids(
    monkeypatch: pytest.MonkeyPatch, mail_settings: SimpleNamespace
) -> None:
    smtp = SMTPStub()
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: smtp)
    monkeypatch.setattr(socket, "getfqdn", lambda: "private-machine.internal")
    before = datetime.now(UTC) - timedelta(seconds=1)
    with capture_logs() as logs:
        for _ in range(2):
            await email_service.send_transactional_email(
                recipient="recipient@example.com", subject="Code", text="private-token-123456"
            )
    after = datetime.now(UTC)

    ids = []
    for sent in smtp.messages:
        message = BytesParser(policy=policy.default).parsebytes(sent.as_bytes())
        assert message["Date"] is not None
        date = parsedate_to_datetime(str(message["Date"]))
        assert date.utcoffset() == timedelta(0)
        assert before <= date <= after
        assert len(message.get_all("Date", [])) == 1
        assert len(message.get_all("Message-ID", [])) == 1
        message_id = str(message["Message-ID"])
        assert message_id.startswith("<") and message_id.endswith(">")
        assert message_id.count("@") == 1
        assert not message["Message-ID"].defects
        assert not any(
            private in message_id
            for private in ("private-machine", "recipient", "private-token", "sender")
        )
        assert message.get_content().strip() == "private-token-123456"
        ids.append(message_id)
    assert len(ids) == len(set(ids)) == 2
    assert logs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "category", "smtp_code"),
    [
        (ssl.SSLCertVerificationError("private-server-reply"), "tls_certificate", None),
        (ssl.SSLError("private-server-reply"), "tls", None),
        (smtplib.SMTPAuthenticationError(535, b"private-server-reply"), "authentication", 535),
        (
            smtplib.SMTPRecipientsRefused(
                {"private-address@example.com": (550, b"private-server-reply")}
            ),
            "recipient_refused",
            550,
        ),
        (
            smtplib.SMTPSenderRefused(553, b"private-server-reply", "private-address@example.com"),
            "sender_refused",
            553,
        ),
        (smtplib.SMTPDataError(554, b"private-server-reply"), "data_rejected", 554),
        (smtplib.SMTPConnectError(421, b"private-server-reply"), "connection", 421),
        (smtplib.SMTPHeloError(501, b"private-server-reply"), "greeting", 501),
        (smtplib.SMTPNotSupportedError("private-server-reply"), "unsupported", None),
        (smtplib.SMTPServerDisconnected("private-server-reply"), "disconnected", None),
        (TimeoutError("private-server-reply"), "timeout", None),
        (ConnectionRefusedError("private-server-reply"), "network", None),
        (smtplib.SMTPResponseException(450, b"private-server-reply"), "smtp", 450),
        (smtplib.SMTPException("private-server-reply"), "smtp", None),
        (
            smtplib.SMTPResponseException("private-server-reply", b"private-server-reply"),
            "smtp",
            None,
        ),
        (
            smtplib.SMTPRecipientsRefused(
                {
                    "first-private@example.com": (450, b"private-server-reply"),
                    "second-private@example.com": (550, b"private-server-reply"),
                }
            ),
            "recipient_refused",
            None,
        ),
    ],
)
async def test_delivery_failure_logs_only_category_and_numeric_smtp_code(
    monkeypatch: pytest.MonkeyPatch,
    mail_settings: SimpleNamespace,
    failure: Exception,
    category: str,
    smtp_code: int | None,
) -> None:
    attempts = 0

    def reject_connection(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise failure

    monkeypatch.setattr(smtplib, "SMTP", reject_connection)
    with capture_logs() as logs:
        with pytest.raises(email_service.EmailDeliveryFailed) as caught:
            await email_service.send_transactional_email(
                recipient="private-address@example.com",
                subject="private-subject",
                text="private-token-123456",
            )

    assert logs == [
        {
            "event": "transactional_email_delivery_failed",
            "log_level": "warning",
            "category": category,
            "smtp_code": smtp_code,
        }
    ]
    assert attempts == 1
    assert str(caught.value) == "SMTP delivery failed"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    # Do not include this test's own frame/source lines in the diagnostic traceback.
    rendered = "".join(
        traceback.TracebackException(type(caught.value), caught.value, None).format(chain=True)
    )
    assert "private-" not in rendered


@pytest.mark.parametrize(
    ("failure", "retry_safe"),
    [
        (TimeoutError("private-server-reply"), False),
        (smtplib.SMTPServerDisconnected("private-server-reply"), False),
        (smtplib.SMTPException("private-server-reply"), False),
        (smtplib.SMTPDataError(554, b"private-server-reply"), True),
        (smtplib.SMTPRecipientsRefused({"private@example.test": (550, b"reply")}), True),
    ],
)
async def test_only_definitive_rejection_is_retry_safe_after_submission_starts(
    monkeypatch: pytest.MonkeyPatch,
    mail_settings: SimpleNamespace,
    failure: Exception,
    retry_safe: bool,
) -> None:
    class SubmissionFailure(SMTPStub):
        def send_message(self, message: EmailMessage) -> None:
            raise failure

    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: SubmissionFailure())
    with pytest.raises(email_service.EmailDeliveryFailed) as caught:
        await email_service.send_transactional_email(
            recipient="recipient@example.test", subject="Code", text="Verification message"
        )
    assert caught.value.retry_safe is retry_safe


async def test_failed_quit_after_confirmed_acceptance_does_not_trigger_retry(
    monkeypatch: pytest.MonkeyPatch,
    mail_settings: SimpleNamespace,
) -> None:
    class QuitFailure(SMTPStub):
        def __exit__(self, *args: object) -> None:
            raise smtplib.SMTPServerDisconnected("private-server-reply")

    smtp = QuitFailure()
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: smtp)
    await email_service.send_transactional_email(
        recipient="recipient@example.test", subject="Code", text="Verification message"
    )
    assert len(smtp.messages) == 1

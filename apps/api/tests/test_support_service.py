"""Support promises and mail boundary tests without external delivery."""

from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from uuid import uuid4

import pytest


@pytest.mark.parametrize("raw,expected", [(None, None), ("", None), ("1", 1), ("57", 57)])
def test_optional_inbox_backfill_uid_accepts_empty_compose_value_without_enabling_history(
    raw, expected
):
    from yleum_api.core.config import Settings

    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://qa:qa@localhost/qa",
        jwt_secret="qa-support-test-secret",
        support_imap_start_uid=raw,
    )
    assert settings.support_imap_start_uid == expected


@pytest.mark.parametrize("raw", ["0", "-1", "invalid"])
def test_optional_inbox_backfill_uid_rejects_invalid_or_nonpositive_values(raw):
    from pydantic import ValidationError

    from yleum_api.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://qa:qa@localhost/qa",
            jwt_secret="qa-support-test-secret",
            support_imap_start_uid=raw,
        )


def test_optional_inbox_backfill_uid_defaults_to_future_mail_and_normalizes_only_its_env(
    monkeypatch,
):
    from pydantic import ValidationError

    from yleum_api.core.config import Settings

    monkeypatch.delenv("SUPPORT_IMAP_START_UID", raising=False)
    assert (
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://qa:qa@localhost/qa",
            jwt_secret="qa-support-test-secret",
        ).support_imap_start_uid
        is None
    )
    monkeypatch.setenv("SUPPORT_IMAP_START_UID", "")
    assert (
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://qa:qa@localhost/qa",
            jwt_secret="qa-support-test-secret",
        ).support_imap_start_uid
        is None
    )
    monkeypatch.setenv("SMTP_PORT", "")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://qa:qa@localhost/qa",
            jwt_secret="qa-support-test-secret",
        )


@pytest.mark.parametrize(
    "created,due",
    [
        ("2026-10-30T07:00:00+00:00", "2026-10-30T16:00:00+00:00"),
        ("2026-10-30T15:00:00+00:00", "2026-11-02T15:00:00+00:00"),
        ("2026-10-30T16:00:00+00:00", "2026-11-02T16:00:00+00:00"),
        ("2026-10-30T18:00:00+00:00", "2026-11-02T16:00:00+00:00"),
        ("2026-10-31T12:00:00+00:00", "2026-11-02T16:00:00+00:00"),
        ("2026-11-02T05:00:00+00:00", "2026-11-02T16:00:00+00:00"),
    ],
)
def test_first_response_counts_only_nine_working_hours(created, due):
    from yleum_api.services import support_tickets

    calculate = getattr(support_tickets, "first_response_deadline", None)
    assert callable(calculate), "First-response deadline is missing"
    assert calculate(datetime.fromisoformat(created)) == datetime.fromisoformat(due)


def test_recovery_payload_is_encrypted_and_scoped_to_one_ticket():
    from yleum_api.services import support_tickets

    seal = getattr(support_tickets, "seal_guest_delivery", None)
    assert callable(seal), "Encrypted guest delivery capability is missing"
    ticket_id, foreign = uuid4(), uuid4()
    token = "b" * 64
    payload = seal(ticket_id, token)
    assert token not in payload
    assert support_tickets.open_guest_delivery(ticket_id, payload) == token
    with pytest.raises(ValueError):
        support_tickets.open_guest_delivery(foreign, payload)


def mail(**headers):
    message = EmailMessage()
    for key, value in {
        "From": "guest@example.com",
        "To": "support@yleum.ru",
        "Message-ID": "<qa-1@example.com>",
        "Subject": "Помощь",
        **headers,
    }.items():
        message[key] = value
    message.set_content("Мне нужна помощь")
    return message.as_bytes()


def test_email_parser_only_accepts_explicit_support_recipient():
    from yleum_api.services import support_inbox

    parsed = support_inbox.parse_support_mail(mail(), "support@yleum.ru")
    assert parsed.sender == "guest@example.com"
    assert parsed.body == "Мне нужна помощь"
    assert support_inbox.parse_support_mail(mail(To="office@yleum.ru"), "support@yleum.ru") is None
    assert (
        support_inbox.parse_support_mail(
            mail(To="support@yleum.ru.evil.example"), "support@yleum.ru"
        )
        is None
    )


@pytest.mark.parametrize(
    "headers",
    [
        {"Auto-Submitted": "auto-replied"},
        {"Precedence": "bulk"},
        {"List-ID": "<list.example>"},
        {"Return-Path": "<>"},
        {"From": "support@yleum.ru"},
        {"From": "mailer-daemon@example.com"},
    ],
)
def test_inbox_prevents_auto_reply_and_bounce_loops(headers):
    from yleum_api.services import support_inbox

    assert support_inbox.parse_support_mail(mail(**headers), "support@yleum.ru") is None


def test_inbox_rejects_oversized_and_attachment_only_messages():
    from yleum_api.services import support_inbox

    assert support_inbox.parse_support_mail(b"x" * 262145, "support@yleum.ru") is None
    message = EmailMessage()
    message["From"], message["To"] = "guest@example.com", "support@yleum.ru"
    message.add_attachment(b"private", maintype="text", subtype="plain", filename="private.txt")
    assert support_inbox.parse_support_mail(message.as_bytes(), "support@yleum.ru") is None


def test_inbox_never_reads_attached_forwarded_message_as_customer_text():
    from yleum_api.services import support_inbox

    message = EmailMessage()
    message["From"], message["To"] = "guest@example.com", "support@yleum.ru"
    message.set_content("Публичный вопрос")
    forwarded = EmailMessage()
    forwarded.set_content("Секрет во вложении")
    message.add_attachment(forwarded, filename="forward.eml")
    parsed = support_inbox.parse_support_mail(message.as_bytes(), "support@yleum.ru")
    assert parsed.body == "Публичный вопрос"


def test_quoted_receipt_capabilities_never_enter_conversation_storage():
    from yleum_api.services import support_inbox

    message = BytesParser(policy=policy.default).parsebytes(mail())
    token = "c" * 64
    signature = "support." + "a" * 32 + "." + "b" * 16
    message.set_content(
        f"Спасибо\nhttps://yleum.ru/max#support={uuid4()}&token={token}\n{signature}"
    )
    parsed = support_inbox.parse_support_mail(message.as_bytes(), "support@yleum.ru")
    assert "Спасибо" in parsed.body
    assert token not in parsed.body and signature not in parsed.body


@pytest.mark.parametrize(
    "body",
    [
        "Спасибо\nhttps://yleum.ru/max#support={id}&token= \n{token}",
        "Спасибо\n> https://yleum.ru/max#support={id}&token=\n> {token}",
        "Спасибо\n> https://yleum.ru/max#support={id}&token={left} \n> {right}",
    ],
)
def test_wrapped_quoted_recovery_capability_is_removed_before_storage(body):
    from yleum_api.services import support_inbox

    token = "c" * 64
    message = BytesParser(policy=policy.default).parsebytes(mail())
    message.set_content(
        body.format(id=uuid4(), token=token, left=token[:32], right=token[32:]),
        params={"format": "flowed"},
    )
    parsed = support_inbox.parse_support_mail(message.as_bytes(), "support@yleum.ru")
    assert "Спасибо" in parsed.body
    assert token not in parsed.body
    assert token not in "".join(parsed.body.split()).replace(">", "")


def test_html_only_support_mail_has_safe_text_without_hidden_markup_or_capabilities():
    from yleum_api.services import support_inbox

    token = "c" * 64
    message = BytesParser(policy=policy.default).parsebytes(mail())
    message.set_content(
        f"<html><head><style>.x{{color:red}}</style></head><body>"
        f"<p>Помогите <b>с проектом</b></p><script>unsafe()</script>"
        f"<p>https://yleum.ru/max#support={uuid4()}&amp;token={token}</p>"
        f"<img src='https://external.example/image'></body></html>",
        subtype="html",
    )
    parsed = support_inbox.parse_support_mail(message.as_bytes(), "support@yleum.ru")
    assert parsed is not None
    assert "Помогите с проектом" in parsed.body
    assert all(
        text not in parsed.body for text in ("<", "unsafe()", ".x", "external.example", token)
    )


def test_inbox_tls_certificate_verification_precedes_login_and_bounds_fetch(monkeypatch):
    import ssl
    from types import SimpleNamespace

    from pydantic import SecretStr

    from yleum_api.services import support_inbox

    settings = SimpleNamespace(
        support_imap_host="imap.example.com",
        support_imap_port=993,
        support_imap_user="support@example.com",
        support_imap_password=SecretStr("qa"),
        support_imap_mailbox="INBOX",
        support_imap_recipient="support@yleum.ru",
        support_imap_start_uid=1,
    )
    monkeypatch.setattr(support_inbox, "get_settings", lambda: settings)
    captured = {}

    class Inbox:
        def __init__(self, host, port, *, ssl_context, timeout):
            captured["context"] = ssl_context

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, *args):
            assert captured["context"].verify_mode == ssl.CERT_REQUIRED
            assert captured["context"].check_hostname

        def select(self, mailbox, *, readonly):
            assert readonly is True
            return "OK", []

        def response(self, name):
            return name, [b"10"]

        def uid(self, command, *args):
            if command == "search":
                return "OK", [b"1 2 3"]
            captured.setdefault("fetches", []).append(args)
            if args[0] == "2":
                return "NO", []
            return "OK", [(b"header", mail())]

    monkeypatch.setattr(support_inbox.imaplib, "IMAP4_SSL", Inbox)
    fetched = support_inbox._fetch_mail({})
    assert [item[1] for item in fetched] == [1]
    assert captured["fetches"] == [
        ("1", "(BODY.PEEK[]<0.262145>)"),
        ("2", "(BODY.PEEK[]<0.262145>)"),
    ]


async def test_inbox_is_disabled_unless_recipient_and_all_transport_fields_are_explicit(
    monkeypatch,
):
    from yleum_api.core.config import get_settings
    from yleum_api.services import support_inbox

    settings = get_settings()
    for key, value in {
        "support_imap_host": "imap.example.com",
        "support_imap_user": "support@yleum.ru",
        "support_imap_password": None,
        "support_imap_mailbox": "INBOX",
        "support_imap_recipient": "support@yleum.ru",
    }.items():
        monkeypatch.setattr(settings, key, value)

    def forbidden_connection(*args, **kwargs):
        pytest.fail("Incomplete mail configuration opened an IMAP connection")

    monkeypatch.setattr(support_inbox.imaplib, "IMAP4_SSL", forbidden_connection)
    await support_inbox.import_support_inbox(None)


@pytest.mark.parametrize("backfill", [None, 57])
def test_uid_validity_reset_replays_new_epoch_instead_of_skipping_new_mail(monkeypatch, backfill):
    from types import SimpleNamespace

    from pydantic import SecretStr

    from yleum_api.services import support_inbox

    monkeypatch.setattr(
        support_inbox,
        "get_settings",
        lambda: SimpleNamespace(
            support_imap_host="imap.example.com",
            support_imap_port=993,
            support_imap_user="support@example.com",
            support_imap_password=SecretStr("qa"),
            support_imap_mailbox="INBOX",
            support_imap_recipient="support@yleum.ru",
            support_imap_start_uid=None,
        ),
    )

    class Inbox:
        validity = b"10"

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, *args):
            pass

        def select(self, *args, **kwargs):
            return "OK", []

        def response(self, name):
            return name, [self.validity if name == "UIDVALIDITY" else b"3"]

        def uid(self, command, *args):
            return ("OK", [b"1 2"]) if command == "search" else ("OK", [(b"header", mail())])

    monkeypatch.setattr(support_inbox.imaplib, "IMAP4_SSL", Inbox)
    initial = support_inbox._fetch_mail({})
    assert initial[0][1:3] == (2, None)
    configured = support_inbox.get_settings()
    configured.support_imap_start_uid = backfill
    monkeypatch.setattr(support_inbox, "get_settings", lambda: configured)
    Inbox.validity = b"11"
    reset = support_inbox._fetch_mail({initial[0][0]: 2})
    assert [item[1] for item in reset] == [1, 2]
    assert all(item[2] is not None for item in reset)


def test_support_acknowledgement_has_deadline_and_small_public_brand_asset():
    from yleum_api.services.email_templates import support_email

    content = support_email(
        reference="SUP-42",
        subject="Вопрос",
        body="Текст",
        url="https://yleum.ru/max",
        staff=False,
        acknowledgement=True,
        created_at=datetime(2026, 10, 30, 7, tzinfo=UTC),
        first_response_due_at=datetime(2026, 10, 30, 16, tzinfo=UTC),
    )
    assert "SUP-42" in content.text and "19:00 МСК" in content.text
    assert "перв" in content.text.lower() and "10:00–19:00" in content.text
    assert "cid:" not in content.html
    assert "/brand/logo-mark.svg" in content.html
    assert len(content.html.encode()) < 12000


def test_email_thread_capability_cannot_be_forged_by_subject_or_sender():
    from yleum_api.models.support import SupportTicket
    from yleum_api.services import support_inbox

    ticket = SupportTicket(id=uuid4(), guest_token_hash="a" * 64)
    reply = support_inbox.customer_thread_reference(ticket)
    assert support_inbox.valid_thread_reference(ticket, reply)
    foreign = SupportTicket(id=uuid4(), guest_token_hash="a" * 64)
    assert not support_inbox.valid_thread_reference(foreign, reply)
    assert not support_inbox.valid_thread_reference(ticket, "SUP-42")
    assert not support_inbox.valid_thread_reference(ticket, reply + ".evil")


async def test_support_sender_options_keep_smtp_identity_and_skip_inline_logo(monkeypatch):
    import smtplib
    from types import SimpleNamespace

    from pydantic import SecretStr

    from tests.test_transactional_email import SMTPStub
    from yleum_api.services import transactional_email

    smtp = SMTPStub()
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: smtp)
    monkeypatch.setattr(
        transactional_email,
        "get_settings",
        lambda: SimpleNamespace(
            smtp_host="mail.example.com",
            smtp_port=587,
            smtp_starttls=True,
            smtp_user="office@example.com",
            smtp_password=SecretStr("qa"),
            smtp_from="Yleum <office@example.com>",
        ),
    )
    await transactional_email.send_transactional_email(
        recipient="guest@example.com",
        subject="SUP-42",
        text="Помощь",
        html="<p>Yleum</p>",
        reply_to="support@yleum.ru",
        inline_logo=False,
        auto_submitted=True,
    )
    message = smtp.messages[0]
    assert message["From"] == "Yleum <office@example.com>"
    assert message["Reply-To"] == "support@yleum.ru"
    assert message["Auto-Submitted"] == "auto-generated"
    assert not list(message.iter_attachments())
    assert len(message.as_bytes()) < 5000

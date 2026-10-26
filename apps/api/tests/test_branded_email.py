from datetime import UTC, datetime
from decimal import Decimal
from email.message import EmailMessage
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from yleum_api.services import transactional_email as transport


def test_verification_has_identical_plain_and_html_code_and_safe_link():
    from yleum_api.services.email_templates import verification_email

    mail = verification_email(code="482916", link="https://yleum.ru/max/verify-email?token=a&x=b")
    assert "482916" in mail.text and "482916" in mail.html
    assert "10 минут" in mail.text and "24 часа" in mail.text
    assert "cid:yleum-logo" in mail.html
    assert "?token=a&amp;x=b" in mail.html
    assert "<script" not in mail.html and "https://fonts." not in mail.html
    assert "'Helvetica Neue',Aptos,Helvetica,Arial,sans-serif" in mail.html


def test_subscription_templates_escape_dynamic_values_and_distinguish_autopay():
    from yleum_api.services.email_templates import paid_email, reminder_email

    end = datetime(2026, 11, 26, 9, tzinfo=UTC)
    kwargs = dict(
        plan_name="<Pro & Team>",
        amount_rub=Decimal("2990"),
        period_end=end,
        manage_url="https://yleum.ru/billing/plan",
    )
    paid = paid_email(**kwargs)
    assert "&lt;Pro &amp; Team&gt;" in paid.html
    assert "<Pro & Team>" not in paid.html
    assert "2 990" in paid.text and "12:00" in paid.text and "МСК" in paid.text
    for days in (1, 3):
        manual = reminder_email(**kwargs, days=days, auto_renew=False)
        automatic = reminder_email(**kwargs, days=days, auto_renew=True)
        assert "Автопродление выключено" in manual.text
        assert "списание" in automatic.text and "2 990" in automatic.text
        assert "Автопродление выключено" not in automatic.text
        assert ("остался один день" if days == 1 else "осталось три дня") in manual.text
    with pytest.raises(ValueError):
        reminder_email(**kwargs, days=2, auto_renew=False)


async def test_transport_builds_text_html_and_inline_original_logo(monkeypatch):
    captured: list[EmailMessage] = []

    class SMTP:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, **kwargs):
            pass

        def login(self, *args):
            pass

        def send_message(self, message):
            captured.append(message)

    monkeypatch.setattr(
        transport,
        "get_settings",
        lambda: SimpleNamespace(
            smtp_host="smtp.test",
            smtp_port=587,
            smtp_starttls=True,
            smtp_user="u",
            smtp_password=SecretStr("dummy"),
            smtp_from="Yleum <noreply@yleum.ru>",
        ),
    )
    monkeypatch.setattr(transport.smtplib, "SMTP", lambda *a, **k: SMTP())
    identity = uuid4()
    await transport.send_transactional_email(
        recipient="recipient@example.test",
        subject="Проверка",
        text="Код: 482916",
        html='<img src="cid:yleum-logo">482916',
        message_id=identity,
    )
    mail = captured[0]
    assert mail["Message-ID"] == f"<{identity.hex}@yleum.ru>"
    assert mail.get_body(preferencelist=("plain",)).get_content().strip() == "Код: 482916"
    assert "482916" in mail.get_body(preferencelist=("html",)).get_content()
    images = [p for p in mail.walk() if p.get_content_type() == "image/png"]
    assert len(images) == 1 and images[0]["Content-ID"] == "<yleum-logo>"
    assert len(images[0].get_payload(decode=True)) == 625358

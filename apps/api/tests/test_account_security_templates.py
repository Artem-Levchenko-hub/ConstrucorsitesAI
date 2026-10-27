"""Account security mail content has no credential payloads."""

from yleum_api.services import email_templates


def test_reset_request_has_branded_html_and_same_single_use_link():
    render = getattr(email_templates, "password_reset_email", None)
    assert callable(render), "Branded reset template missing"
    mail = render(link="https://yleum.ru/reset-password?token=test-capability")
    assert "cid:yleum-logo" in mail.html
    assert "30 минут" in mail.text
    assert "token=test-capability" in mail.text and "token=test-capability" in mail.html


def test_email_change_confirmation_escapes_address_and_keeps_fragment_link():
    render = getattr(email_templates, "email_change_confirmation_email", None)
    assert callable(render), "Double confirmation template missing"
    mail = render(
        link="https://yleum.ru/confirm-email-change#token=test-capability",
        new_email="<untrusted@example.com>",
        current_address=True,
    )
    assert "<untrusted@example.com>" not in mail.html
    assert "&lt;untrusted@example.com&gt;" in mail.html
    assert "#token=test-capability" in mail.html
    assert "оба" in mail.text.lower()


def test_security_success_notice_has_no_change_capability():
    render = getattr(email_templates, "account_security_email", None)
    assert callable(render), "Security success template missing"
    for kind in ("password_changed", "password_reset", "email_changed"):
        mail = render(kind=kind, recovery_url="https://yleum.ru/forgot-password")
        assert "cid:yleum-logo" in mail.html
        assert "token=" not in mail.html + mail.text
        assert "https://yleum.ru/forgot-password" in mail.text


def test_payment_test_receipt_keeps_subscription_unchanged():
    from decimal import Decimal
    render = getattr(email_templates, 'payment_test_email', None)
    assert callable(render), 'Standalone payment test template missing'
    mail = render(amount_rub=Decimal('1.00'), manage_url='https://yleum.ru/billing/history')
    assert 'Проверочная оплата подтверждена' in mail.text
    assert '1 ₽' in mail.text
    assert 'Действующая подписка, её срок, автопродление и пакеты не изменились' in mail.text
    assert 'cid:yleum-logo' in mail.html
    assert 'https://yleum.ru/billing/history' in mail.text

"""Branded, dependency-free transactional messages; never render unescaped values."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape


@dataclass(frozen=True)
class EmailContent:
    subject: str
    text: str
    html: str


def money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",").removesuffix(",00") + " ₽"


def period_label(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Email period must have a timezone")
    local = value.astimezone(timezone(timedelta(hours=3)))
    months = (
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    )
    return f"{local.day} {months[local.month - 1]} {local.year}, {local:%H:%M} МСК"


def _content(
    *,
    subject: str,
    eyebrow: str,
    title: str,
    lead: str,
    panel: str,
    panel_text: str,
    button: str,
    url: str,
    note: str,
    footer: str,
) -> EmailContent:
    text = (
        f"{title}\n\n{lead}\n\n{panel_text}\n\n{button}: {url}\n\n{note}"
        f"\n\n{footer}\n\nYleum · yleum.ru"
    )
    markup = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f5f5f3;
font-family:'Helvetica Neue',Aptos,Helvetica,Arial,sans-serif;color:#191b22;font-weight:400">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">
<tr><td style="padding:28px 12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
style="max-width:560px;margin:auto">
<tr><td style="padding:0 18px 22px"><table role="presentation"><tr>
<td><img src="cid:yleum-logo" alt="" width="56" height="56" style="display:block"></td>
<td style="padding-left:10px;font-size:27px;font-weight:500;letter-spacing:-0.6px">Yleum</td>
</tr></table></td></tr>
<tr><td style="background:#fff;border:1px solid #e6e6e3;border-top:4px solid #005cff;
border-radius:16px;padding:30px 24px">
<p style="font-size:11px;letter-spacing:1px;color:#005cff;font-weight:700;margin:0 0 22px">
{escape(eyebrow)}</p>
<h1 style="font-size:29px;line-height:1.25;font-weight:400;letter-spacing:-0.6px;margin:0 0 18px">
{escape(title)}</h1>
<p style="font-size:16px;line-height:1.65;color:#555b66;margin:0 0 24px">{escape(lead)}</p>
<div style="background:#f6f8ff;border:1px solid #e5ebff;border-radius:12px;
padding:24px 16px;margin-bottom:26px">{panel}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
<td bgcolor="#005cff" style="border-radius:10px;text-align:center">
<a href="{escape(url, quote=True)}" style="display:block;padding:18px 16px;
color:#fff;text-decoration:none;font-size:15px;font-weight:700">{escape(button)} →</a>
</td></tr></table>
<p style="font-size:13px;line-height:1.65;color:#626873;margin:20px 0 0">{escape(note)}</p>
</td></tr><tr><td style="padding:24px 20px;color:#626873;font-size:12px;line-height:1.65">
{escape(footer)}<br><br>Yleum · Из идеи — в мини-приложение MAX.<br>yleum.ru
</td></tr></table></td></tr></table></body></html>'''
    return EmailContent(subject, text, markup)


def verification_email(*, code: str, link: str) -> EmailContent:
    return _content(
        subject="Подтвердите email для Yleum",
        eyebrow="ДОБРО ПОЖАЛОВАТЬ В YLEUM",
        title="Идея начинается с вас.",
        lead="Подтвердите, что эта почта принадлежит вам.",
        panel=f'<div style="font-size:12px;color:#626873">ВАШ КОД ПОДТВЕРЖДЕНИЯ</div>'
        f'<div style="font-size:36px;letter-spacing:8px;font-weight:700;color:#005cff;'
        f'white-space:nowrap;margin:14px 0">{escape(code)}</div>'
        '<div style="font-size:13px;color:#626873">Код действует 10 минут.</div>',
        panel_text=f"Код: {code}\nКод действует 10 минут.",
        button="Подтвердить почту",
        url=link,
        note="Можно ввести код на сайте или подтвердить почту по ссылке. Ссылка действует 24 часа.",
        footer="Если вы не регистрировались в Yleum, просто проигнорируйте это письмо.",
    )


def _subscription_panel(
    plan_name: str, amount_rub: Decimal, period_end: datetime
) -> tuple[str, str]:
    label, price = period_label(period_end), money(amount_rub)
    text = f"Тариф: {plan_name}\nСумма: {price}\nПериод оплачен до: {label}"
    panel = (
        f'<div style="font-size:26px;font-weight:700">Yleum {escape(plan_name)}</div>'
        f'<div style="font-size:15px;line-height:1.8;margin-top:14px">'
        f"Сумма: <strong>{escape(price)}</strong><br>"
        f"Оплачено до: <strong>{escape(label)}</strong></div>"
    )
    return panel, text


def paid_email(
    *, plan_name: str, amount_rub: Decimal, period_end: datetime, manage_url: str
) -> EmailContent:
    panel, text = _subscription_panel(plan_name, amount_rub, period_end)
    return _content(
        subject=f"Подписка Yleum {plan_name} оплачена",
        eyebrow="ОПЛАТА ПОДТВЕРЖДЕНА",
        title="Оплата прошла успешно.",
        lead=f"Оплата подписки {plan_name} подтверждена. "
        "Ниже — сумма и срок оплаченного периода.",
        panel=panel,
        panel_text=text,
        button="Управлять подпиской",
        url=manage_url,
        note="Тариф, лимиты и дату следующего платежа можно посмотреть в разделе «Биллинг».",
        footer="Это уведомление об оплате. Кассовый чек, когда он предусмотрен, "
        "приходит отдельно от платёжного сервиса.",
    )


def reminder_email(
    *,
    plan_name: str,
    amount_rub: Decimal,
    period_end: datetime,
    days: int,
    auto_renew: bool,
    manage_url: str,
) -> EmailContent:
    if days not in (1, 3):
        raise ValueError("Supported reminders are one and three days")
    label = "один день" if days == 1 else "три дня"
    panel, text = _subscription_panel(plan_name, amount_rub, period_end)
    note = (
        f"Включено автопродление: предстоит списание {money(amount_rub)}. "
        "Проверьте способ оплаты; управлять продлением можно в разделе «Биллинг»."
        if auto_renew
        else "Автопродление выключено. Чтобы сохранить возможности тарифа, "
        "оплатите следующий период в разделе «Биллинг»."
    )
    return _content(
        subject=f"До конца периода Yleum {plan_name} — {label}",
        eyebrow="ПОДПИСКА · НАПОМИНАНИЕ",
        title="Завтра — новый период." if days == 1 else "Ещё три дня на продление.",
        lead=f"До конца текущего периода подписки {plan_name} "
        f"{'остался' if days == 1 else 'осталось'} {label}. "
        "Напоминаем заранее, чтобы вы могли спокойно проверить подписку.",
        panel=panel,
        panel_text=text,
        button="Управлять подпиской",
        url=manage_url,
        note=note,
        footer="Актуальный статус подписки всегда доступен в личном кабинете.",
    )


def support_email(
    *, reference: str, subject: str, body: str, url: str, staff: bool
) -> EmailContent:
    """Render untrusted support text as escaped text inside the shared brand shell."""
    title = "Сообщение в поддержку" if staff else "Ответ поддержки Yleum"
    return _content(
        subject=f"Yleum · {reference} · {title}",
        eyebrow=f"ПОДДЕРЖКА · {reference}",
        title=title,
        lead=subject,
        panel=f'<div style="white-space:pre-wrap;overflow-wrap:anywhere">{escape(body)}</div>',
        panel_text=body,
        button="Открыть обращение" if staff else "Открыть Yleum",
        url=url,
        note="Ответьте в чате поддержки на сайте. Ответы на это письмо не обрабатываются.",
        footer="Вы получили это письмо по обращению в поддержку Yleum.",
    )


def password_reset_email(*, link: str) -> EmailContent:
    return _content(
        subject="Сброс пароля Yleum",
        eyebrow="БЕЗОПАСНОСТЬ АККАУНТА",
        title="Задайте новый пароль.",
        lead="Получен запрос на восстановление доступа к Yleum.",
        panel="<p>Одноразовая ссылка действует <strong>30 минут</strong>.</p>",
        panel_text="Одноразовая ссылка действует 30 минут.",
        button="Задать новый пароль",
        url=link,
        note="После смены пароля все прежние сессии будут завершены.",
        footer="Если вы не запрашивали восстановление, проигнорируйте это письмо. "
        "Пароль не изменится.",
    )


def email_change_confirmation_email(
    *, link: str, new_email: str, current_address: bool
) -> EmailContent:
    return _content(
        subject="Подтвердите смену email в Yleum",
        eyebrow="БЕЗОПАСНОСТЬ АККАУНТА",
        title="Подтвердите смену адреса." if current_address else "Подтвердите новый адрес.",
        lead="Получен запрос на смену email для входа в Yleum.",
        panel=f"<p>Новый адрес: <strong>{escape(new_email)}</strong></p>",
        panel_text=f"Новый адрес: {new_email}",
        button="Подтвердить смену email",
        url=link,
        note="Подтвердите оба адреса: текущий и новый. Ссылки действуют 30 минут. "
        "До двух подтверждений email для входа не изменится.",
        footer="Если вы не меняете email, не подтверждайте запрос "
        "и обратитесь в поддержку на сайте Yleum.",
    )


def account_security_email(*, kind: str, recovery_url: str) -> EmailContent:
    titles = {
        "password_changed": "Пароль изменён.",
        "password_reset": "Пароль восстановлен.",
        "email_changed": "Email для входа изменён.",
    }
    title = titles[kind]
    return _content(
        subject=f"Yleum · {title}",
        eyebrow="БЕЗОПАСНОСТЬ АККАУНТА",
        title=title,
        lead="Изменение данных аккаунта завершено.",
        panel="<p>Все прежние сессии завершены. Для входа используйте обновлённые данные.</p>",
        panel_text="Все прежние сессии завершены. Для входа используйте обновлённые данные.",
        button="Восстановить доступ",
        url=recovery_url,
        note="Если это были не вы, восстановите доступ и обратитесь в поддержку на сайте yleum.ru.",
        footer="Это уведомление об изменении безопасности аккаунта. "
        "Письмо не содержит паролей или одноразовых ссылок.",
    )


def payment_test_email(*, amount_rub: Decimal, manage_url: str) -> EmailContent:
    amount = money(amount_rub)
    unchanged = "Действующая подписка, её срок, автопродление и пакеты не изменились."
    return _content(
        subject="Проверочная оплата Yleum подтверждена",
        eyebrow="ПРОВЕРОЧНАЯ ОПЛАТА",
        title="Проверочная оплата подтверждена.",
        lead="Платёжный сервис подтвердил проверочную оплату.",
        panel=f'<p>Сумма: <strong>{escape(amount)}</strong></p>',
        panel_text=f"Сумма: {amount}",
        button="Открыть историю операций",
        url=manage_url,
        note=unchanged,
        footer="Это уведомление о проверочной оплате. Кассовый чек, когда он предусмотрен, "
        "приходит отдельно от платёжного сервиса.",
    )

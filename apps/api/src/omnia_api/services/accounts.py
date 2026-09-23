"""Создание аккаунта — одна функция для регистрации по паролю и входа через
VK ID / Яндекс ID.

Аккаунт = email (+ пароль, если он есть). Вместе с пользователем создаются
личный платёжный счёт, кошелёк со стартовым балансом, бесплатная подписка и,
если переданы, записи о принятых юридических документах. Функция только
добавляет строки и делает flush — транзакцию завершает вызывающий.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.models.account import LegalAcceptance
from omnia_api.models.billing import FREE_PLAN_ID, BillingAccount, Subscription
from omnia_api.models.user import User
from omnia_api.models.wallet import Wallet

REQUIRED_LEGAL_DOCUMENTS: tuple[str, ...] = ("terms", "privacy", "personal_data")


@dataclass(frozen=True)
class LegalConsent:
    """Факт принятия обязательных документов (terms, privacy, personal_data)
    конкретной версии; ``marketing_accepted`` — необязательная рассылка."""

    document_version: str
    ip_address: str | None
    user_agent: str | None
    marketing_accepted: bool = False


async def create_account(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str | None,
    email_verified_at: datetime | None,
    consent: LegalConsent | None,
    signup_source: str | None = None,
    referrer_project_id: UUID | None = None,
) -> User:
    settings = get_settings()
    user = User(
        email=email,
        password_hash=password_hash,
        signup_source=signup_source,
        referrer_project_id=referrer_project_id,
        email_verified_at=email_verified_at,
    )
    session.add(user)
    await session.flush()
    billing_account = BillingAccount(
        scope="personal",
        personal_user_id=user.id,
        created_by_user_id=user.id,
    )
    session.add(billing_account)
    await session.flush()
    user.wallet = Wallet(
        billing_account_id=billing_account.id,
        balance_rub=Decimal(str(settings.initial_wallet_balance_rub)),
    )
    session.add(
        Subscription(
            billing_account_id=billing_account.id,
            user_id=user.id,
            plan_id=FREE_PLAN_ID,
            status="active",
        )
    )
    if consent is not None:
        document_types = REQUIRED_LEGAL_DOCUMENTS + (
            ("marketing",) if consent.marketing_accepted else ()
        )
        for document_type in document_types:
            session.add(
                LegalAcceptance(
                    user_id=user.id,
                    document_type=document_type,
                    document_version=consent.document_version,
                    ip_address=consent.ip_address,
                    user_agent=consent.user_agent,
                )
            )
    return user


__all__ = ["REQUIRED_LEGAL_DOCUMENTS", "LegalConsent", "create_account"]

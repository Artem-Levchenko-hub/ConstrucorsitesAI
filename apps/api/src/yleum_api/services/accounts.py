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

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.account import AuthToken, LegalAcceptance
from yleum_api.models.billing import FREE_PLAN_ID, BillingAccount, Subscription
from yleum_api.models.oauth_login import UserIdentity
from yleum_api.models.user import User
from yleum_api.models.wallet import Wallet
from yleum_api.schemas.user import LegalAcceptanceRequest

REQUIRED_LEGAL_DOCUMENTS: tuple[str, ...] = ("terms", "privacy", "personal_data")


@dataclass(frozen=True)
class LegalConsent:
    """Факт принятия обязательных документов (terms, privacy, personal_data)
    конкретной версии; ``marketing_accepted`` — необязательная рассылка."""

    document_version: str
    ip_address: str | None
    user_agent: str | None
    marketing_accepted: bool = False


async def has_verified_email(session: AsyncSession, user: User) -> bool:
    """Trust explicit verification authority, never the legacy signup timestamp alone.

    Token rotation also spends unused tokens. Only the timestamp written by the
    successful verification consumer identifies its receipt. The internal admin
    role is persisted authority; email-based bootstrap admin access is insufficient.
    OAuth identities are written only by the known providers' authenticated flow.
    """
    if user.is_anon or user.email is None or user.email_verified_at is None:
        return False
    if user.role == "admin":
        return True
    receipt = await session.scalar(select(AuthToken.id).where(
        AuthToken.user_id == user.id, AuthToken.purpose == "verify_email",
        AuthToken.used_at == user.email_verified_at,
        AuthToken.used_at <= AuthToken.expires_at,
    ).limit(1))
    if receipt is not None:
        return True
    identity = await session.scalar(select(UserIdentity.id).where(
        UserIdentity.user_id == user.id, UserIdentity.provider.in_(("vk", "yandex")),
        func.length(func.trim(UserIdentity.provider_user_id)) > 0,
        func.lower(UserIdentity.email) == user.email.casefold(),
    ).limit(1))
    return identity is not None


def require_current_legal_acceptance(payload: LegalAcceptanceRequest) -> None:
    if not (payload.terms_accepted and payload.privacy_accepted and payload.personal_data_accepted):
        raise ApiError(
            "legal_acceptance_required",
            "Для Yleum нужно принять условия, политику и согласие на обработку данных",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if payload.document_version != get_settings().legal_document_version:
        raise ApiError(
            "legal_version_outdated",
            "Юридические документы обновились. Обновите страницу и подтвердите их снова",
            status.HTTP_409_CONFLICT,
        )


async def accepted_legal_documents(session: AsyncSession, user_id: UUID, version: str) -> set[str]:
    return set(await session.scalars(
        select(LegalAcceptance.document_type).where(
            LegalAcceptance.user_id == user_id, LegalAcceptance.document_version == version,
        ),
    ))


async def record_legal_acceptance(
    session: AsyncSession, user: User, consent: LegalConsent,
) -> None:
    """Append only explicitly accepted documents; serialize retries on the owner."""
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    existing = await accepted_legal_documents(session, user.id, consent.document_version)
    requested = REQUIRED_LEGAL_DOCUMENTS + (("marketing",) if consent.marketing_accepted else ())
    for document_type in requested:
        if document_type not in existing:
            session.add(LegalAcceptance(
                user_id=user.id, document_type=document_type,
                document_version=consent.document_version,
                ip_address=consent.ip_address, user_agent=consent.user_agent,
            ))


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
        await record_legal_acceptance(session, user, consent)
    return user


__all__ = ["REQUIRED_LEGAL_DOCUMENTS", "LegalConsent", "create_account"]

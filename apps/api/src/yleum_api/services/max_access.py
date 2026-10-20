from __future__ import annotations

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.user import User
from yleum_api.services.accounts import (
    REQUIRED_LEGAL_DOCUMENTS,
    accepted_legal_documents,
    has_verified_email,
)


async def max_studio_access_reason(session: AsyncSession, user: User) -> str | None:
    """Authorize new MAX projects from persisted proof, never signup product hints."""
    if user.is_anon or user.email is None:
        return "max_registration_required"
    if not await has_verified_email(session, user):
        return "email_verification_required"
    accepted = await accepted_legal_documents(
        session,
        user.id,
        get_settings().legal_document_version,
    )
    if not set(REQUIRED_LEGAL_DOCUMENTS).issubset(accepted):
        return "legal_acceptance_required"
    return None


async def require_max_studio_access(session: AsyncSession, user: User) -> None:
    """Strict admission for creating a new MAX project, regardless of signup product."""
    _raise_max_access_denied(await max_studio_access_reason(session, user))


def require_existing_max_account_access(user: User) -> None:
    """Preserve the established integration account-management boundary.

    This legacy registered/email-timestamp policy is limited to managing an
    existing account's integrations; ownership, plan and provider gates still
    apply there. It never authorizes creation of a new MAX project.
    """
    if user.is_anon or user.email is None:
        _raise_max_access_denied("max_registration_required")
    if user.email_verified_at is None:
        _raise_max_access_denied("email_verification_required")


def _raise_max_access_denied(reason: str | None) -> None:
    if reason == "max_registration_required":
        raise ApiError(
            "max_registration_required",
            "Для Yleum нужна регистрация",
            status.HTTP_403_FORBIDDEN,
        )
    if reason == "email_verification_required":
        raise ApiError(
            "email_verification_required",
            "Подтвердите email перед созданием MAX Mini App",
            status.HTTP_403_FORBIDDEN,
        )
    if reason == "legal_acceptance_required":
        raise ApiError(
            "legal_acceptance_required",
            "Примите документы Yleum перед созданием MAX Mini App",
            status.HTTP_403_FORBIDDEN,
        )

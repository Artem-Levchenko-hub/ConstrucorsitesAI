from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response, status
from sqlalchemy import select, update

from yleum_api.core.config import get_settings
from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.account import (
    AuthSession,
)
from yleum_api.models.max_integration import MaxIntegration

router = APIRouter(prefix="/api/account", tags=["account"])
legal_router = APIRouter(prefix="/api/legal", tags=["legal"])


@legal_router.get("/config")
async def legal_config() -> dict[str, object]:
    settings = get_settings()
    return {
        "operator_name": settings.legal_operator_name,
        "operator_inn": settings.legal_operator_inn,
        "operator_address": settings.legal_operator_address,
        "support_email": settings.legal_support_email,
        "document_version": settings.legal_document_version,
        "payments_enabled": bool(
            settings.yookassa_shop_id
            and settings.yookassa_secret_key
            and settings.legal_operator_name
            and settings.legal_operator_inn
        ),
    }


@router.get("/export")
async def export_account_data(
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, object]:
    """User archive exports are unavailable, including account exports."""
    raise ApiError("forbidden", "Экспорт данных недоступен.", status.HTTP_403_FORBIDDEN)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def request_account_deletion(
    response: Response,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> None:
    now = datetime.now(UTC)
    current_user.status = "deletion_pending"
    current_user.deletion_requested_at = now
    current_user.delete_after = now + timedelta(days=30)
    current_user.session_version += 1
    current_user.github_token_enc = None
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == current_user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    # Revoke operational MAX secrets immediately. Accounting documents and the
    # minimal acceptance trail are retained for their statutory periods.
    integrations = list(
        (
            await session.execute(
                select(MaxIntegration).where(MaxIntegration.owner_id == current_user.id)
            )
        ).scalars()
    )
    for integration in integrations:
        integration.bot_token_enc = "revoked"
        integration.webhook_secret_enc = "revoked"
        integration.status = "error"
        integration.last_error = "Доступ отозван при удалении аккаунта"
    await session.commit()
    settings = get_settings()
    response.delete_cookie(
        key=settings.jwt_cookie_name,
        path="/",
        domain=settings.jwt_cookie_domain,
    )

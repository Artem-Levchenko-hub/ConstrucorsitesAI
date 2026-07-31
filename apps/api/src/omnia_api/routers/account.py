from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response, status
from sqlalchemy import select, update

from omnia_api.core.config import get_settings
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.models.account import (
    AuthSession,
    LegalAcceptance,
    Payment,
)
from omnia_api.models.billing import BillingPlan, Subscription
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.models.project import Project
from omnia_api.models.wallet_charge import WalletCharge
from omnia_api.services.max_access import get_user_business

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
    business = await get_user_business(session, current_user.id)
    projects = list(
        (
            await session.execute(
                select(Project)
                .where(Project.owner_id == current_user.id)
                .order_by(Project.created_at)
            )
        ).scalars()
    )
    acceptances = list(
        (
            await session.execute(
                select(LegalAcceptance)
                .where(LegalAcceptance.user_id == current_user.id)
                .order_by(LegalAcceptance.accepted_at)
            )
        ).scalars()
    )
    payments = list(
        (
            await session.execute(
                select(Payment)
                .where(Payment.user_id == current_user.id)
                .order_by(Payment.created_at)
            )
        ).scalars()
    )
    subscriptions = list(
        (
            await session.execute(
                select(Subscription, BillingPlan)
                .join(BillingPlan, BillingPlan.id == Subscription.plan_id)
                .where(Subscription.user_id == current_user.id)
                .order_by(Subscription.created_at)
            )
        ).all()
    )
    ledger = list(
        (
            await session.execute(
                select(WalletCharge)
                .where(WalletCharge.user_id == current_user.id)
                .order_by(WalletCharge.created_at)
            )
        ).scalars()
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "account": {
            "id": str(current_user.id),
            "email": current_user.email,
            "email_verified_at": (
                current_user.email_verified_at.isoformat()
                if current_user.email_verified_at
                else None
            ),
            "created_at": current_user.created_at.isoformat(),
            "status": current_user.status,
        },
        "business": (
            {
                "id": str(business.id),
                "kind": business.kind,
                "inn": business.inn,
                "ogrn": business.ogrn,
                "legal_name": business.legal_name,
                "status": business.status,
                "verified_at": business.verified_at.isoformat() if business.verified_at else None,
            }
            if business
            else None
        ),
        "projects": [
            {
                "id": str(project.id),
                "name": project.name,
                "template": project.template,
                "created_at": project.created_at.isoformat(),
            }
            for project in projects
        ],
        "legal_acceptances": [
            {
                "document_type": item.document_type,
                "document_version": item.document_version,
                "accepted_at": item.accepted_at.isoformat(),
            }
            for item in acceptances
        ],
        "payments": [
            {
                "id": str(item.id),
                "purpose": item.purpose,
                "subscription_id": (
                    str(item.subscription_id) if item.subscription_id else None
                ),
                "package_code": item.package_code,
                "amount_rub": str(item.amount_rub),
                "status": item.status,
                "created_at": item.created_at.isoformat(),
            }
            for item in payments
        ],
        "subscriptions": [
            {
                "id": str(subscription.id),
                "status": subscription.status,
                "plan": {
                    "code": plan.code,
                    "version": plan.version,
                    "price_rub": str(plan.price_rub),
                    "billing_interval": plan.billing_interval,
                    "included_credit_rub": str(plan.included_credit_rub),
                    "entitlements": plan.entitlements,
                },
                "auto_renew": subscription.auto_renew,
                "cancel_at_period_end": subscription.cancel_at_period_end,
                "current_period_start": (
                    subscription.current_period_start.isoformat()
                    if subscription.current_period_start
                    else None
                ),
                "current_period_end": (
                    subscription.current_period_end.isoformat()
                    if subscription.current_period_end
                    else None
                ),
                "created_at": subscription.created_at.isoformat(),
            }
            for subscription, plan in subscriptions
        ],
        "wallet_ledger": [
            {
                "type": item.entry_type,
                "amount_rub": str(item.amount_rub),
                "balance_after_rub": str(item.balance_after_rub),
                "external_ref": item.external_ref,
                "subscription_id": (
                    str(item.subscription_id) if item.subscription_id else None
                ),
                "description": item.description,
                "created_at": item.created_at.isoformat(),
            }
            for item in ledger
        ],
    }


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

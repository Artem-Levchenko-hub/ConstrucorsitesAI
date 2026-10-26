"""Owner-scoped personal account records, with an explicit public field list."""
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.account import AuthSession, LegalAcceptance, Payment
from yleum_api.models.oauth_login import UserIdentity
from yleum_api.models.project import Project
from yleum_api.models.support import SupportMessage, SupportTicket
from yleum_api.models.user import User
from yleum_api.models.wallet import Wallet
from yleum_api.models.wallet_charge import WalletCharge


async def export_personal_account_data(session: AsyncSession, user: User) -> dict[str, object]:
    """Export personal records without granting source, token or backup access.

    Select columns rather than serialising ORM models: adding a secret column
    later cannot silently add it to this download. Every collection has its own
    owner predicate, including records belonging to a shared billing account.
    """
    queries: dict[str, Select[Any]] = {
        "identities": select(
            UserIdentity.provider, UserIdentity.provider_user_id, UserIdentity.email,
            UserIdentity.created_at,
        ).where(UserIdentity.user_id == user.id).order_by(UserIdentity.created_at, UserIdentity.id),
        "consents": select(
            LegalAcceptance.document_type, LegalAcceptance.document_version,
            LegalAcceptance.accepted_at, LegalAcceptance.ip_address, LegalAcceptance.user_agent,
        ).where(LegalAcceptance.user_id == user.id).order_by(
            LegalAcceptance.accepted_at, LegalAcceptance.id,
        ),
        "sessions": select(
            AuthSession.user_agent, AuthSession.ip_address, AuthSession.created_at,
            AuthSession.last_seen_at, AuthSession.expires_at, AuthSession.revoked_at,
        ).where(AuthSession.user_id == user.id).order_by(AuthSession.created_at, AuthSession.id),
        "projects": select(
            Project.id, Project.name, Project.template, Project.language,
            Project.created_at, Project.updated_at,
        ).where(Project.owner_id == user.id).order_by(Project.created_at, Project.id),
        "wallet": select(Wallet.balance_rub, Wallet.updated_at).where(Wallet.user_id == user.id),
        "payments": select(
            Payment.id, Payment.purpose, Payment.package_code, Payment.amount_rub,
            Payment.credit_rub, Payment.status, Payment.created_at, Payment.paid_at,
            Payment.cancelled_at, Payment.refunded_at,
        ).where(Payment.user_id == user.id).order_by(Payment.created_at, Payment.id),
        "transactions": select(
            WalletCharge.id, WalletCharge.entry_type, WalletCharge.amount_rub,
            WalletCharge.balance_after_rub, WalletCharge.created_at,
        ).where(WalletCharge.user_id == user.id).order_by(WalletCharge.created_at, WalletCharge.id),
        "support_tickets": select(
            SupportTicket.id, SupportTicket.number, SupportTicket.subject, SupportTicket.status,
            SupportTicket.requester_email, SupportTicket.created_at, SupportTicket.updated_at,
            SupportTicket.resolved_at, SupportTicket.source_path,
        ).where(SupportTicket.owner_id == user.id).order_by(
            SupportTicket.created_at, SupportTicket.id,
        ),
        "support_messages": select(
            SupportMessage.id, SupportMessage.ticket_id, SupportMessage.author,
            SupportMessage.body, SupportMessage.created_at,
        ).join(SupportTicket, SupportTicket.id == SupportMessage.ticket_id).where(
            SupportTicket.owner_id == user.id,
        ).order_by(SupportMessage.created_at, SupportMessage.id),
    }
    result: dict[str, object] = {
        "format_version": 1,
        "exported_at": datetime.now(UTC),
        "scope": "personal_account_records",
        "account": {
            "id": user.id, "email": user.email, "created_at": user.created_at,
            "last_login_at": user.last_login_at, "email_verified_at": user.email_verified_at,
            "phone_e164": user.phone_e164, "phone_verified_at": user.phone_verified_at,
            "status": user.status, "default_language": user.default_language,
            "github_login": user.github_login, "github_connected_at": user.github_connected_at,
            "deletion_requested_at": user.deletion_requested_at, "delete_after": user.delete_after,
        },
        "excluded": [
            "authentication_secrets", "project_source_archives", "disaster_recovery_backups",
            "generated_application_business_records",
        ],
    }
    for name, query in queries.items():
        records = await session.execute(query)
        result[name] = [dict(row) for row in records.mappings()]
    return dict(jsonable_encoder(result, custom_encoder={Decimal: str}))

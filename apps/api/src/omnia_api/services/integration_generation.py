"""Secretless, live project capability context for the code generation agent."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.app_integration import BusinessIntegration, ProjectIntegrationBinding

_METHODS = {
    "yookassa": (
        "createOmniaPayment({amount, description, return_url, idempotency_key}); "
        "getOmniaPayment(paymentId). Only payment creation and status; no "
        "refunds/subscriptions."
    ),
    "bitrix24": (
        "createOmniaLead({name, phone?, email?, comment?, source?, "
        "idempotency_key}). Creates a lead, not a deal or task."
    ),
    "amocrm": (
        "createOmniaLead({name, phone?, email?, idempotency_key}). Creates a "
        "lead/contact, not an arbitrary CRM operation."
    ),
    "moysklad": (
        "getOmniaCatalog(). Reads catalog/prices; do not claim stock "
        "synchronization or order creation."
    ),
    "iiko": (
        "getOmniaCatalog(). Reads menu only; do not claim restaurant order "
        "submission or live stop-list support."
    ),
    "aitunnel": (
        "requestOmniaAI({message, instructions?, context?}). Uses the connected "
        "server-side account."
    ),
    "yandex_metrica": (
        "getOmniaIntegrations() returns analytics_counter_id. Counter access does"
        " not prove events are installed."
    ),
}


async def generation_context(session: AsyncSession, project_id: UUID) -> str:
    providers = list(
        (
            await session.scalars(
                select(BusinessIntegration.provider)
                .join(
                    ProjectIntegrationBinding,
                    ProjectIntegrationBinding.integration_id == BusinessIntegration.id,
                )
                .where(
                    ProjectIntegrationBinding.project_id == project_id,
                    ProjectIntegrationBinding.enabled.is_(True),
                    ProjectIntegrationBinding.status == "ready",
                    BusinessIntegration.status == "active",
                )
                .order_by(BusinessIntegration.provider)
            )
        ).all()
    )
    lines = [
        (
            "CONNECTED BUSINESS INTEGRATIONS (server-verified configuration, not live"
            " operation proof):"
        )
    ]
    lines.extend(f"- {p}: {_METHODS[p]}" for p in providers if p in _METHODS)
    if not providers:
        lines.append(

                "No external business service is connected. Show setup-required state; do"
                " not fabricate external success."

        )
    lines.extend(
        [
            (
                "Use the exact exported helpers from @/lib/omnia/integration-client. Do "
                "not create provider routes or request keys in chat."
            ),
            (
                "For payment/lead submission keep one idempotency_key per user intent "
                "across retries; disable the submit button while pending. An unknown "
                "operation result requires reconciliation, not a new key."
            ),
            (
                "A payment redirect is not proof of payment: confirm status server-side "
                "and match the authoritative order amount before fulfillment. Never treat"
                " a browser-provided amount or status as an authorized order."
            ),
            (
                "If both CRM/catalog providers are connected, the runtime currently "
                "selects Bitrix24 before amoCRM and iiko before MoySklad. Do not promise "
                "selection of a different provider."
            ),
            (
                "Preserve existing working product behavior; integrate the requested "
                "feature and show actionable provider/setup errors."
            ),
        ]
    )
    return "\n".join(lines)

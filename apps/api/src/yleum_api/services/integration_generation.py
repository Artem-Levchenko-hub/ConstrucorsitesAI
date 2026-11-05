"""Secretless, live project capability context for the code generation agent."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.app_integration import AccountIntegration, ProjectIntegrationBinding
from yleum_api.models.max_staff import MaxStaffSettings
from yleum_api.models.project import Project

MANAGED_PRODUCT_GUIDE = """MANAGED BUSINESS CONFIGURATION AND PRODUCT STATE
Read the owner's saved brief in src/lib/omnia/max-config.ts. In product screens
use getYleumAppConfig() from @/lib/omnia/integration-client on mount and focus for
editable names, descriptions, actions and catalogs. Render active content with
stable ids, category, image_url, price, availability and options honestly; never
invent content or copy mutable configuration into constants. Handle loading,
empty and failed reads. Implement configured features and personal-data consent
when required; saved policy settings do not prove consent was collected.
Use createMaxAction/getMaxActions for managed user actions. Never fabricate user
history or provider statuses. A connected service's history/status contract below
supersedes a local action cache for that service. Product-owned storage remains
available under the selected provider's isolation and authenticated identity rules.
Keep MAX Bridge/session initialization and platform-owned /api/max/*, /api/omnia/*
and managed config/clients intact. Use exact SDK exports; credentials belong in
the Integration Hub, never in product code, chat instructions or .env files.
Do not turn on unrequested features merely because a service is connected.
""".strip()

_STAFF_PICKUP_GUIDE = """STAFF PICKUP ORDERS
The owner explicitly selected payment on pickup for orders sent to staff. Persist
these orders with createManagedOrder and show the saved pickup details. Keep
online payment and generic payment helpers out of the staff pickup flow.
""".strip()

_STAFF_CHECKOUT_GUIDE = """STAFF ORDERS WITH LINKED ONLINE PAYMENT
After explicit order confirmation, save it with createManagedOrder from
@/lib/omnia/business-client and retain the returned canonical ManagedRecord.id.
On explicit payment action call createYleumOrderCheckout(order.id) from
@/lib/omnia/integration-client. Disable repeated submit while the call is running.
Keep the same order ID on network errors and explicit retries. The platform reads the
saved price and tracks the attempt. Send no amount, client payment status,
provider payment ID, credentials or idempotency key to linked checkout.
On return, getReturnedYleumCheckoutOrderId() reads the validated URL lookup key.
Call getYleumOrderCheckout(orderId) after verified MAX session on mount, refresh
and visible return. If the return opens outside MAX, offer to reopen the mini app
and check the same order from its history. The redirect is not proof of payment. Pending with a
confirmation_url can continue via that URL. Pending without a URL means check
the same order again. Only succeeded with test=false and paid_at confirms real
payment. Clearly label a succeeded test payment. Canceled is terminal for this
order: show canceled and let the customer deliberately start a new order if
wanted. Never automatically place a second order. For needs_review or unknown
outcome, keep the order ID, show the actual state and offer status refresh or
support. Missing provider setup must show an actionable error and keep the order.
For failure_reason=receipt_required show “Магазину нужно настроить чеки для
онлайн-оплаты. Обратитесь в поддержку магазина.” Keep the saved order and offer
support. Never retry the same payment, request receipt data or switch payment
methods automatically in this state.
On app updates preserve prior order and payment history. Staff checkout becomes
available to buyers after the owner publishes the updated app.
""".strip()

_METHODS = {
    "yookassa": (
        "createYleumPayment({amount, description, return_url, idempotency_key}); "
        "getYleumPayment(paymentId). Only payment creation and status; no "
        "refunds/subscriptions."
    ),
    "bitrix24": (
        "createYleumLead({name, phone?, email?, comment?, source?, "
        "idempotency_key}). Creates a lead, not a deal or task."
    ),
    "amocrm": (
        "createYleumLead({name, phone?, email?, comment?, source?, idempotency_key}) "
        "creates a lead/contact in the project's configured pipeline/stage. Name maps to "
        "lead/contact name, phone/email to contact fields, comment/source to a lead note. "
        "Do not invent CRM custom-field mappings or set provider IDs in generated code. "
        "Show visible field validation (trimmed nonempty name, valid phone/email), never "
        "silently return from an invalid submit. A returned id proves creation; if "
        "details_status is unknown show warning and never resubmit that lead. "
        "getYleumLeads() returns {items, has_more}, the latest 20 current user's leads; "
        "getYleumLeadStatus(id) returns current authorized CRM status. Both are amoCRM-only. "
        "Build My requests with status_name, checked_at and refresh/error/empty states; "
        "refresh on mount, window focus and visible resume, plus an explicit refresh. "
        "These statuses are live on read, not webhook push. Never store fake CRM statuses "
        "in getMaxActions/localStorage. Do not claim more than the returned history."
    ),
    "moysklad": (
        "getYleumCatalog() reads catalog, prices and current stock; a null "
        "available_quantity means stock is unknown, not available. "
        "createYleumOrder({buyer_name, phone?, lines: [{product_id, quantity}], "
        "idempotency_key?}) submits a customer order. Do not claim fulfillment "
        "or payment merely from order creation. Use real product_id UUIDs from the managed "
        "catalog, not a hardcoded local catalog whitelist. Server preflight supplies prices. "
        "Persist one idempotency_key per deliberate intent scoped to the current actor/project "
        "before submitting; unknown/lost responses keep that key across reload. "
        "getYleumOrderStatus(idempotencyKey) reads its durable status without a provider POST; "
        "dispatching/unknown are not success, rejection, or permission to resubmit with a new key. "
        "A 404 receipt is not proof no external write occurred. On succeeded show order and "
        "begin a new intent only after an explicit new purchase. getYleumOrders() returns "
        "{items, has_more}: latest 20 own receipts; getYleumOrder(orderId) reads own details. "
        "Use submitted_snapshot.lines and total_minor (decimal strings in minor units), "
        "currency only if known. amount_source=server_submitted is the platform's submitted "
        "price snapshot, NOT payment/fulfillment or an exact provider invoice. Provider total "
        "currency may be unknown; never assume it matches the submitted currency. "
        "Legacy snapshot_status=unavailable must show unavailable details, not invented totals. "
        "New managed orders must read history from these receipts: NEVER POST a second local "
        "order after the external write. Preserve old local orders as read-only history. "
        "No portable backend signer, MAX token, credential, or forged assertion is allowed."
    ),
    "iiko": (
        "getYleumCatalog(). Reads menu only; do not claim restaurant order "
        "submission or live stop-list support."
    ),
    "llmgw": (
        "requestYleumAI({message, instructions?, context?}). Uses built-in LLMGW "
        "through the platform gateway; charged to the owner's balance. Never request "
        "an API key or choose billing identity/model in the app."
    ),
    "yandex_metrica": (
        "getYleumIntegrations() returns analytics_counter_id. Counter access does"
        " not prove events are installed."
    ),
}


async def generation_context(session: AsyncSession, project_id: UUID) -> str:
    providers = list(
        (
            await session.scalars(
                select(AccountIntegration.provider)
                .join(
                    ProjectIntegrationBinding,
                    ProjectIntegrationBinding.integration_id == AccountIntegration.id,
                )
                .where(
                    ProjectIntegrationBinding.project_id == project_id,
                    ProjectIntegrationBinding.enabled.is_(True),
                    ProjectIntegrationBinding.status == "ready",
                    AccountIntegration.status == "active",
                )
                .order_by(AccountIntegration.provider)
            )
        ).all()
    )
    providers = [p for p in providers if p != "aitunnel"]
    ai_enabled = await session.scalar(
        select(Project.runtime_ai_enabled).where(Project.id == project_id)
    )
    staff_order_mode = await session.scalar(
        select(MaxStaffSettings.orders_mode)
        .join(Project, Project.id == MaxStaffSettings.project_id)
        .where(
            Project.id == project_id,
            Project.owner_id == MaxStaffSettings.owner_id,
            MaxStaffSettings.orders_enabled_at.is_not(None),
        )
    )
    if ai_enabled:
        providers.append("llmgw")
    lines = [
        MANAGED_PRODUCT_GUIDE,
        (
            "CONNECTED BUSINESS INTEGRATIONS (server-verified configuration, not live"
            " operation proof):"
        )
    ]
    lines.extend(
        f"- {provider}: {_METHODS[provider]}"
        for provider in providers
        if provider in _METHODS
        and not (provider == "yookassa" and staff_order_mode in {"pickup", "yookassa"})
    )
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
                "For generic payment/lead/order submission keep one idempotency_key per user "
                "intent across retries; disable the submit button while pending. An unknown "
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
    if staff_order_mode == "yookassa":
        lines.append(_STAFF_CHECKOUT_GUIDE)
    elif staff_order_mode == "pickup":
        lines.append(_STAFF_PICKUP_GUIDE)
    elif "yookassa" in providers:
        lines.append(
            "When the owner explicitly asks to prepare online orders for the staff queue, "
            "read the linked checkout and status helpers in integration-client.ts and use the "
            "canonical ID returned by createManagedOrder. Preserve that ID across redirect and "
            "retry, confirm payment by server status, and keep generic payment creation out of "
            "that order flow. The owner activates the queue after publishing the adapted app."
        )
    return "\n".join(lines)

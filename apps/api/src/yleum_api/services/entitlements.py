"""Plan entitlements: read the live plan, count what the account already uses,
refuse an action that would go past the plan.

One module owns the three questions every guarded endpoint used to answer on
its own (or not at all): which plan applies to this user, how much of each
entitlement is already used, and whether one more project / publication /
integration fits. The same counts feed ``GET /api/billing/usage``, so the
report and the refusals can never disagree.

Semantics of a plan's ``entitlements`` JSON:

* numeric key ``null`` or missing  -> no limit;
* numeric key ``N``                -> at most N;
* flag key missing                 -> allowed; ``false`` -> not included.

An account without a billing account or a live subscription (technical users,
half-seeded test fixtures) gets no limits: the guards protect commercial
terms, they are not an access-control layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.app_integration import AccountIntegration
from yleum_api.models.billing import (
    ENTITLEMENT_FLAG_KEYS,
    ENTITLEMENT_LIMIT_KEYS,
    BillingAccount,
    BillingPlan,
    Subscription,
)
from yleum_api.models.billing_usage_event import BillingUsageEvent
from yleum_api.models.project import Project

log = structlog.get_logger(__name__)

LIVE_SUBSCRIPTION_STATUSES = ("trialing", "active", "past_due", "paused")

ENTITLEMENT_LABELS: dict[str, str] = {
    "max_projects": "Приложений",
    "static_publish_slots": "Опубликованных приложений",
    "always_on_slots": "Постоянно работающих приложений",
    "team_seats": "Мест в команде",
    "integrations": "Интеграции",
}


@dataclass(frozen=True)
class PlanContext:
    account: BillingAccount | None
    subscription: Subscription | None
    plan: BillingPlan | None

    @property
    def plan_name(self) -> str:
        return self.plan.name if self.plan is not None else "—"


@dataclass(frozen=True)
class EntitlementUsage:
    key: str
    kind: Literal["limit", "flag"]
    used: int
    limit: int | None = None
    enabled: bool | None = None

    @property
    def exceeded(self) -> bool:
        if self.kind == "flag":
            return self.enabled is False and self.used > 0
        return self.limit is not None and self.used > self.limit

    @property
    def label(self) -> str:
        return ENTITLEMENT_LABELS.get(self.key, self.key)


def entitlement_limit(plan: BillingPlan | None, key: str) -> int | None:
    """Numeric entitlement of the plan; ``None`` means unlimited."""
    if plan is None:
        return None
    value = plan.entitlements.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, value)


def entitlement_flag(plan: BillingPlan | None, key: str) -> bool:
    """Boolean entitlement of the plan; a missing key means allowed."""
    if plan is None:
        return True
    value = plan.entitlements.get(key)
    return True if value is None else bool(value)


async def load_plan_context(session: AsyncSession, user_id: UUID) -> PlanContext:
    account = (
        await session.execute(
            select(BillingAccount).where(BillingAccount.personal_user_id == user_id)
        )
    ).scalar_one_or_none()
    if account is None:
        return PlanContext(account=None, subscription=None, plan=None)
    row = (
        await session.execute(
            select(Subscription, BillingPlan)
            .join(BillingPlan, BillingPlan.id == Subscription.plan_id)
            .where(
                Subscription.billing_account_id == account.id,
                Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        return PlanContext(account=account, subscription=None, plan=None)
    return PlanContext(account=account, subscription=row[0], plan=row[1])


async def _count(session: AsyncSession, statement: Any) -> int:
    return int((await session.execute(statement)).scalar_one() or 0)


async def count_projects(session: AsyncSession, user_id: UUID) -> int:
    return await _count(session, select(func.count(Project.id)).where(Project.owner_id == user_id))


async def count_published_projects(
    session: AsyncSession,
    account: BillingAccount | None,
    *,
    exclude_project_id: UUID | None = None,
) -> int:
    """Projects of the account that occupy a publish slot.

    A slot is held by every existing project that was ever sent to the public
    runtime; deleting the project releases it (the journal row keeps the
    history with ``project_id = NULL``).
    """
    if account is None:
        return 0
    statement = select(func.count(func.distinct(BillingUsageEvent.project_id))).where(
        BillingUsageEvent.billing_account_id == account.id,
        BillingUsageEvent.kind == "publication",
        BillingUsageEvent.project_id.is_not(None),
    )
    if exclude_project_id is not None:
        statement = statement.where(BillingUsageEvent.project_id != exclude_project_id)
    return await _count(session, statement)


async def count_always_on_projects(session: AsyncSession, user_id: UUID) -> int:
    return await _count(
        session,
        select(func.count(Project.id)).where(
            Project.owner_id == user_id, Project.keep_alive_enabled.is_(True)
        ),
    )


async def count_integrations(session: AsyncSession, user_id: UUID) -> int:
    return await _count(
        session,
        select(func.count(AccountIntegration.id)).where(AccountIntegration.user_id == user_id),
    )


async def entitlement_usages(
    session: AsyncSession,
    user_id: UUID,
    *,
    context: PlanContext | None = None,
) -> list[EntitlementUsage]:
    """Every enforced entitlement of the plan next to what the account uses now."""
    ctx = context or await load_plan_context(session, user_id)
    counts = {
        "max_projects": await count_projects(session, user_id),
        "static_publish_slots": await count_published_projects(session, ctx.account),
        "always_on_slots": await count_always_on_projects(session, user_id),
        # Every billing account is personal since migration 0069.
        "team_seats": 1,
        "integrations": await count_integrations(session, user_id),
    }
    usages = [
        EntitlementUsage(
            key=key,
            kind="limit",
            used=counts[key],
            limit=entitlement_limit(ctx.plan, key),
        )
        for key in ENTITLEMENT_LIMIT_KEYS
    ]
    usages.extend(
        EntitlementUsage(
            key=key,
            kind="flag",
            used=counts[key],
            enabled=entitlement_flag(ctx.plan, key),
        )
        for key in ENTITLEMENT_FLAG_KEYS
    )
    return usages


def _details(ctx: PlanContext, key: str, *, limit: int | None, used: int) -> dict[str, Any]:
    return {
        "entitlement": key,
        "limit": limit,
        "used": used,
        "plan_code": ctx.plan.code if ctx.plan is not None else None,
        "plan_version": ctx.plan.version if ctx.plan is not None else None,
    }


def _refuse(ctx: PlanContext, key: str, *, limit: int | None, used: int, message: str) -> None:
    """Raise unless enforcement is switched off; then only log the would-be refusal."""
    details = _details(ctx, key, limit=limit, used=used)
    if not get_settings().enforce_plan_entitlements:
        log.warning("entitlements.advisory_exceeded", **details)
        return
    code: Literal["entitlement_exceeded", "subscription_entitlement_required"] = (
        "entitlement_exceeded" if limit is not None else "subscription_entitlement_required"
    )
    raise ApiError(code, message, 402, details=details)


async def assert_can_create_project(session: AsyncSession, user_id: UUID) -> None:
    ctx = await load_plan_context(session, user_id)
    limit = entitlement_limit(ctx.plan, "max_projects")
    if limit is None:
        return
    used = await count_projects(session, user_id)
    if used < limit:
        return
    _refuse(
        ctx,
        "max_projects",
        limit=limit,
        used=used,
        message=(
            f"На тарифе {ctx.plan_name} можно держать не больше {limit} "
            f"приложений, сейчас у вас {used}. Удалите ненужное приложение или "
            "перейдите на другой тариф."
        ),
    )


async def assert_can_publish(session: AsyncSession, project: Project) -> None:
    """One more published project must fit into the plan's publish slots.

    Re-publishing an app that already holds a slot is always allowed: the
    limit is about how many apps are live, not how often they are updated.
    """
    ctx = await load_plan_context(session, project.owner_id)
    limit = entitlement_limit(ctx.plan, "static_publish_slots")
    if limit is None:
        return
    if await _project_holds_publish_slot(session, ctx.account, project.id):
        return
    used = await count_published_projects(session, ctx.account, exclude_project_id=project.id)
    if used < limit:
        return
    message = (
        f"Тариф {ctx.plan_name} не включает публикацию приложений. "
        "Перейдите на другой тариф, чтобы опубликовать приложение."
        if limit == 0
        else (
            f"На тарифе {ctx.plan_name} можно опубликовать не больше {limit} "
            f"приложений, опубликовано уже {used}. Перейдите на другой тариф, "
            "чтобы опубликовать ещё одно."
        )
    )
    _refuse(ctx, "static_publish_slots", limit=limit, used=used, message=message)


async def _project_holds_publish_slot(
    session: AsyncSession, account: BillingAccount | None, project_id: UUID
) -> bool:
    if account is None:
        return False
    return (
        await _count(
            session,
            select(func.count(BillingUsageEvent.id)).where(
                BillingUsageEvent.billing_account_id == account.id,
                BillingUsageEvent.kind == "publication",
                BillingUsageEvent.project_id == project_id,
            ),
        )
        > 0
    )


async def assert_integrations_allowed(session: AsyncSession, user_id: UUID) -> None:
    ctx = await load_plan_context(session, user_id)
    if entitlement_flag(ctx.plan, "integrations"):
        return
    _refuse(
        ctx,
        "integrations",
        limit=None,
        used=await count_integrations(session, user_id),
        message=(
            f"Тариф {ctx.plan_name} не включает подключение внешних сервисов. "
            "Перейдите на другой тариф, чтобы подключать интеграции."
        ),
    )


async def record_publication(
    session: AsyncSession,
    project: Project,
    *,
    idempotency_key: str,
    backend: str,
    commit_sha: str | None = None,
) -> BillingUsageEvent | None:
    """Journal one accepted publication; a retry with the same key is a no-op.

    Returns ``None`` when the owner has no billing account (nothing to bill
    against) — the publication itself is not affected.
    """
    account = (
        await session.execute(
            select(BillingAccount).where(BillingAccount.personal_user_id == project.owner_id)
        )
    ).scalar_one_or_none()
    if account is None:
        log.warning("entitlements.publication_unjournaled", project_id=str(project.id))
        return None
    external_ref = f"publication:{project.id}:{idempotency_key}"
    existing = (
        await session.execute(
            select(BillingUsageEvent).where(BillingUsageEvent.external_ref == external_ref)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    event = BillingUsageEvent(
        billing_account_id=account.id,
        user_id=project.owner_id,
        project_id=project.id,
        kind="publication",
        quantity=1,
        external_ref=external_ref,
        details={
            "backend": backend,
            "slug": project.slug,
            "commit_sha": commit_sha,
        },
    )
    session.add(event)
    await session.flush()
    return event

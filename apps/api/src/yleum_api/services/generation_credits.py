"""Integer rights: immutable grants, one account lock, one hold per generation."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.account import Payment
from yleum_api.models.billing import BillingAccount
from yleum_api.models.generation_credits import (
    GenerationCreditGrant as Grant,
)
from yleum_api.models.generation_credits import (
    GenerationCreditReservation as Hold,
)
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.services.billing_accounts import resolve_billing_account
from yleum_api.services.entitlements import load_plan_context

POLICY_VERSION = "accepted-units-v2"


def allocate_units(grants: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    if kind not in {"initial", "edit"}:
        raise ValueError("Unknown generation kind")
    for grant in grants:
        if grant["bucket"] == kind and grant["available"] >= 1:
            return [{"grant_id": str(grant["id"]), "units": 1}]
    if kind == "edit":
        for grant in grants:
            if grant["bucket"] == "edit_only" and grant["available"] >= 1:
                return [{"grant_id": str(grant["id"]), "units": 1}]
    remaining = 5 if kind == "initial" else 1
    allocations = []
    for grant in grants:
        if grant["bucket"] != "flexible":
            continue
        used = min(remaining, grant["available"])
        if used:
            allocations.append({"grant_id": str(grant["id"]), "units": used})
            remaining -= used
        if not remaining:
            return allocations
    raise ValueError("Generation allowance exhausted")


async def uses_unit_billing(
    session: AsyncSession, user_id: UUID, *, run_id: UUID | None = None
) -> bool:
    from yleum_api.core.config import get_settings
    from yleum_api.services.entitlements import has_qa_unlimited_generations

    ctx = await load_plan_context(session, user_id)
    user = await session.get(User, user_id)
    if get_settings().unlimited_generations or (user and has_qa_unlimited_generations(user, ctx)):
        return False
    metered = bool(
        ctx.plan
        and (ctx.plan.code == "free" or "initial_generations_monthly" in ctx.plan.entitlements)
    )
    if metered or run_id is None:
        return metered
    # Existing RUB contracts stay visible as legacy. A newly purchased package
    # can fund a new eligible run, without suppressing an unrelated RUB balance.
    run = await session.get(GenerationRun, run_id)
    if run is None or run.user_id != user_id:
        raise ApiError("forbidden", "Generation owner differs", 403)
    account = await _lock_account(session, user_id)
    grants, _, _ = await _inventory(session, account, user_id)
    try:
        allocate_units(grants, "initial" if await _initial(session, run) else "edit")
    except ValueError:
        return False
    return True


async def _lock_account(session: AsyncSession, user_id: UUID) -> BillingAccount:
    account = await resolve_billing_account(session, user_id)
    # Serialize non-key changes without deadlocking pre-existing FK KEY SHARE locks.
    locked = await session.scalar(
        select(BillingAccount)
        .where(BillingAccount.id == account.id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )

    if locked is None:
        raise ApiError("billing_account_not_found", "Missing billing account", 409)
    return locked


async def _add_grant(session: AsyncSession, **values: Any) -> Grant:
    prior = await session.scalar(select(Grant).where(Grant.external_ref == values["external_ref"]))
    if prior is not None:
        if any(getattr(prior, key) != value for key, value in values.items()):
            raise ValueError("Immutable credit grant conflicts")
        return prior
    grant = Grant(**values)
    session.add(grant)
    await session.flush()
    return grant


async def grant_payment_units(session: AsyncSession, payment: Payment) -> None:
    account = await _lock_account(session, payment.user_id)
    if (
        payment.status != "succeeded"
        or payment.purpose != "wallet_topup"
        or payment.billing_account_id != account.id
        or payment.edit_units <= 0
        or payment.credit_rub != 0
        or payment.package_version is None
    ):
        raise ValueError("Verified unit payment required")
    await _add_grant(
        session,
        billing_account_id=account.id,
        bucket="edit_only" if payment.edits_only else "flexible",
        scope="package",
        units=payment.edit_units,
        external_ref="payment:" + str(payment.id),
        payment_id=payment.id,
    )


async def grant_subscription_units(
    session: AsyncSession, subscription: Any, payment: Payment, plan: Any
) -> None:
    account = await _lock_account(session, payment.user_id)
    if (
        payment.status != "succeeded"
        or payment.subscription_id != subscription.id
        or account.id != subscription.billing_account_id
        or payment.billing_account_id != account.id
        or subscription.plan_id != plan.id
    ):
        raise ValueError("Verified paid period required")
    start, end = subscription.current_period_start, subscription.current_period_end
    if not start or not end or end <= start:
        raise ValueError("Verified paid period missing")
    for bucket, key in (("initial", "initial_generations_monthly"), ("edit", "edits_monthly")):
        units = plan.entitlements.get(key)
        if units is None:
            continue
        if type(units) is not int or units < 0:
            raise ValueError("Invalid plan allowance")
        await _add_grant(
            session,
            billing_account_id=account.id,
            bucket=bucket,
            scope="period",
            units=units,
            external_ref="subscription:" + str(payment.id) + ":" + bucket,
            payment_id=payment.id,
            subscription_id=subscription.id,
            period_start=start,
            period_end=end,
        )


def _accepted_runs() -> Select[tuple[UUID, UUID]]:
    return (
        select(GenerationRun.project_id, GenerationRun.id)
        .outerjoin(Message, Message.id == GenerationRun.assistant_message_id)
        .outerjoin(Snapshot, Snapshot.id == Message.snapshot_id)
        .where(
            GenerationRun.status == "completed",
            GenerationRun.response_mode.in_(("build", "edit")),
            or_(
                Snapshot.prompt_text.is_not(None),
                GenerationRun.agent_state["billing_accepted_candidate"].as_string().is_not(None),
                and_(
                    GenerationRun.agent_state["snapshot_id"].as_string().is_not(None),
                    GenerationRun.agent_state["commit_sha"].as_string().is_not(None),
                ),
            ),
        )
    )


async def _free_grants(session: AsyncSession, account: Any, user_id: UUID) -> None:
    if await session.scalar(
        select(Grant.id).where(Grant.external_ref == "free:" + str(account.id) + ":initial")
    ):
        return
    user = await session.get(User, user_id)
    rows = (await session.execute(_accepted_runs().where(GenerationRun.user_id == user_id))).all()
    projects = {row[0] for row in rows}
    accepted_edit = await session.scalar(
        _accepted_runs()
        .where(GenerationRun.user_id == user_id, GenerationRun.response_mode == "edit")
        .limit(1)
    )
    for bucket, spent in (
        ("initial", bool(rows) or bool(user and user.free_generations_used)),
        ("edit", accepted_edit is not None or len(rows) > len(projects)),
    ):
        await _add_grant(
            session,
            billing_account_id=account.id,
            bucket=bucket,
            scope="free",
            units=0 if spent else 1,
            external_ref="free:" + str(account.id) + ":" + bucket,
        )


async def _inventory(
    session: AsyncSession, account: Any, user_id: UUID
) -> tuple[list[dict[str, Any]], list[Hold], Any]:
    ctx = await load_plan_context(session, user_id)
    if ctx.plan and ctx.plan.code == "free":
        await _free_grants(session, account, user_id)
    holds = list(
        (await session.scalars(select(Hold).where(Hold.billing_account_id == account.id))).all()
    )
    spent: dict[str, int] = {}
    for hold in holds:
        if hold.state in {"held", "consumed"}:
            for item in hold.allocations:
                key = str(item["grant_id"])
                spent[key] = spent.get(key, 0) + int(item["units"])
    now = datetime.now(UTC)
    grants = []
    for grant in (
        await session.scalars(
            select(Grant)
            .where(Grant.billing_account_id == account.id)
            .order_by(Grant.created_at, Grant.id)
        )
    ).all():
        if grant.scope == "free" and not (ctx.plan and ctx.plan.code == "free"):
            continue
        if grant.scope == "period" and (
            grant.period_start is None
            or grant.period_end is None
            or not ctx.subscription
            or grant.subscription_id != ctx.subscription.id
            or grant.period_start != ctx.subscription.current_period_start
            or grant.period_end != ctx.subscription.current_period_end
            or not grant.period_start <= now < grant.period_end
        ):
            continue
        if grant.payment_id:
            payment = await session.get(Payment, grant.payment_id)
            if not payment or payment.status != "succeeded":
                continue
        available = grant.units - spent.get(str(grant.id), 0)
        if available < 0:
            raise RuntimeError("Credit ledger overspent")
        grants.append(
            {
                "id": str(grant.id),
                "bucket": grant.bucket,
                "available": available,
                "scope": grant.scope,
                "limit": grant.units,
                "period_end": grant.period_end,
            }
        )
    return grants, holds, ctx


async def credits_view(session: AsyncSession, user_id: UUID) -> dict[str, Any]:
    account = await _lock_account(session, user_id)
    grants, holds, ctx = await _inventory(session, account, user_id)

    def total(bucket: str, key: str = "available") -> int:
        return sum(int(g[key]) for g in grants if g["bucket"] == bucket)

    return {
        "edit_only_units": total("edit_only"),
        "flexible_units": total("flexible"),
        "reserved_units": sum(h.units for h in holds if h.state == "held"),
        "available_units": total("edit_only") + total("flexible"),
        "initial_build_units": 5,
        "edit_units": 1,
        "included_generations_remaining": total("initial"),
        "included_edits_remaining": total("edit"),
        "included_generations_limit": 1
        if ctx.plan and ctx.plan.code == "free"
        else total("initial", "limit"),
        "included_edits_limit": 1
        if ctx.plan and ctx.plan.code == "free"
        else total("edit", "limit"),
        "period_end": ctx.subscription.current_period_end
        if ctx.subscription and ctx.plan and ctx.plan.code != "free"
        else None,
    }


async def _initial(session: AsyncSession, run: GenerationRun) -> bool:
    project = await session.get(Project, run.project_id)
    if project and project.generation_initial_accepted:
        return False
    if await session.scalar(
        select(Hold.run_id)
        .where(Hold.project_id == run.project_id, Hold.state == "consumed", Hold.kind == "initial")
        .limit(1)
    ):
        return False
    historical = (
        await session.execute(
            _accepted_runs().where(GenerationRun.project_id == run.project_id).limit(1)
        )
    ).first()
    if historical is not None and project:
        project.generation_initial_accepted = True
    return historical is None


async def reserve_generation_units(session: AsyncSession, run: GenerationRun) -> Hold:
    project = await session.get(Project, run.project_id)
    if project is None or project.owner_id != run.user_id:
        raise ApiError("forbidden", "Generation project owner differs", 403)
    account = await _lock_account(session, run.user_id)
    existing = await session.get(Hold, run.id)
    if existing:
        if (existing.user_id, existing.project_id, existing.billing_account_id) != (
            run.user_id,
            run.project_id,
            account.id,
        ) or existing.state != "held":
            raise ValueError("Generation hold is not reusable")
        return existing
    kind = "initial" if await _initial(session, run) else "edit"
    grants, _, _ = await _inventory(session, account, run.user_id)
    try:
        allocations = allocate_units(grants, kind)
    except ValueError:
        raise ApiError(
            "generation_credits_exhausted", "Доступные создания и правки закончились", 402
        ) from None
    hold = Hold(
        run_id=run.id,
        billing_account_id=account.id,
        user_id=run.user_id,
        project_id=run.project_id,
        kind=kind,
        state="held",
        units=sum(int(a["units"]) for a in allocations),
        allocations=allocations,
    )
    session.add(hold)
    await session.flush()
    return hold


async def settle_generation_units(
    session: AsyncSession, run: GenerationRun, *, accepted: bool, snapshot_id: UUID | None
) -> None:
    project = await session.get(Project, run.project_id)
    if project is None or project.owner_id != run.user_id:
        raise ApiError("forbidden", "Generation project owner differs", 403)
    account = await _lock_account(session, run.user_id)
    hold = await session.get(Hold, run.id, with_for_update=True)
    if (
        not hold
        or hold.billing_account_id != account.id
        or hold.project_id != run.project_id
        or hold.user_id != run.user_id
    ):
        raise ValueError("Generation unit hold binding changed")
    target = "consumed" if accepted else "released"
    if hold.state != "held":
        if hold.state != target or hold.accepted_snapshot_id != snapshot_id:
            raise ValueError("Terminal unit outcome conflicts")
        return
    if accepted:
        project = await session.get(Project, run.project_id)
        if project:
            project.generation_initial_accepted = True
    hold.state = target
    hold.accepted_snapshot_id = snapshot_id
    hold.settled_at = datetime.now(UTC)
    await session.flush()

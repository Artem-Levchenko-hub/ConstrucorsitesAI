"""Audited, account-serialized adjustments; callers own the transaction and commit."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.admin import is_admin_user
from yleum_api.core.errors import ApiError
from yleum_api.models.account import Payment
from yleum_api.models.admin_audit import AdminAuditEvent
from yleum_api.models.billing import BillingPlan, Subscription
from yleum_api.models.generation_credits import GenerationCreditAdjustment as Adjustment
from yleum_api.models.generation_credits import GenerationCreditReservation as Hold
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.user import User
from yleum_api.services.generation_credits import (
    CREDIT_BUCKETS,
    _inventory,
    _lock_account,
    uses_unit_billing,
)


def _reason(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 500:
        raise ApiError("validation_failed", "A reason of 1 to 500 characters is required", 422)
    return value.strip()


async def _authorize(session: AsyncSession, actor_user_id: UUID, target_user_id: UUID) -> None:
    actor = await session.get(User, actor_user_id, populate_existing=True)
    if actor is None or actor.status != "active" or actor.is_anon or not is_admin_user(actor):
        raise ApiError("forbidden", "Active administrator required", 403)
    target = await session.get(User, target_user_id, populate_existing=True)
    if target is None or target.status != "active" or target.is_anon:
        raise ApiError("account_unavailable", "Active registered target required", 409)


async def _refresh_account_snapshot(session: AsyncSession, account_id: UUID) -> None:
    """Refresh mutable identity-map rows after acquiring the account serialization lock."""
    # Preserve intentional pending changes even when the caller disabled autoflush.
    # Materialize each result: merely issuing SELECT does not refresh every ORM row.
    await session.flush()
    (
        await session.execute(
            select(Subscription, BillingPlan)
            .join(BillingPlan, BillingPlan.id == Subscription.plan_id)
            .where(Subscription.billing_account_id == account_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    for model in (Hold, Payment):
        (
            await session.scalars(
                select(model)
                .where(model.billing_account_id == account_id)
                .execution_options(populate_existing=True)
            )
        ).all()


def _result(row: Adjustment, *, replayed: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"operation_id": str(row.id), "replayed": replayed}
    if row.reverses_adjustment_id is not None:
        result["reversed_adjustment_id"] = str(row.reverses_adjustment_id)
    else:
        totals = dict.fromkeys(CREDIT_BUCKETS, 0)
        for item in row.allocations:
            totals[item["bucket"]] += item["units"]
        result["debited_by_bucket"] = totals
    return result


async def _replay(
    session: AsyncSession,
    operation_id: UUID,
    *,
    account_id: UUID,
    target_user_id: UUID,
    actor_user_id: UUID,
    reason: str,
    reverses_adjustment_id: UUID | None = None,
) -> dict[str, Any] | None:
    row = await session.get(Adjustment, operation_id, populate_existing=True)
    if row is None:
        return None
    if (
        row.billing_account_id,
        row.user_id,
        row.actor_user_id,
        row.reason,
        row.reverses_adjustment_id,
    ) != (
        account_id,
        target_user_id,
        actor_user_id,
        reason,
        reverses_adjustment_id,
    ):
        raise ApiError("conflict", "Operation ID has different bindings", 409)
    return _result(row, replayed=True)


async def _require_idle_metered(session: AsyncSession, account_id: UUID, user_id: UUID) -> None:
    if not await uses_unit_billing(session, user_id):
        raise ApiError("conflict", "Metered generation billing required", 409)
    held = await session.scalar(
        select(Hold.run_id)
        .where(Hold.billing_account_id == account_id, Hold.state == "held")
        .limit(1)
    )
    active = await session.scalar(
        select(GenerationRun.id)
        .where(
            GenerationRun.user_id == user_id,
            GenerationRun.status.not_in(("completed", "failed", "cancelled")),
        )
        .limit(1)
    )
    if held is not None or active is not None:
        raise ApiError("generation_active", "Finish active generation reservations first", 409)


def _totals(grants: list[dict[str, Any]]) -> dict[str, int]:
    totals = dict.fromkeys(CREDIT_BUCKETS, 0)
    for grant in grants:
        totals[grant["bucket"]] += grant["available"]
    return totals


async def _insert_adjustment(session: AsyncSession, **values: Any) -> Adjustment:
    # The account lock serializes ordinary retries. ON CONFLICT also handles a
    # globally reused operation UUID racing on a different billing account.
    inserted = await session.scalar(
        insert(Adjustment).values(**values).on_conflict_do_nothing().returning(Adjustment.id)
    )
    if inserted is None:
        raise ApiError("conflict", "Adjustment already exists", 409)
    row = await session.get(Adjustment, inserted)
    if row is None:
        raise RuntimeError("Inserted credit adjustment missing")
    return row


def _audit(
    session: AsyncSession,
    row: Adjustment,
    *,
    before: dict[str, int],
    after: dict[str, int],
) -> None:
    session.add(
        AdminAuditEvent(
            actor_user_id=row.actor_user_id,
            target_user_id=row.user_id,
            action="generation_credits.restore"
            if row.reverses_adjustment_id
            else "generation_credits.exhaust",
            details={
                "operation_id": str(row.id),
                "reason": row.reason,
                "before": before,
                "after": after,
                "reverses_adjustment_id": str(row.reverses_adjustment_id)
                if row.reverses_adjustment_id
                else None,
            },
        )
    )


async def exhaust_available_credits(
    session: AsyncSession,
    *,
    target_user_id: UUID,
    actor_user_id: UUID,
    operation_id: UUID,
    reason: str,
) -> dict[str, Any]:
    """Debit only rights available now; replay never captures later purchases or renewals."""
    reason = _reason(reason)
    await _authorize(session, actor_user_id, target_user_id)
    account = await _lock_account(session, target_user_id)
    await _refresh_account_snapshot(session, account.id)
    await _authorize(session, actor_user_id, target_user_id)
    replay = await _replay(
        session,
        operation_id,
        account_id=account.id,
        target_user_id=target_user_id,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    if replay is not None:
        return replay
    await _require_idle_metered(session, account.id, target_user_id)
    grants, _, _ = await _inventory(session, account, target_user_id)
    before = _totals(grants)
    row = await _insert_adjustment(
        session,
        id=operation_id,
        billing_account_id=account.id,
        user_id=target_user_id,
        actor_user_id=actor_user_id,
        reason=reason,
        reverses_adjustment_id=None,
        allocations=[
            {"grant_id": grant["id"], "bucket": grant["bucket"], "units": grant["available"]}
            for grant in grants
            if grant["available"] > 0
        ],
    )
    after = _totals((await _inventory(session, account, target_user_id))[0])
    if any(after.values()):
        raise RuntimeError("Credit adjustment did not exhaust available rights")
    _audit(session, row, before=before, after=after)
    await session.flush()
    return _result(row, replayed=False)


async def restore_exhausted_credits(
    session: AsyncSession,
    *,
    adjustment_id: UUID,
    actor_user_id: UUID,
    operation_id: UUID,
    reason: str,
) -> dict[str, Any]:
    """Undo one debit without reissuing grants or resurrecting expired entitlement periods."""
    reason = _reason(reason)
    original = await session.get(Adjustment, adjustment_id, populate_existing=True)
    if original is None or original.reverses_adjustment_id is not None:
        raise ApiError("conflict", "Original debit required", 409)
    await _authorize(session, actor_user_id, original.user_id)
    account = await _lock_account(session, original.user_id)
    await _refresh_account_snapshot(session, account.id)
    await _authorize(session, actor_user_id, original.user_id)
    if account.id != original.billing_account_id:
        raise ApiError("conflict", "Original billing account changed", 409)
    replay = await _replay(
        session,
        operation_id,
        account_id=account.id,
        target_user_id=original.user_id,
        actor_user_id=actor_user_id,
        reason=reason,
        reverses_adjustment_id=original.id,
    )
    if replay is not None:
        return replay
    if await session.scalar(
        select(Adjustment.id).where(
            Adjustment.reverses_adjustment_id == original.id,
        )
    ):
        raise ApiError("conflict", "Debit already reversed", 409)
    await _require_idle_metered(session, account.id, original.user_id)
    before = _totals((await _inventory(session, account, original.user_id))[0])
    row = await _insert_adjustment(
        session,
        id=operation_id,
        billing_account_id=account.id,
        user_id=original.user_id,
        actor_user_id=actor_user_id,
        reason=reason,
        allocations=[],
        reverses_adjustment_id=original.id,
    )
    after = _totals((await _inventory(session, account, original.user_id))[0])
    _audit(session, row, before=before, after=after)
    await session.flush()
    return _result(row, replayed=False)

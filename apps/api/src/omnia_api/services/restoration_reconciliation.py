"""AV19.1: keep restoration projections moving without a client GET.

The orchestrator owns every physical action and finishes prepare/apply/cancel on
its own schedule. This worker alone advances API projections that wait on the
controller (pending source export or status dispatch, with identity and
monotonic-revision checks). It never invents an intent; it re-sends an already
durable one when a dispatch was lost. Public GETs remain pure SQL projections.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from omnia_api.core.config import get_settings
from omnia_api.models.restoration import Restoration
from omnia_api.services.restoration_runtime import RestorationRuntime
from omnia_api.services.restorations import (
    CONTROLLER_WAIT_STATES,
    advance_restoration,
    schedule_reconcile,
)

log = structlog.get_logger("restoration_reconciliation")

# A stuck projection is worth a warning long before anyone opens the editor.
_BACKLOG_WARNING_SECONDS = 120.0


async def claim_due_restorations(
    session: AsyncSession,
    *,
    now: datetime,
    lease_seconds: int,
    limit: int,
) -> list[tuple[UUID, UUID, UUID]]:
    """Lease due operations to this worker. ``SKIP LOCKED`` lets two workers run
    side by side; an expired lease is claimable again by anyone. Only states
    that wait on the controller are observed — never ``ready``/``needs_changes``."""
    rows = (
        await session.scalars(
            select(Restoration)
            .where(
                Restoration.state.in_(tuple(CONTROLLER_WAIT_STATES)),
                Restoration.next_reconcile_at.is_not(None),
                Restoration.next_reconcile_at <= now,
                or_(
                    Restoration.reconcile_lease_until.is_(None),
                    Restoration.reconcile_lease_until < now,
                ),
            )
            .order_by(Restoration.next_reconcile_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    claimed: list[tuple[UUID, UUID, UUID]] = []
    for operation in rows:
        operation.reconcile_lease_until = now + timedelta(seconds=lease_seconds)
        operation.reconcile_attempts += 1
        claimed.append((operation.id, operation.project_id, operation.owner_id))
    await session.commit()
    return claimed


async def _observe(
    factory: async_sessionmaker[AsyncSession],
    runtime: RestorationRuntime,
    claim: tuple[UUID, UUID, UUID],
    *,
    moment: datetime,
    lease_seconds: int,
) -> None:
    operation_id, project_id, owner_id = claim
    try:
        async with factory() as session:
            # HTTP to the controller happens inside the worker-only advance
            # path after SQL locks are released. A hung controller call cannot
            # outlive the lease and stall the whole cycle.
            await asyncio.wait_for(
                advance_restoration(session, project_id, owner_id, operation_id, runtime),
                timeout=lease_seconds,
            )
    except Exception as exc:
        log.warning(
            "restoration.reconcile_failed",
            operation_id=str(operation_id),
            error_type=type(exc).__name__,
        )
    async with factory() as session:
        operation = await session.get(Restoration, operation_id)
        if operation is None:
            return
        operation.reconcile_lease_until = None
        if operation.state not in CONTROLLER_WAIT_STATES:
            # Owner-facing or terminal by now (a client may have moved it while
            # we observed): nothing to poll until someone acts.
            operation.next_reconcile_at = None
        elif operation.next_reconcile_at is None or operation.next_reconcile_at <= moment:
            # No progress recorded by this observation (stale answer, outage,
            # failure): keep observing with backoff instead of every tick.
            schedule_reconcile(operation, now=datetime.now(UTC))
        await session.commit()


async def reconcile_due_restorations(
    factory: async_sessionmaker[AsyncSession],
    runtime: RestorationRuntime,
    *,
    now: datetime | None = None,
    lease_seconds: int = 60,
    limit: int = 20,
    concurrency: int = 4,
) -> int:
    """One worker cycle. Returns how many operations were observed."""
    moment = now or datetime.now(UTC)
    async with factory() as session:
        claimed = await claim_due_restorations(
            session, now=moment, lease_seconds=lease_seconds, limit=limit
        )
    gate = asyncio.Semaphore(max(1, concurrency))

    async def guarded(claim: tuple[UUID, UUID, UUID]) -> None:
        async with gate:
            await _observe(factory, runtime, claim, moment=moment, lease_seconds=lease_seconds)

    await asyncio.gather(*(guarded(claim) for claim in claimed))
    return len(claimed)


async def oldest_pending_age_seconds(
    session: AsyncSession, *, now: datetime
) -> float | None:
    oldest = await session.scalar(
        select(func.min(Restoration.next_reconcile_at)).where(
            Restoration.state.in_(tuple(CONTROLLER_WAIT_STATES)),
            Restoration.next_reconcile_at.is_not(None),
        )
    )
    if oldest is None:
        return None
    return max(0.0, (now - oldest).total_seconds())


async def run_restoration_reconciliation_forever() -> None:
    from omnia_api.services.restoration_runtime import HttpRestorationRuntime

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    runtime = HttpRestorationRuntime()
    log.info(
        "restoration.reconciler_started",
        poll_seconds=settings.restoration_reconcile_poll_seconds,
        lease_seconds=settings.restoration_reconcile_lease_seconds,
    )
    try:
        while True:
            try:
                processed = await reconcile_due_restorations(
                    factory,
                    runtime,
                    lease_seconds=settings.restoration_reconcile_lease_seconds,
                )
                if processed:
                    log.info("restoration.reconcile_cycle", processed=processed)
                async with factory() as session:
                    age = await oldest_pending_age_seconds(session, now=datetime.now(UTC))
                if age is not None and age > _BACKLOG_WARNING_SECONDS:
                    log.warning("restoration.reconcile_backlog", oldest_pending_seconds=int(age))
            except Exception:
                log.exception("restoration.reconcile_cycle_failed")
            await asyncio.sleep(settings.restoration_reconcile_poll_seconds)
    finally:
        await engine.dispose()

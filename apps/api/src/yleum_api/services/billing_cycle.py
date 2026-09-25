"""One billing tick and the loop that repeats it.

A tick = the subscription lifecycle (renewal charges, grace period, downgrade
to Free) followed by the reconciliation of open orders (a lost webhook must not
leave a paid top-up or plan unfulfilled). The same loop runs either as a thread
of the RQ worker (`workers/run.py`) or as the standalone billing worker
(`workers/billing.py`) in the commerce cluster; `BillingCycleState` is what the
latter's `/health` reports.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yleum_api.core.config import get_settings
from yleum_api.services.payment_state import reconcile_pending_payments
from yleum_api.services.readiness import WORKER_HEARTBEAT_KEY, write_worker_heartbeat
from yleum_api.services.subscription_lifecycle import process_subscription_cycle

log = structlog.get_logger(__name__)

BILLING_HEARTBEAT_KEY = "omnia:health:billing-worker"
# A tick that failed this many times in a row (or none finished within the
# stale window) turns the worker's probe red.
_MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class BillingCycleState:
    """Observable state of the loop: when the last tick finished, how it went."""

    poll_seconds: int
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    cycles: int = 0
    processed_subscriptions: int = 0
    reconciled_payments: int = 0

    @property
    def stale_after(self) -> timedelta:
        return timedelta(seconds=max(self.poll_seconds * 3, 30))

    def begin(self, now: datetime) -> None:
        self.last_started_at = now
        self.cycles += 1

    def succeed(self, now: datetime, *, subscriptions: int, payments: int) -> None:
        self.last_completed_at = now
        self.last_error = None
        self.consecutive_failures = 0
        self.processed_subscriptions += subscriptions
        self.reconciled_payments += payments

    def fail(self, now: datetime, error: str) -> None:
        self.last_error = error[:500]
        self.consecutive_failures += 1

    def healthy(self, now: datetime) -> bool:
        if self.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            return False
        reference = self.last_completed_at or self.started_at
        return now - reference <= self.stale_after

    def snapshot(self, now: datetime) -> dict[str, object]:
        return {
            "healthy": self.healthy(now),
            "poll_seconds": self.poll_seconds,
            "started_at": self.started_at.isoformat(),
            "last_started_at": _iso(self.last_started_at),
            "last_completed_at": _iso(self.last_completed_at),
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "cycles": self.cycles,
            "processed_subscriptions": self.processed_subscriptions,
            "reconciled_payments": self.reconciled_payments,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def run_billing_tick(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """(subscriptions processed, open orders changed) for one pass."""
    current = now or datetime.now(UTC)
    subscriptions = await process_subscription_cycle(session, now=current)
    payments = await reconcile_pending_payments(session, now=current)
    return subscriptions, payments


async def run_billing_loop(
    tick: Callable[[], Awaitable[tuple[int, int]]],
    *,
    poll_seconds: int,
    state: BillingCycleState,
    heartbeat: Callable[[], Awaitable[None]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_cycles: int | None = None,
) -> None:
    """heartbeat → tick → heartbeat → sleep, forever (or `max_cycles` in tests).

    A failing tick is logged and counted, never fatal: the next pass retries.
    """
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        state.begin(datetime.now(UTC))
        try:
            await heartbeat()
            subscriptions, payments = await tick()
            await heartbeat()
        except Exception as exc:  # the loop must outlive any tick
            log.exception("billing.cycle_failed")
            state.fail(datetime.now(UTC), f"{type(exc).__name__}: {exc}")
        else:
            state.succeed(datetime.now(UTC), subscriptions=subscriptions, payments=payments)
            if subscriptions or payments:
                log.info(
                    "billing.cycle",
                    subscriptions=subscriptions,
                    reconciled_payments=payments,
                )
        await sleep(poll_seconds)


async def run_billing_cycles_forever(
    *,
    heartbeat_key: str = WORKER_HEARTBEAT_KEY,
    state: BillingCycleState | None = None,
) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    poll = settings.billing_lifecycle_poll_seconds
    heartbeat_ttl = max(poll * 3, 30)
    current_state = state or BillingCycleState(poll_seconds=poll)

    async def tick() -> tuple[int, int]:
        async with factory() as session:
            return await run_billing_tick(session)

    async def heartbeat() -> None:
        await write_worker_heartbeat(heartbeat_ttl, key=heartbeat_key)

    log.info(
        "billing.cycles_started",
        poll_seconds=poll,
        retry_hours=settings.billing_renewal_retry_hours,
        grace_days=settings.billing_grace_days,
        reconcile_after_minutes=settings.billing_payment_reconcile_after_minutes,
        heartbeat_key=heartbeat_key,
    )
    try:
        await run_billing_loop(tick, poll_seconds=poll, state=current_state, heartbeat=heartbeat)
    finally:
        await engine.dispose()


__all__ = [
    "BILLING_HEARTBEAT_KEY",
    "BillingCycleState",
    "run_billing_cycles_forever",
    "run_billing_loop",
    "run_billing_tick",
]

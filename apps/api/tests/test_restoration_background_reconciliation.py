"""AV19.1: a cancel (or any controller-side finish) reaches the API projection
without a single client GET — the worker re-observes due operations.

The RED this reproduces: cancel → controller finishes asynchronously → API row
stays ``reconciling/cancel`` and blocks the next restoration until someone
polls the operation."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.errors import ApiError
from omnia_api.models.restoration import Restoration
from omnia_api.services import restorations as service
from omnia_api.services.restoration_reconciliation import (
    oldest_pending_age_seconds,
    reconcile_due_restorations,
)
from omnia_api.services.restorations import (
    CONTROLLER_WAIT_STATES,
    reconcile_delay_seconds,
)
from tests.test_restorations import FakeRuntime, restoration_fixture


@pytest.fixture(autouse=True)
def ready_source_resources(monkeypatch):
    from types import SimpleNamespace

    from omnia_api.services import project_cell_runtime

    async def resources(workspace_id):
        return SimpleNamespace(state="resources_ready")

    monkeypatch.setattr(project_cell_runtime, "_get_cell_resources", resources)


class AsyncCancelRuntime(FakeRuntime):
    """The controller acknowledges a cancel and finishes it later, like the real one."""

    def __init__(self):
        super().__init__()
        self.finish_after_status_calls = 1
        self.status_calls = 0
        self.cancelling = False

    async def cancel(self, request):
        self.cancel_calls += 1
        self.cancelling = True
        # phase=cancelling, state still the pre-cancel one: the real controller
        # only records the intent and drives the cleanup in a background task.
        self.result = self.result.model_copy(
            update={"phase": "cancelling", "revision": 2, "can_apply": False, "can_cancel": False}
        )
        return self.result

    async def status(self, request):
        self.status_calls += 1
        if self.cancelling and self.status_calls >= self.finish_after_status_calls:
            self.result = self.result.model_copy(
                update={"state": "cancelled", "phase": "cancelled", "revision": 3}
            )
        return self.result


async def _row(session: AsyncSession, operation_id) -> Restoration:
    session.expire_all()
    row = await session.scalar(select(Restoration).where(Restoration.id == operation_id))
    assert row is not None
    return row


async def _prepared_then_cancelled(db_session, runtime):
    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    operation = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    assert operation.state == "ready"
    cancelled = await service.cancel_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )
    return owner, project, operation.id, cancelled


async def test_cancel_reaches_cancelled_in_sql_without_a_client_get(db_session, test_engine):
    runtime = AsyncCancelRuntime()
    _, _, operation_id, cancelled = await _prepared_then_cancelled(db_session, runtime)
    assert cancelled.state == "reconciling"  # the controller had not finished yet
    row = await _row(db_session, operation_id)
    assert row.next_reconcile_at is not None  # scheduled for the worker, not a client

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    later = datetime.now(UTC) + timedelta(seconds=reconcile_delay_seconds(0) + 1)
    assert await reconcile_due_restorations(factory, runtime, now=later) == 1

    row = await _row(db_session, operation_id)
    assert row.state == "cancelled"
    assert row.next_reconcile_at is None and row.reconcile_lease_until is None
    assert runtime.status_calls == 1  # the worker observed; no client GET was made
    # The next restoration is admitted again.
    await service.assert_no_active_restoration(db_session, row.project_id)


async def test_still_cancelling_controller_is_polled_again_with_backoff(db_session, test_engine):
    runtime = AsyncCancelRuntime()
    runtime.finish_after_status_calls = 3
    _, _, operation_id, _ = await _prepared_then_cancelled(db_session, runtime)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    later = datetime.now(UTC) + timedelta(seconds=60)

    assert await reconcile_due_restorations(factory, runtime, now=later) == 1
    row = await _row(db_session, operation_id)
    assert row.state == "reconciling" and row.phase == "cancel"
    assert row.reconcile_attempts == 1
    assert row.reconcile_lease_until is None
    assert row.next_reconcile_at is not None and row.next_reconcile_at > datetime.now(UTC)

    # Not due yet: nothing is claimed until the backoff elapses.
    assert await reconcile_due_restorations(factory, runtime, now=datetime.now(UTC)) == 0
    for _ in range(2):
        assert await reconcile_due_restorations(
            factory, runtime, now=datetime.now(UTC) + timedelta(seconds=120)
        ) == 1
    row = await _row(db_session, operation_id)
    assert row.state == "cancelled" and row.reconcile_attempts == 0


async def test_owner_wait_states_and_active_leases_are_not_claimed(db_session, test_engine):
    runtime = FakeRuntime()
    owner, project, _, _, _, _, request = await restoration_fixture(db_session)
    operation = await service.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    row = await _row(db_session, operation.id)
    assert row.state == "ready" and row.next_reconcile_at is None
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    far = datetime.now(UTC) + timedelta(days=1)
    assert await reconcile_due_restorations(factory, runtime, now=far) == 0

    # A row another worker holds under a live lease is skipped.
    row.state, row.phase = "reconciling", "cancel"
    row.next_reconcile_at = datetime.now(UTC) - timedelta(seconds=1)
    row.reconcile_lease_until = datetime.now(UTC) + timedelta(seconds=30)
    await db_session.commit()
    assert await reconcile_due_restorations(factory, runtime, now=datetime.now(UTC)) == 0
    assert (await oldest_pending_age_seconds(db_session, now=datetime.now(UTC))) is not None


async def test_failed_cancel_dispatch_is_finished_by_the_worker(db_session, test_engine):
    class BrokenCancelRuntime(AsyncCancelRuntime):
        async def cancel(self, request):
            if self.cancel_calls == 0:
                self.cancel_calls += 1
                raise OSError("controller unreachable")
            return await super().cancel(request)

    runtime = BrokenCancelRuntime()
    _, _, operation_id, cancelled = await _prepared_then_cancelled(db_session, runtime)
    assert cancelled.state == "reconciling"
    row = await _row(db_session, operation_id)
    assert row.next_reconcile_at is not None

    # The controller never received the intent: status still says "ready", so the
    # worker re-sends the same durable cancel instead of inventing a new operation.
    class LostThenFound(BrokenCancelRuntime):
        async def status(self, request):
            if not self.cancelling:
                raise ApiError("not_found", "operation not found", 404)
            return await super().status(request)

    runtime.__class__ = LostThenFound
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    assert await reconcile_due_restorations(
        factory, runtime, now=datetime.now(UTC) + timedelta(seconds=60)
    ) == 1
    row = await _row(db_session, operation_id)
    assert runtime.cancel_calls == 2
    assert row.state in CONTROLLER_WAIT_STATES or row.state == "cancelled"
    for _ in range(3):
        if row.state == "cancelled":
            break
        await reconcile_due_restorations(
            factory, runtime, now=datetime.now(UTC) + timedelta(seconds=120)
        )
        row = await _row(db_session, operation_id)
    assert row.state == "cancelled"


async def test_reconcile_failure_keeps_the_operation_scheduled(db_session, test_engine):
    runtime = AsyncCancelRuntime()
    _, _, operation_id, _ = await _prepared_then_cancelled(db_session, runtime)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    original = service.advance_restoration
    service.advance_restoration = explode  # type: ignore[assignment]
    try:
        from omnia_api.services import restoration_reconciliation as module

        module.advance_restoration = explode  # type: ignore[assignment]
        assert await reconcile_due_restorations(
            factory, runtime, now=datetime.now(UTC) + timedelta(seconds=60)
        ) == 1
    finally:
        service.advance_restoration = original  # type: ignore[assignment]
        module.advance_restoration = original  # type: ignore[assignment]
    row = await _row(db_session, operation_id)
    assert row.state == "reconciling"
    assert row.reconcile_lease_until is None
    assert row.next_reconcile_at is not None and row.next_reconcile_at > datetime.now(UTC)
    assert row.reconcile_attempts == 1
    assert uuid4() != operation_id


async def test_controller_outage_is_no_evidence_and_is_retried_with_backoff(
    db_session, test_engine
):
    """An offline controller must not rewrite every in-flight row with
    "not confirmed" on each poll — nothing was observed, nothing changes."""
    runtime = AsyncCancelRuntime()
    _, _, operation_id, _ = await _prepared_then_cancelled(db_session, runtime)
    before = await _row(db_session, operation_id)
    snapshot = (before.state, before.phase, before.error, before.revision)

    class Offline(AsyncCancelRuntime):
        async def status(self, request):
            self.status_calls += 1
            raise ApiError("orchestrator_unavailable", "connection refused", 503)

    runtime.__class__ = Offline
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    assert await reconcile_due_restorations(
        factory, runtime, now=datetime.now(UTC) + timedelta(seconds=60)
    ) == 1
    row = await _row(db_session, operation_id)
    assert (row.state, row.phase, row.error, row.revision) == snapshot
    assert row.reconcile_attempts == 1 and row.reconcile_lease_until is None
    assert row.next_reconcile_at is not None and row.next_reconcile_at > datetime.now(UTC)

    # A client GET during the outage leaves the row alone as well.
    seen = await service.get_restoration(
        db_session, row.project_id, row.owner_id, operation_id
    )
    row = await _row(db_session, operation_id)
    assert seen.state == snapshot[0]
    assert (row.state, row.phase, row.error, row.revision) == snapshot

    # Controller back: the next observation finishes the cancel.
    runtime.__class__ = AsyncCancelRuntime
    assert await reconcile_due_restorations(
        factory, runtime, now=datetime.now(UTC) + timedelta(seconds=120)
    ) == 1
    row = await _row(db_session, operation_id)
    assert row.state == "cancelled" and row.next_reconcile_at is None


async def test_a_hung_controller_call_is_bounded_by_the_lease(db_session, test_engine):
    runtime = AsyncCancelRuntime()
    _, _, operation_id, _ = await _prepared_then_cancelled(db_session, runtime)

    class Hung(AsyncCancelRuntime):
        async def status(self, request):
            await asyncio.sleep(3600)
            return self.result

    runtime.__class__ = Hung
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    started = time.monotonic()
    assert await reconcile_due_restorations(
        factory, runtime, now=datetime.now(UTC) + timedelta(seconds=60), lease_seconds=1
    ) == 1
    assert time.monotonic() - started < 10
    row = await _row(db_session, operation_id)
    assert row.state == "reconciling" and row.reconcile_lease_until is None
    assert row.next_reconcile_at is not None and row.next_reconcile_at > datetime.now(UTC)

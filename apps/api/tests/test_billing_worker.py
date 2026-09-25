"""The billing loop and the standalone worker's probe, without a database:
ticks are injected, time is explicit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from yleum_api.services import payment_state
from yleum_api.services.billing_cycle import BillingCycleState, run_billing_loop
from yleum_api.workers.billing import build_health_app

T0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ state


def test_fresh_state_is_healthy_only_within_the_startup_grace() -> None:
    state = BillingCycleState(poll_seconds=60, started_at=T0)
    assert state.healthy(T0 + timedelta(seconds=179)) is True
    assert state.healthy(T0 + timedelta(seconds=181)) is False


def test_completed_tick_refreshes_health_and_failures_count_down_to_red() -> None:
    state = BillingCycleState(poll_seconds=10, started_at=T0)
    state.begin(T0)
    state.succeed(T0 + timedelta(seconds=1), subscriptions=2, payments=1)
    assert state.healthy(T0 + timedelta(seconds=30)) is True
    assert state.healthy(T0 + timedelta(seconds=32)) is False  # stale: 3×poll, min 30 s

    for n in range(3):
        state.fail(T0 + timedelta(seconds=2 + n), f"boom {n}")
    assert state.consecutive_failures == 3
    assert state.healthy(T0 + timedelta(seconds=5)) is False
    state.succeed(T0 + timedelta(seconds=6), subscriptions=0, payments=0)
    assert state.consecutive_failures == 0
    assert state.healthy(T0 + timedelta(seconds=7)) is True

    snapshot = state.snapshot(T0 + timedelta(seconds=7))
    assert snapshot["cycles"] == 1
    assert snapshot["processed_subscriptions"] == 2
    assert snapshot["reconciled_payments"] == 1
    assert snapshot["last_error"] is None


# ------------------------------------------------------------------- loop


async def test_loop_heartbeats_around_every_tick_and_survives_failures() -> None:
    events: list[str] = []
    outcomes = iter([(1, 0), RuntimeError("provider down"), (0, 2)])

    async def tick() -> tuple[int, int]:
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            events.append("tick:error")
            raise outcome
        events.append("tick:ok")
        return outcome

    async def heartbeat() -> None:
        events.append("beat")

    async def sleep(seconds: float) -> None:
        events.append(f"sleep:{seconds:g}")

    state = BillingCycleState(poll_seconds=15)
    await run_billing_loop(
        tick, poll_seconds=15, state=state, heartbeat=heartbeat, sleep=sleep, max_cycles=3
    )

    assert events == [
        "beat",
        "tick:ok",
        "beat",
        "sleep:15",
        "beat",
        "tick:error",
        "sleep:15",
        "beat",
        "tick:ok",
        "beat",
        "sleep:15",
    ]
    assert state.cycles == 3
    assert state.processed_subscriptions == 1
    assert state.reconciled_payments == 2
    assert state.consecutive_failures == 0
    assert state.last_error is None


async def test_loop_records_the_last_error_while_it_keeps_failing() -> None:
    async def tick() -> tuple[int, int]:
        raise ValueError("bad state")

    async def heartbeat() -> None:
        return None

    async def sleep(_seconds: float) -> None:
        return None

    state = BillingCycleState(poll_seconds=10)
    await run_billing_loop(
        tick, poll_seconds=10, state=state, heartbeat=heartbeat, sleep=sleep, max_cycles=4
    )
    assert state.consecutive_failures == 4
    assert state.last_error == "ValueError: bad state"
    assert state.healthy(datetime.now(UTC)) is False


# ------------------------------------------------------------------ probe


async def test_health_endpoint_reports_200_then_503() -> None:
    state = BillingCycleState(poll_seconds=10, started_at=datetime.now(UTC))
    app = build_health_app(state, release_sha="abc12345")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as client:
        ok = await client.get("/health")
        assert ok.status_code == 200
        body = ok.json()
        assert body["status"] == "ok"
        assert body["service"] == "billing-worker"
        assert body["release_sha"] == "abc12345"
        assert body["healthy"] is True

        for n in range(3):
            state.fail(datetime.now(UTC), f"failure {n}")
        degraded = await client.get("/health")
        assert degraded.status_code == 503
        assert degraded.json()["status"] == "degraded"
        assert degraded.json()["consecutive_failures"] == 3
        assert degraded.json()["last_error"] == "failure 2"


# -------------------------------------------------------- reconciliation


def test_stale_payment_cutoffs_follow_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        payment_state,
        "get_settings",
        lambda: SimpleNamespace(
            billing_payment_reconcile_after_minutes=10,
            billing_payment_abandon_after_hours=24,
        ),
    )
    reread_before, abandon_before = payment_state.stale_payment_cutoffs(T0)
    assert reread_before == T0 - timedelta(minutes=10)
    assert abandon_before == T0 - timedelta(hours=24)
    assert payment_state.RECONCILED_PURPOSES == ("wallet_topup", "subscription_initial")
    assert payment_state.OPEN_PAYMENT_STATUSES == ("pending", "waiting_for_capture")

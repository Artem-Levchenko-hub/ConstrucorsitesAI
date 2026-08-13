"""Durable execution ownership and continuation policy for MAX generations.

The browser request only *accepts* work.  RQ executes it, Postgres stores the
public checkpoint and lease, Redis stores the opaque native conversation needed
to replay one ambiguous provider turn.  No hidden reasoning is written to
``generation_runs.agent_state``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.db import get_engine
from omnia_api.core.redis import get_redis
from omnia_api.models.generation_run import GenerationRun

CONTINUITY_KEY = "continuity"
ENVELOPE_KEY = "execution_envelope"
LEASE_SECONDS = 90
WATCHDOG_SECONDS = 20
MAX_EXTERNAL_OUTAGE_SECONDS = 24 * 60 * 60
NATIVE_CHECKPOINT_TTL_SECONDS = 48 * 60 * 60
ENQUEUE_RESERVATION_SECONDS = 120


def _native_checkpoint_key(run_id: UUID | str) -> str:
    return f"omnia:generation:native-checkpoint:{run_id}"


async def save_native_checkpoint(
    run_id: UUID | str, checkpoint: Mapping[str, object]
) -> None:
    """Persist the opaque agent transcript outside public run state.

    The payload is deliberately Redis-only: it is required to replay an
    ambiguous logical provider turn exactly, but must never leak model reasoning
    through the Studio run/message APIs.
    """

    await get_redis().set(
        _native_checkpoint_key(run_id),
        json.dumps(dict(checkpoint), ensure_ascii=False, separators=(",", ":")),
        ex=NATIVE_CHECKPOINT_TTL_SECONDS,
    )


async def load_native_checkpoint(run_id: UUID | str) -> dict[str, object] | None:
    raw = await get_redis().get(_native_checkpoint_key(run_id))
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


async def clear_native_checkpoint(run_id: UUID | str) -> None:
    await get_redis().delete(_native_checkpoint_key(run_id))


class GenerationContinuationRequired(RuntimeError):
    """Yield a bounded execution slice without finalising the accepted run."""

    def __init__(self, reason: str, *, delay_seconds: int | None = None) -> None:
        self.reason = reason or "internal_recovery"
        self.delay_seconds = delay_seconds
        super().__init__(self.reason)


@dataclass(frozen=True)
class ContinuationDecision:
    continue_run: bool
    classification: str
    delay_seconds: int
    action: str


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _object_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _object_int(value: object, default: int = 0) -> int:
    if isinstance(value, (int, str)):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def classify_stop(
    stop_reason: str,
    *,
    attempt: int,
    started_at: datetime | None,
    now: datetime | None = None,
) -> ContinuationDecision:
    """Classify product/internal debt as recoverable and true external blocks only.

    A time slice or repeated internal compile/runtime mismatch never becomes a
    user-facing failure.  Permanent provider rejection and a provider outage
    beyond the durable policy are external blockers with an exact next action.
    """

    current = now or datetime.now(UTC)
    reason = (stop_reason or "internal_recovery").casefold()
    elapsed = (current - started_at).total_seconds() if started_at else 0
    permanent_provider = reason.startswith(("provider_rejected", "spend_budget"))
    provider_outage = reason.startswith(
        ("provider_stopped", "provider_response_timeout", "paid_call_ambiguous")
    )
    if permanent_provider:
        return ContinuationDecision(
            False,
            "external_provider_access",
            0,
            "Проверьте доступ и обязательные настройки AI-провайдера; затем нажмите повтор.",
        )
    if provider_outage and elapsed >= MAX_EXTERNAL_OUTAGE_SECONDS:
        return ContinuationDecision(
            False,
            "external_provider_outage",
            0,
            "Провайдер недоступен дольше допустимого окна; повтор будет безопасен "
            "после восстановления.",
        )
    # Repeated internal debt switches to slower environment rediscovery rather
    # than becoming terminal. This bounds pressure without abandoning the run.
    delay = min(300, 5 * (2 ** min(max(attempt, 0), 6)))
    classification = (
        "provider_replay"
        if provider_outage
        else "environment_rediscovery"
        if attempt >= 3
        else "internal_repair"
    )
    return ContinuationDecision(
        True,
        classification,
        delay,
        "Продолжить тот же run с checkpoint и повторной проверкой среды.",
    )


def initial_continuity(envelope: Mapping[str, object]) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "version": 1,
        "status": "queued",
        "attempt": 0,
        "provider_epoch": 0,
        "created_at": now,
        "last_heartbeat_at": None,
        "lease_owner": None,
        "lease_expires_at": None,
        "enqueue_token": None,
        "enqueue_expires_at": None,
        "last_stop_reason": None,
        "classification": "accepted",
        "retryable": True,
        "action": "Запуск принят и поставлен в надёжную очередь.",
        "envelope_version": _object_int(envelope.get("version"), 1),
    }


async def store_execution_envelope(
    run_id: UUID,
    envelope: Mapping[str, object],
    *,
    session: AsyncSession | None = None,
) -> None:
    async def _store(db: AsyncSession) -> None:
        run = await db.get(GenerationRun, run_id, with_for_update=True)
        if run is None:
            return
        state = dict(run.agent_state or {})
        state[ENVELOPE_KEY] = dict(envelope)
        state.setdefault(CONTINUITY_KEY, initial_continuity(envelope))
        run.agent_state = state
        await db.commit()

    if session is not None:
        await _store(session)
        return
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as own:
        await _store(own)


async def claim_run(
    run_id: UUID,
    owner: str,
    enqueue_token: str,
) -> dict[str, object] | None:
    """Acquire one expired/free lease and return its immutable execution envelope."""

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id, with_for_update=True)
        if run is None or run.status not in {"pending", "running"}:
            return None
        state = dict(run.agent_state or {})
        envelope = state.get(ENVELOPE_KEY)
        continuity = _object_dict(state.get(CONTINUITY_KEY))
        if not isinstance(envelope, dict):
            return None
        # Jobs from an older watchdog tick/deploy stay harmless in the backlog.
        # Only the current Postgres reservation may acquire the execution lease.
        if not enqueue_token or continuity.get("enqueue_token") != enqueue_token:
            return None
        now = datetime.now(UTC)
        lease_owner = str(continuity.get("lease_owner") or "")
        lease_expires = _parse_time(continuity.get("lease_expires_at"))
        if lease_owner and lease_owner != owner and lease_expires and lease_expires > now:
            return None
        continuity.update(
            {
                "status": "running",
                "lease_owner": owner,
                "lease_expires_at": (now + timedelta(seconds=LEASE_SECONDS)).isoformat(),
                "last_heartbeat_at": now.isoformat(),
                "enqueue_token": None,
                "enqueue_expires_at": None,
            }
        )
        state[CONTINUITY_KEY] = continuity
        run.agent_state = state
        run.status = "running"
        run.started_at = run.started_at or now
        await session.commit()
        return dict(envelope)


async def heartbeat(run_id: UUID, owner: str) -> bool:
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id, with_for_update=True)
        if run is None or run.status != "running":
            return False
        state = dict(run.agent_state or {})
        continuity = _object_dict(state.get(CONTINUITY_KEY))
        if continuity.get("lease_owner") != owner:
            return False
        now = datetime.now(UTC)
        continuity["last_heartbeat_at"] = now.isoformat()
        continuity["lease_expires_at"] = (
            now + timedelta(seconds=LEASE_SECONDS)
        ).isoformat()
        state[CONTINUITY_KEY] = continuity
        run.agent_state = state
        await session.commit()
        return True


async def heartbeat_forever(run_id: UUID, owner: str) -> None:
    while await heartbeat(run_id, owner):  # noqa: ASYNC110 - cancellation owns this loop
        await asyncio.sleep(max(5, LEASE_SECONDS // 3))


async def schedule_continuation(run_id: UUID, reason: str) -> ContinuationDecision:
    """Release the lease, advance the slice and keep the same run active."""

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id, with_for_update=True)
        if run is None:
            return ContinuationDecision(False, "missing_run", 0, "Run отсутствует.")
        state = dict(run.agent_state or {})
        continuity = _object_dict(state.get(CONTINUITY_KEY))
        attempt = _object_int(continuity.get("attempt"))
        decision = classify_stop(
            reason,
            attempt=attempt,
            started_at=run.started_at or run.created_at,
        )
        continuity.update(
            {
                "attempt": attempt + 1,
                "status": "queued" if decision.continue_run else "blocked_external",
                "last_stop_reason": reason[:200],
                "classification": decision.classification,
                "retryable": decision.continue_run,
                "action": decision.action,
                "lease_owner": None,
                "lease_expires_at": None,
                "enqueue_token": None,
                "enqueue_expires_at": None,
            }
        )
        # New slice gets new provider turn ids. An ambiguous/timeout replay keeps
        # the same epoch so the gateway can return the settled logical turn.
        if decision.classification != "provider_replay":
            continuity["provider_epoch"] = _object_int(continuity.get("provider_epoch")) + 1
        state[CONTINUITY_KEY] = continuity
        run.agent_state = state
        if decision.continue_run:
            run.status = "pending"
            run.error = None
        else:
            run.status = "failed"
            run.error = decision.classification
            run.finished_at = datetime.now(UTC)
        await session.commit()
        return decision


async def release_lease(run_id: UUID, owner: str) -> None:
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id, with_for_update=True)
        if run is None:
            return
        state = dict(run.agent_state or {})
        continuity = _object_dict(state.get(CONTINUITY_KEY))
        if continuity.get("lease_owner") != owner:
            return
        continuity["lease_owner"] = None
        continuity["lease_expires_at"] = None
        state[CONTINUITY_KEY] = continuity
        run.agent_state = state
        await session.commit()


async def reclaimable_run_ids() -> tuple[UUID, ...]:
    """Pending runs and running runs with an expired lease are watchdog work."""

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(GenerationRun).where(GenerationRun.status.in_(("pending", "running")))
                )
            )
            .scalars()
            .all()
        )
    now = datetime.now(UTC)
    result: list[UUID] = []
    for run in rows:
        state = run.agent_state or {}
        if not isinstance(state.get(ENVELOPE_KEY), dict):
            continue
        continuity = state.get(CONTINUITY_KEY)
        if not isinstance(continuity, dict):
            result.append(run.id)
            continue
        enqueue_expires = _parse_time(continuity.get("enqueue_expires_at"))
        enqueue_token = str(continuity.get("enqueue_token") or "")
        if enqueue_token and enqueue_expires is not None and enqueue_expires > now:
            continue
        if run.status == "pending":
            result.append(run.id)
            continue
        expires = _parse_time(continuity.get("lease_expires_at"))
        if expires is None or expires <= now:
            result.append(run.id)
    return tuple(result)


async def reserve_enqueue(run_id: UUID, *, delay_seconds: int = 0) -> str | None:
    """Atomically reserve exactly one queue generation for a reclaimable run."""

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id, with_for_update=True)
        if run is None or run.status not in {"pending", "running"}:
            return None
        state = dict(run.agent_state or {})
        if not isinstance(state.get(ENVELOPE_KEY), dict):
            return None
        continuity = _object_dict(state.get(CONTINUITY_KEY))
        now = datetime.now(UTC)
        lease_expires = _parse_time(continuity.get("lease_expires_at"))
        lease_owner = str(continuity.get("lease_owner") or "")
        if run.status == "running" and lease_owner and lease_expires and lease_expires > now:
            return None
        enqueue_expires = _parse_time(continuity.get("enqueue_expires_at"))
        if continuity.get("enqueue_token") and enqueue_expires and enqueue_expires > now:
            return None
        token = uuid4().hex
        # A delayed RQ job must keep owning its reservation until it can start.
        # Otherwise the watchdog would replace it after 120s and defeat the
        # continuation backoff. The extra reservation window covers scheduler
        # latency and a worker restart around the due time.
        reservation_seconds = max(
            ENQUEUE_RESERVATION_SECONDS,
            max(0, int(delay_seconds)) + ENQUEUE_RESERVATION_SECONDS,
        )
        continuity.update(
            {
                "status": "queued",
                "enqueue_token": token,
                "enqueue_expires_at": (
                    now + timedelta(seconds=reservation_seconds)
                ).isoformat(),
                "lease_owner": None,
                "lease_expires_at": None,
            }
        )
        state[CONTINUITY_KEY] = continuity
        run.agent_state = state
        run.status = "pending"
        await session.commit()
        return token


async def clear_enqueue_reservation(run_id: UUID, enqueue_token: str) -> None:
    """Clear only the failed/lost enqueue generation, preserving newer work."""

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id, with_for_update=True)
        if run is None:
            return
        state = dict(run.agent_state or {})
        continuity = _object_dict(state.get(CONTINUITY_KEY))
        if continuity.get("enqueue_token") != enqueue_token:
            return
        continuity["enqueue_token"] = None
        continuity["enqueue_expires_at"] = None
        state[CONTINUITY_KEY] = continuity
        run.agent_state = state
        await session.commit()


async def enqueue_run_durably(run_id: UUID, *, delay_seconds: int = 0) -> bool:
    """Reserve in Postgres, enqueue once, and recover a lost enqueue failure."""

    from omnia_api.services.queue import enqueue_generation_run

    token = await reserve_enqueue(run_id, delay_seconds=delay_seconds)
    if token is None:
        return False
    try:
        await asyncio.to_thread(
            enqueue_generation_run,
            run_id,
            token,
            delay_seconds=delay_seconds,
        )
    except Exception:
        await clear_enqueue_reservation(run_id, token)
        raise
    return True


async def run_watchdog_forever() -> None:
    while True:
        try:
            for run_id in await reclaimable_run_ids():
                await enqueue_run_durably(run_id)
        except Exception:
            # Readiness and logs expose queue outages; the next pass retries.
            pass
        await asyncio.sleep(WATCHDOG_SECONDS)


__all__ = [
    "CONTINUITY_KEY",
    "ENVELOPE_KEY",
    "GenerationContinuationRequired",
    "claim_run",
    "classify_stop",
    "clear_enqueue_reservation",
    "clear_native_checkpoint",
    "enqueue_run_durably",
    "heartbeat_forever",
    "initial_continuity",
    "load_native_checkpoint",
    "reclaimable_run_ids",
    "release_lease",
    "reserve_enqueue",
    "run_watchdog_forever",
    "save_native_checkpoint",
    "schedule_continuation",
    "store_execution_envelope",
]

"""New retry slots must not refill a cold V3 call's original SQL arithmetic."""

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.services import llm_client
from yleum_api.services.generation import provider_attempts as attempts
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation import recovery_reconciliation as rec
from yleum_api.services.generation.recovery_contract import RecoveryStateV3, decode_recovery

from .test_generation_recovery_execution import _provider_context


@asynccontextmanager
async def owned_session(test_engine, run_id):
    """Use the same real owner lock required by production effect writers."""
    async with test_engine.connect() as owner:
        await owner.execute(text("SELECT pg_advisory_lock(1195724366,:key)"), {
            "key": int.from_bytes(run_id.bytes[-4:], "big", signed=True),
        })
        await owner.commit()
        token = execution._SESSION_OWNER.set(execution.SessionOwnership(run_id, owner))
        try:
            yield
        finally:
            execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


async def v3_provider_context(db_session, test_engine, monkeypatch):
    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    state = deepcopy(run.agent_state["recovery_v1"])
    state.update(version=3, clock={
        "window_ms": 30000,
        "segment_deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        "interval_id": str(uuid4()),
    })
    assert isinstance(decode_recovery(state), RecoveryStateV3)
    run.agent_state = {**run.agent_state, "recovery_v1": state}
    await db_session.commit()
    return run, replace(cursor, outer_allowance=5), body


async def record_original(cursor, body, *, kind, allowance, used):
    route = "/v1/messages" if kind == "native" else "/v1/chat/completions"
    with rec.provider_effect_scope(cursor):
        rec.begin_provider_dispatch(kind, allowance=allowance, seconds=30,
                                    preconnect_allowance=allowance)
        identity = await attempts.prepare_generation_attempt(body, route)
        assert identity is not None
        for index in range(used):
            wire = await attempts.reserve_generation_wire(identity, wire_attempt=index)
            await attempts.confirm_generation_preconnect(wire, identity)
    return identity, route


async def effect_count(test_engine, run_id):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        return len(list((await session.scalars(select(GenerationRecoveryEffect).where(
            GenerationRecoveryEffect.run_id == run_id,
        ))).all()))


async def recorded_wires(test_engine, run_id):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        rows = list((await session.scalars(select(GenerationRecoveryEffect).where(
            GenerationRecoveryEffect.run_id == run_id,
            GenerationRecoveryEffect.kind == "progress",
        ).order_by(GenerationRecoveryEffect.effect_ordinal))).all())
        return [row.pre_binding["dispatch_v1"] for row in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,current_cap,saved_cap", [
    ("chat", 2, 1), ("chat", 3, 2), ("sse", 3, 2),
    ("chat", 4, 1), ("chat", 4, 2), ("chat", 5, 2), ("chat", 5, 3),
    ("sse", 4, 2), ("sse", 5, 2), ("sse", 5, 3),
])
async def test_cold_real_history_preserves_pre_upgrade_cap_clock_and_used_wires(
    db_session, test_engine, monkeypatch, kind, current_cap, saved_cap,
):
    run, cursor, body = await v3_provider_context(db_session, test_engine, monkeypatch)
    frozen_clock = deepcopy(run.agent_state["recovery_v1"]["clock"])
    # Exhaust one old slot for saved_cap=1; otherwise retain exactly one slot.
    used = max(1, saved_cap - 1)
    async with owned_session(test_engine, run.id):
        identity, route = await record_original(
            cursor, body, kind=kind, allowance=saved_cap, used=used,
        )
        original = cursor.dispatch.plan
        # Live reservation consumption lives in SQL. wire_used is populated by
        # the cold history validator, not incremented as a second authority.
        wires = await recorded_wires(test_engine, run.id)
        assert [wire["wire_attempt"] for wire in wires] == list(range(used))
        assert all(wire["wire_allowance"] == saved_cap for wire in wires)
        assert all(wire["deadline"] == original.model_dump(mode="json")["deadline"]
                   for wire in wires)
        before_count = await effect_count(test_engine, run.id)
        assert before_count == used + 1  # One provider effect and each consumed wire.
        cold = replace(cursor, reconcile_only=True, dispatch=rec._DispatchState())
        with rec.provider_effect_scope(cold):
            rec.begin_provider_dispatch(kind, allowance=current_cap, seconds=300,
                                        preconnect_allowance=saved_cap)
            assert cold.dispatch.plan.deadline > original.deadline
            # This real SQL prepare enters _latest_provider_identity, validates
            # provider AND wire history, and then restores the persisted plan.
            assert await attempts.prepare_generation_attempt(body, route) == identity
            assert cold.dispatch.plan == original
            assert cold.dispatch.history_plan == original
            assert rec.provider_dispatch_wire_allowance() == saved_cap
            assert cold.dispatch.wire_used == used
            assert 0 < rec.provider_dispatch_remaining() <= 30
            if used == saved_cap:
                with pytest.raises(llm_client.LLMError) as exhausted:
                    await attempts.resume_generation_wire(identity, allow_provider_refusal=False)
                assert exhausted.value.code == "preconnect"
                assert cold.dispatch.resume_authorized is False
            else:
                assert await attempts.resume_generation_wire(
                    identity, allow_provider_refusal=False,
                ) == (used, False)
                assert cold.dispatch.wire_used == used
                assert cold.dispatch.plan == original
        assert await effect_count(test_engine, run.id) == before_count
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        persisted = await session.get(GenerationRun, run.id)
        assert persisted.agent_state["recovery_v1"]["clock"] == frozen_clock


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_kind,current_kind,current_cap,saved_cap,current_outer", [
    ("chat", "chat", 6, 2, 5),  # No general 'larger number' compatibility.
    ("chat", "chat", 4, 3, 5),
    ("sse", "sse", 4, 1, 5),
    ("native", "native", 5, 2, 5),
    ("chat", "sse", 4, 2, 5),
    ("sse", "chat", 4, 2, 5),
    ("chat", "chat", 4, 2, 4),  # Total outer allowance is immutable too.
])
async def test_unknown_native_and_mismatched_saved_plans_cannot_acquire_dispatch(
    db_session, test_engine, monkeypatch, saved_kind, current_kind,
    current_cap, saved_cap, current_outer,
):
    run, cursor, body = await v3_provider_context(db_session, test_engine, monkeypatch)
    async with owned_session(test_engine, run.id):
        _identity, route = await record_original(
            cursor, body, kind=saved_kind, allowance=saved_cap, used=1,
        )
        before_count = await effect_count(test_engine, run.id)
        cold = replace(cursor, outer_allowance=current_outer, reconcile_only=True,
                       dispatch=rec._DispatchState())
        with rec.provider_effect_scope(cold):
            rec.begin_provider_dispatch(current_kind, allowance=current_cap, seconds=300)
            with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
                await attempts.prepare_generation_attempt(body, route)
            assert cold.dispatch.resume_authorized is False
        assert await effect_count(test_engine, run.id) == before_count

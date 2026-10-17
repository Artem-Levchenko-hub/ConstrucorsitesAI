"""Soft recovery windows preserve durable work and all paid allowances."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_generation_recovery_execution import _bootstrap_wait
from tests.test_generation_worker import _queued_dispatch
from yleum_api.services.max_finalization import generation_deadline_wait, watch_generation_deadline


def _clock(now):
    return {
        "version": 1,
        "stage": "edit",
        "segment_seq": 0,
        "window_ms": 1500000,
        "segment_used_ms": 1499000,
        "segment_deadline": now.isoformat(),
        "interval_id": str(uuid4()),
        "interval_open": True,
        "interval_charged_ms": 0,
        "repair_granted": False,
        "consumed_requirement_ids": [],
        "dependency_failures": 0,
    }


async def test_productive_v3_watchdog_cannot_terminalize_saved_work(db_session, test_engine):
    run = await _queued_dispatch(db_session)
    now = datetime.now(UTC)
    state = _bootstrap_wait(run) | {
        "version": 3,
        "phase": "working",
        "next_check_at": None,
        "clock": _clock(now),
    }
    run.status = "running"
    run.started_at = now - timedelta(hours=1)
    run.agent_state = {**run.agent_state, "recovery_v1": state}
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    assert await generation_deadline_wait(session_factory=factory, generation_run_id=run.id) is None
    assert not await watch_generation_deadline(
        session_factory=factory,
        generation_run_id=run.id,
        now=now,
    )
    await db_session.refresh(run)
    assert run.status == "running" and run.finished_at is None
    assert run.agent_state["recovery_v1"] == state


def _state():
    from types import SimpleNamespace

    from yleum_api.services.generation.recovery_contract import decode_recovery

    run = SimpleNamespace(id=uuid4(), project_id=uuid4(), user_id=uuid4(), prompt_hash="a" * 64)
    return decode_recovery(
        _bootstrap_wait(run) | {"version": 3, "clock": _clock(datetime.now(UTC))}
    )


def test_clock_wire_is_explicit_and_old_versions_cannot_read_it():
    from pydantic import ValidationError

    from yleum_api.services.generation.recovery_contract import RecoveryStateV2, decode_recovery

    state = _state()
    with pytest.raises(ValidationError):
        RecoveryStateV2.model_validate_json(state.model_dump_json())
    raw = state.model_dump(mode="json")
    raw.pop("clock")
    with pytest.raises(ValueError):
        decode_recovery(raw)


def test_interval_idempotence_restart_unknown_and_wait_do_not_refund_paid_budget():
    from yleum_api.services.generation.recovery_clock import charge_interval, resume_clock

    state = _state()
    known = charge_interval(state, interval_id=state.clock.interval_id, elapsed_ms=600)
    assert charge_interval(known, interval_id=state.clock.interval_id, elapsed_ms=600) == known
    assert known.active_work_ms == known.stage_active_work_ms["edit"] == 600
    cold = resume_clock(known, now=datetime.now(UTC) + timedelta(days=2))
    assert cold.clock.segment_used_ms >= cold.clock.window_ms
    assert cold.active_work_ms == known.active_work_ms
    assert cold.execution.budget == state.execution.budget
    with pytest.raises(ValueError):
        charge_interval(cold, interval_id=state.clock.interval_id, elapsed_ms=700)
    paused = known.model_copy(
        update={"clock": known.clock.model_copy(update={"interval_open": False})}
    )
    resumed = resume_clock(paused, now=datetime.now(UTC) + timedelta(days=3))
    assert resumed.clock.segment_used_ms == known.clock.segment_used_ms
    assert resumed.execution.budget == state.execution.budget


def test_dependency_backoff_is_durable_deterministic_bounded():
    from yleum_api.services.generation.recovery_clock import next_dependency_check

    run_id, now = uuid4(), datetime.now(UTC)
    for failures, base in enumerate((15, 30, 60, 120, 300, 300, 300), 1):
        due = next_dependency_check(run_id, failures=failures, now=now)
        assert due == next_dependency_check(run_id, failures=failures, now=now)
        assert base <= (due - now).total_seconds() <= base * 1.1


@pytest.mark.parametrize("blocked", ["nonquiescent", "exception"])
@pytest.mark.parametrize("lane", ["maintenance", "fresh"])
async def test_actual_dispatch_rotates_considered_dependency_waiters(monkeypatch, blocked, lane):
    import asyncio
    from types import SimpleNamespace

    from yleum_api.services.generation import recovery_dependencies
    from yleum_api.workers import generation

    a = generation.HostDispatch(uuid4(), "A", maintenance=lane == "maintenance")
    b = generation.HostDispatch(uuid4(), "B", maintenance=lane == "maintenance")
    considered, executed = [], []
    ticks = 0
    original_sleep = asyncio.sleep

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    async def scan(_session, _active):
        return ({}, [], [a, b]) if lane == "maintenance" else ({}, [a, b], [])

    async def poll(_factory, run_id):
        considered.append(run_id)
        if run_id == a.run_id:
            if blocked == "exception":
                raise RuntimeError("synthetic unavailable dependency")
            return False
        return True

    async def execute(run_id):
        executed.append(run_id)
        return False

    async def heartbeat(*_args, **_kwargs):
        pass

    async def tick(_seconds):
        nonlocal ticks
        await original_sleep(0)
        ticks += 1
        if ticks == 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(generation, "get_engine", object)
    monkeypatch.setattr(generation, "async_sessionmaker", lambda *_a, **_kw: Session)
    monkeypatch.setattr(generation, "_load_dispatch_scan", scan)
    monkeypatch.setattr(generation, "execute_dispatch", execute)
    monkeypatch.setattr(generation, "current_dispatch_limit", lambda: 1)
    monkeypatch.setattr(generation, "get_settings", lambda: SimpleNamespace(
        generation_worker_max_concurrent=1, omnia_release_sha="a" * 40,
    ))
    monkeypatch.setattr(generation, "get_redis", lambda: SimpleNamespace(set=heartbeat))
    monkeypatch.setattr(generation, "asyncio", SimpleNamespace(
        create_task=asyncio.create_task, gather=asyncio.gather, sleep=tick,
    ))
    monkeypatch.setattr(recovery_dependencies, "poll_due_dependency", poll)
    with pytest.raises(asyncio.CancelledError):
        await generation._run_dispatch_forever()
    assert considered == [a.run_id, b.run_id, a.run_id, b.run_id]
    assert executed == [b.run_id, b.run_id]


async def test_future_v3_wait_does_not_occupy_worker_selection(db_session):
    from yleum_api.workers.generation import _maintenance_candidates_query

    run = await _queued_dispatch(db_session)
    now = datetime.now(UTC)
    state = _bootstrap_wait(run) | {
        "version": 3,
        "clock": _clock(now),
        "next_check_at": (now + timedelta(minutes=5)).isoformat(),
    }
    run.status = "running"
    run.execution_backend = "worker"
    run.execution_started_at = now
    run.agent_state = {**run.agent_state, "recovery_v1": state}
    await db_session.commit()
    assert not (await db_session.execute(_maintenance_candidates_query([]))).all()


@pytest.fixture
async def clock_owner(db_session, test_engine, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy import text

    from tests.test_generation_recovery_readers import install_healthy_reader_inventory
    from yleum_api.services.generation import recovery_execution as execution
    from yleum_api.services.generation.recovery_clock import start_interval
    from yleum_api.services.generation.recovery_contract import ContinuationClaim, decode_recovery

    install_healthy_reader_inventory(monkeypatch, refresh=True)
    run = await _queued_dispatch(db_session)
    from yleum_api.services.generation_runs import prompt_hash

    run.prompt_hash = prompt_hash("Собери приложение")
    run.status = "running"
    now = datetime.now(UTC)
    claim = ContinuationClaim(
        run_id=run.id,
        token=uuid4(),
        attempt_seq=0,
        worker_id="clock-test",
        expires_at=now + timedelta(minutes=1),
    )
    state = decode_recovery(
        _bootstrap_wait(run)
        | {
            "version": 3,
            "clock": _clock(now),
            "phase": "working",
            "next_check_at": None,
            "continuation_claim": claim.model_dump(mode="json"),
        }
    )
    run.agent_state = {**run.agent_state, "recovery_v1": state.model_dump(mode="json")}
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    driver = execution.ExecutionDriver(factory, state, {}, AsyncMock())
    start_interval(state.clock)

    @asynccontextmanager
    async def owned():
        async with test_engine.connect() as connection:
            await connection.execute(
                text("SELECT pg_advisory_lock(1195724366,:key)"),
                {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
            )
            await connection.commit()
            token = execution._SESSION_OWNER.set(execution.SessionOwnership(run.id, connection))
            try:
                yield
            finally:
                execution._SESSION_OWNER.reset(token)
                await connection.execute(text("SELECT pg_advisory_unlock_all()"))
                await connection.commit()

    return SimpleNamespace(
        run=run, driver=driver, factory=factory, owned=owned, session=db_session, engine=test_engine
    )


async def test_known_clock_charge_CAS_rejects_stale_writer_and_unknown_ACK(clock_owner):
    from sqlalchemy.ext.asyncio import AsyncSession

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation.recovery_clock import charge_interval, update_owned_clock
    from yleum_api.services.generation.recovery_execution import ExecutionDriver, recovery_state

    c = clock_owner
    stale = ExecutionDriver(c.factory, c.driver.state, {}, c.driver.snapshot)

    async def charged(_session, state):
        return charge_interval(state, interval_id=state.clock.interval_id, elapsed_ms=1000)

    class LostAck(AsyncSession):
        async def commit(self):
            await super().commit()
            raise RuntimeError("synthetic commit ACK lost")

    uncertain = ExecutionDriver(
        async_sessionmaker(c.engine, class_=LostAck, expire_on_commit=False),
        c.driver.state,
        {},
        c.driver.snapshot,
    )
    async with c.owned():
        with pytest.raises(RuntimeError, match="ACK lost"):
            await update_owned_clock(uncertain, charged)
        with pytest.raises(ValueError, match="owner changed"):
            await update_owned_clock(stale, charged)
    async with c.factory() as session:
        saved = recovery_state(await session.get(GenerationRun, c.run.id))
    assert saved.active_work_ms == saved.stage_active_work_ms["edit"] == 1000
    assert saved.clock.segment_used_ms == c.driver.state.clock.segment_used_ms + 1000
    assert saved.execution.budget == c.driver.state.execution.budget


async def test_health_unknown_reschedules_without_slot_or_refunding(clock_owner, monkeypatch):
    from unittest.mock import AsyncMock

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation import provider_health, recovery_dependencies
    from yleum_api.services.generation.provider_health_auth import unknown_snapshot
    from yleum_api.services.generation.recovery_execution import recovery_state

    c = clock_owner
    state = c.driver.state.model_copy(
        update={
            "phase": "waiting_dependency",
            "next_check_at": datetime.now(UTC) - timedelta(seconds=1),
            "clock": c.driver.state.clock.model_copy(
                update={
                    "interval_open": False,
                    "wait_dependency": "funds",
                    "dependency_failures": 1,
                }
            ),
        }
    )
    c.run.agent_state = {**c.run.agent_state, "recovery_v1": state.model_dump(mode="json")}
    await c.session.commit()
    probe = AsyncMock(return_value=unknown_snapshot(None, "unsupported"))
    monkeypatch.setattr(provider_health, "free_health_for_run", probe)
    before = datetime.now(UTC)
    assert not await recovery_dependencies.poll_due_dependency(c.factory, c.run.id)
    async with c.factory() as session:
        saved = recovery_state(await session.get(GenerationRun, c.run.id))
    assert saved.clock.wait_dependency == "funds" and saved.clock.dependency_failures == 2
    assert 30 <= (saved.next_check_at - before).total_seconds() < 34
    assert saved.execution == state.execution and saved.active_work_ms == state.active_work_ms
    assert not await recovery_dependencies.poll_due_dependency(c.factory, c.run.id)
    probe.assert_awaited_once()


async def test_wait_pause_and_cold_crash_preserve_known_work_without_time_refund(clock_owner):
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation.recovery_execution import (
        GenerationYielded,
        claim_continuation,
        recovery_state,
    )

    c = clock_owner
    async with c.owned():
        with pytest.raises(GenerationYielded):
            await c.driver.wait()
        await c.driver.account_work(stopped=True)
        stopped = c.driver.state
        assert not stopped.clock.interval_open
        async with c.factory() as session:
            assert (
                await claim_continuation(
                    session,
                    run_id=c.run.id,
                    expected_attempt=0,
                    worker_id="too-early",
                    now=datetime.now(UTC),
                )
                is None
            )
        later = datetime.now(UTC) + timedelta(days=2)
        async with c.factory() as session:
            assert await claim_continuation(
                session, run_id=c.run.id, expected_attempt=0, worker_id="cold", now=later
            )
            await session.commit()
        async with c.factory() as session:
            resumed = recovery_state(await session.get(GenerationRun, c.run.id))
            assert resumed.active_work_ms == stopped.active_work_ms
            assert resumed.clock.segment_used_ms == stopped.clock.segment_used_ms
            assert resumed.execution.budget == stopped.execution.budget
            assert await claim_continuation(
                session,
                run_id=c.run.id,
                expected_attempt=1,
                worker_id="after-unknown-crash",
                now=later + timedelta(minutes=1),
            )
            await session.commit()
        async with c.factory() as session:
            crashed = recovery_state(await session.get(GenerationRun, c.run.id))
        assert crashed.clock.segment_used_ms >= crashed.clock.window_ms
        assert crashed.clock.segment_seq == stopped.clock.segment_seq == 0
        assert crashed.active_work_ms == stopped.active_work_ms


@pytest.mark.parametrize("change", ["cancel", "revision"])
async def test_health_wait_rechecks_owner_after_slow_advisory(clock_owner, monkeypatch, change):
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation import provider_health, recovery_dependencies
    from yleum_api.services.generation.provider_health_auth import unknown_snapshot

    c = clock_owner
    state = c.driver.state.model_copy(
        update={
            "phase": "waiting_dependency",
            "next_check_at": datetime.now(UTC) - timedelta(seconds=1),
            "clock": c.driver.state.clock.model_copy(
                update={
                    "interval_open": False,
                    "wait_dependency": "credentials",
                    "dependency_failures": 1,
                }
            ),
        }
    )
    c.run.agent_state = {**c.run.agent_state, "recovery_v1": state.model_dump(mode="json")}
    await c.session.commit()

    async def slow(**kwargs):
        async with c.factory() as session:
            run = await session.get(GenerationRun, c.run.id)
            if change == "cancel":
                run.status = "cancel_requested"
            else:
                updated = state.model_copy(update={"state_revision": state.state_revision + 1})
                run.agent_state = {
                    **run.agent_state,
                    "recovery_v1": updated.model_dump(mode="json"),
                }
            await session.commit()
        return unknown_snapshot(None, "unsupported")

    monkeypatch.setattr(provider_health, "free_health_for_run", slow)
    if change == "cancel":
        with pytest.raises(ValueError):
            await recovery_dependencies.poll_due_dependency(c.factory, c.run.id)
    else:
        assert not await recovery_dependencies.poll_due_dependency(c.factory, c.run.id)
    await c.session.refresh(c.run)
    assert c.run.agent_state["recovery_v1"]["clock"]["dependency_failures"] == 1


@pytest.fixture
async def proof_clock(clock_owner, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from yleum_api.models.project_cell import (
        ProjectCellActivityLease,
        ProjectCellProof,
        ProjectCellProofResult,
        ProjectCellWorkspace,
    )
    from yleum_api.services.generation import recovery_requirements
    from yleum_api.services.generation.recovery_contract import RecoveryBinding
    from yleum_api.services.orchestrator_client import ProjectCellWorkspaceIdentity
    from yleum_api.services.project_cell_proofs import ProofDimension, ProofIdentity

    c = clock_owner
    workspace = ProjectCellWorkspace(
        id=uuid4(),
        project_id=c.run.project_id,
        owner_id=c.run.user_id,
        provider="docker_owner_canary",
        state="ready",
        generation_run_id=c.run.id,
        fencing_epoch=7,
    )
    c.session.add(workspace)
    await c.session.flush()
    binding = RecoveryBinding(
        workspace_id=str(workspace.id),
        fencing_epoch=7,
        source_sha256="b" * 64,
        schema_sha256="c" * 64,
        environment_sha256="e" * 64,
    )
    identity = ProofIdentity(
        workspace.id,
        c.run.id,
        7,
        binding.source_sha256,
        "d" * 64,
        binding.schema_sha256,
        "a" * 64,
        "e" * 64,
        "e" * 64,
        "profile-v1",
        "f" * 64,
    )
    proof = ProjectCellProof(
        id=uuid4(),
        **{k: getattr(identity, k) for k in identity.__dataclass_fields__},
        proof_key=identity.proof_key,
    )
    now = datetime.now(UTC)
    lease = ProjectCellActivityLease(
        operation_id=uuid4(),
        workspace_id=workspace.id,
        generation_run_id=c.run.id,
        kind="command",
        state="failed",
        fencing_epoch=7,
        proof_key=identity.proof_key,
        phase="fast_check",
        started_at=now,
        heartbeat_at=now,
        finished_at=now,
        deadline_at=now + timedelta(minutes=1),
        log_bytes=0,
    )
    detail = "TS2554: expected an argument"
    result = ProjectCellProofResult(
        id=uuid4(),
        proof_id=proof.id,
        workspace_id=workspace.id,
        dimension="fast_check",
        dimension_key=identity.dimension_key(ProofDimension.FAST_CHECK),
        outcome="red",
        operation_id=lease.operation_id,
        artifact_ref=None,
        detail_digest=hashlib.sha256(detail.encode()).hexdigest(),
        redacted_detail=detail,
    )
    c.session.add_all([proof, lease, result])
    c.driver.state = c.driver.state.model_copy(update={"binding": binding})
    c.run.agent_state = {**c.run.agent_state, "recovery_v1": c.driver.state.model_dump(mode="json")}
    await c.session.commit()
    physical = ProjectCellWorkspaceIdentity(
        binding.source_sha256, "d" * 64, binding.schema_sha256, "a" * 64, "e" * 64, "f" * 64
    )

    async def current(*args, **kwargs):
        return physical

    monkeypatch.setattr(recovery_requirements, "project_cell_agent_identity", current)
    return SimpleNamespace(
        owner=c,
        workspace=workspace,
        proof=proof,
        result=result,
        lease=lease,
        binding=binding,
        identity=identity,
    )


@pytest.mark.parametrize("damage", [None, "source", "unfinished", "cancel", "foreign"])
async def test_first_repair_window_requires_live_owned_terminal_red(proof_clock, damage):
    from yleum_api.services.generation.recovery_clock import grant_window

    c, p = proof_clock.owner, proof_clock
    if damage == "source":
        p.proof.workspace_revision = "0" * 64
    elif damage == "unfinished":
        p.lease.finished_at = None
    elif damage == "cancel":
        c.run.status = "cancel_requested"
    elif damage == "foreign":
        from yleum_api.models.user import User

        foreign = User(
            id=uuid4(),
            email=f"foreign-{uuid4().hex}@example.test",
            password_hash="synthetic",
        )
        c.session.add(foreign)
        await c.session.flush([foreign])
        p.workspace.owner_id = foreign.id
    await c.session.commit()
    budget = c.driver.state.execution.budget
    async with c.owned():
        if damage:
            with pytest.raises(ValueError):
                await grant_window(c.driver, repair_receipt_id=p.result.id)
            assert not c.driver.state.clock.repair_granted
        else:
            from yleum_api.services.generation.recovery_execution import GenerationYielded
            from yleum_api.services.max_finalization import MaxFinalizationCoordinator

            coordinator = object.__new__(MaxFinalizationCoordinator)
            with pytest.raises(GenerationYielded):
                await coordinator._grant_initial_repair_window(c.driver, p.result)
            assert c.driver.state.phase == "waiting_dependency"
            assert c.driver.state.clock.stage == "repair"
            assert c.driver.state.clock.segment_seq == 1
            assert not await grant_window(c.driver, repair_receipt_id=p.result.id)
            assert c.driver.state.clock.segment_seq == 1
    assert c.driver.state.execution.budget == budget


@pytest.mark.parametrize("outcome", ["confirmed", "tampered", "unknown_ack"])
async def test_whole_named_bundle_grants_once_then_proof_only(proof_clock, monkeypatch, outcome):
    from types import SimpleNamespace

    from tests.test_max_behavior_proof import driver as behavior_driver
    from yleum_api.models.project_cell import ProjectCellCandidate
    from yleum_api.services import max_behavior_proof as behavior
    from yleum_api.services.build_spec import FidelitySpec
    from yleum_api.services.generation.recovery_clock import grant_window
    from yleum_api.services.generation.recovery_requirements import (
        frozen_requirements,
        store_frozen_requirements,
    )

    c, p = proof_clock.owner, proof_clock
    named = behavior.required_contract("task_density_v1", template="max_miniapp")
    c.run.prompt_hash = named.request_sha256
    candidate = ProjectCellCandidate(
        id=uuid4(),
        workspace_id=p.workspace.id,
        generation_run_id=c.run.id,
        fencing_epoch=7,
        source_revision=p.binding.source_sha256,
        migration_digest="a" * 64,
        database_backup_ref="database-backup/sha256/" + "b" * 64,
        build_ref="build/sha256/" + "c" * 64,
        verification_ref="verification/sha256/" + "d" * 64,
        status="prepared",
        cancelled=False,
    )
    c.session.add(candidate)
    bound = behavior.CandidateBehaviorBinding(
        candidate.id,
        c.run.project_id,
        p.workspace.id,
        c.run.id,
        7,
        p.identity.proof_key,
        p.binding.source_sha256,
        candidate.build_ref,
        named.request_sha256,
        named.contract_digest,
        "owner_preview",
    )
    registered, _ = behavior_driver(bound)
    receipt = await behavior.observe_candidate(
        bound, named, registered, behavior.PrivatePreviewCapability(SimpleNamespace())
    )
    c.run.agent_state = {
        **c.run.agent_state,
        behavior.STATE_KEY: named.to_json(),
        behavior.RECEIPT_KEY: receipt,
    }
    store_frozen_requirements(c.run, "task_density_v1", FidelitySpec(), named)
    c.driver.state = c.driver.state.model_copy(
        update={
            "identity": c.driver.state.identity.model_copy(
                update={"prompt_hash": named.request_sha256}
            ),
            "requirements": frozen_requirements(c.run),
        }
    )
    c.run.agent_state = {**c.run.agent_state, "recovery_v1": c.driver.state.model_dump(mode="json")}
    await c.session.commit()
    original_budget = c.driver.state.execution.budget
    async with c.owned():
        if outcome == "tampered":
            c.run.agent_state = {
                **c.run.agent_state,
                behavior.RECEIPT_KEY: {
                    **receipt,
                    "receipt_digest": "f" * 64,
                },
            }
            await c.session.commit()
            with pytest.raises(ValueError):
                await grant_window(c.driver)
            assert c.driver.state.clock.segment_seq == 0
            assert c.driver.state.clock.consumed_requirement_ids == ()
            from yleum_api.services.generation.recovery_execution import GenerationYielded

            with pytest.raises(GenerationYielded):
                await c.driver.soft_boundary(exhausted=True)
            assert c.driver.state.phase == "waiting_dependency"
            assert c.driver.state.clock.segment_seq == 0
            assert c.driver.state.next_check_at > datetime.now(UTC)
            assert c.driver.state.execution.budget == original_budget
            return
        if outcome == "unknown_ack":
            from sqlalchemy.ext.asyncio import AsyncSession

            from yleum_api.models.generation_run import GenerationRun
            from yleum_api.services.generation.recovery_execution import recovery_state

            class LostAck(AsyncSession):
                async def commit(self):
                    await super().commit()
                    raise RuntimeError("synthetic grant ACK lost")

            c.driver.factory = async_sessionmaker(c.engine, class_=LostAck, expire_on_commit=False)
            with pytest.raises(RuntimeError, match="ACK lost"):
                await grant_window(c.driver)
            assert c.driver.state.clock.segment_seq == 0  # no local false grant
            async with c.factory() as session:
                c.driver.state = recovery_state(await session.get(GenerationRun, c.run.id))
            c.driver.factory = c.factory
        else:
            assert await grant_window(c.driver)
        assert c.driver.state.clock.stage == "proof"
        assert c.driver.soft_edit_deadline(None) <= datetime.now(UTC)
        expected = {r.id for r in c.driver.state.requirements if r.required}
        assert set(c.driver.state.clock.consumed_requirement_ids) == expected
        assert not await grant_window(c.driver)
        assert c.driver.state.clock.segment_seq == 1
        assert c.driver.state.execution.budget == original_budget


@pytest.fixture
async def response_schema(db_session, test_engine):
    """Use the actual private-body migration absent from ORM-only test metadata."""
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    path = Path(__file__).parents[1] / "migrations/versions/0087_generation_responses.py"
    spec = importlib.util.spec_from_file_location("clock_response_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def upgrade(connection):
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

    def cleanup(connection):
        migration.op = Operations(MigrationContext.configure(connection))
        connection.execute(text("DELETE FROM generation_response_chunks"))
        connection.execute(text("DELETE FROM generation_responses"))
        migration.downgrade()

    async with test_engine.begin() as connection:
        await connection.run_sync(upgrade)
    try:
        yield
    finally:
        await db_session.rollback()
        async with test_engine.begin() as connection:
            await connection.run_sync(cleanup)


@pytest.mark.parametrize("edge", [None, "partial_tool", "early_check"])
async def test_v3_cold_public_pipeline_keeps_tools_and_paid_allowance_once(
    db_session,
    test_engine,
    monkeypatch,
    edge,
    response_schema,
):
    from tests.test_generation_recovery_execution import (
        test_noninitial_worker_reenters_real_pipeline_native_loop_with_fresh_fence as actual_flow,
    )

    await actual_flow(db_session, test_engine, monkeypatch, edge, False, True, soft_windows=True)


@pytest.mark.parametrize("resume", [False, True])
async def test_v3_initial_context_is_committed_before_runtime_and_survives_flag_off(
    db_session,
    test_engine,
    monkeypatch,
    resume,
):
    from tests.test_generation_recovery_execution import (
        test_pipeline_commits_bootstrap_context_before_first_runtime_effect as actual_bootstrap,
    )

    await actual_bootstrap(db_session, test_engine, monkeypatch, resume, soft_windows=True)


async def test_cell_release_cannot_pause_clock_while_provider_receiver_is_unknown(clock_owner):
    from sqlalchemy import text

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse

    c = clock_owner
    workspace = ProjectCellWorkspace(
        id=uuid4(),
        project_id=c.run.project_id,
        owner_id=c.run.user_id,
        provider="synthetic",
        state="ready",
        fencing_epoch=2,
    )
    c.session.add(workspace)
    await c.session.flush()
    receipt = ProjectCellResourceResponse(
        workspace_id=workspace.id,
        state="resources_ready",
        provider_ref="owned",
        fencing_epoch=2,
        checkpoint_ref=None,
        has_workspace=True,
        has_agent_home=True,
        has_postgres=True,
        has_redis=True,
    )
    release = ProjectCellOperation(
        id=uuid4(),
        workspace_id=workspace.id,
        generation_run_id=c.run.id,
        kind="release",
        status="completed",
        fencing_epoch=2,
        finished_at=datetime.now(UTC),
        idempotency_key=str(uuid4()),
        request_digest="a" * 64,
        result_payload=receipt.to_wire_json(),
    )
    c.session.add(release)
    c.session.add(
        GenerationProviderAttempt(
            attempt_id=uuid4(),
            run_id=c.run.id,
            project_id=c.run.project_id,
            user_id=c.run.user_id,
            prompt_hash=c.run.prompt_hash,
            request_sha256="a" * 64,
            dispatch_token=uuid4(),
            phase="pending",
        )
    )
    state = c.driver.state.model_copy(
        update={
            "phase": "waiting_dependency",
            "execution": c.driver.state.execution.model_copy(
                update={
                    "physical_state": "released",
                    "control_workspace_id": workspace.id,
                    "control_fencing_epoch": 2,
                    "release_operation_id": release.id,
                }
            ),
        }
    )
    c.run.agent_state = {**c.run.agent_state, "recovery_v1": state.model_dump(mode="json")}
    await c.session.commit()
    c.driver.state = state
    async with c.owned():
        with pytest.raises(ValueError, match="active physical writer"):
            await c.driver.account_work(stopped=True)
    await c.session.refresh(c.run)
    assert c.run.agent_state["recovery_v1"] == state.model_dump(mode="json")
    assert await c.session.scalar(text("SELECT count(*) FROM generation_provider_attempts")) == 1


async def test_v3_cold_public_xml_pipeline_preserves_original_contract(
    db_session, test_engine, monkeypatch, response_schema
):
    from tests.test_generation_recovery_execution import (
        test_noninitial_worker_reenters_real_pipeline_native_loop_with_fresh_fence as actual_flow,
    )

    await actual_flow(
        db_session, test_engine, monkeypatch, None, False, True, soft_windows=True, native=False
    )


@pytest.mark.parametrize("kind", ["native", "legacy"])
@pytest.mark.parametrize("changed", [None, "model", "task"])
async def test_soft_window_changes_only_runtime_cutoff_not_saved_loop_identity(
    monkeypatch, kind, changed
):
    from copy import deepcopy
    from types import SimpleNamespace

    from yleum_api.services import agent_builder, agent_native
    from yleum_api.services.generation.recovery_execution import GenerationYielded

    state = _state()
    original = datetime.now(UTC) + timedelta(seconds=300)
    state = state.model_copy(
        update={
            "execution": state.execution.model_copy(
                update={
                    "budget": state.execution.budget.model_copy(
                        update={"absolute_deadline": original}
                    ),
                }
            )
        }
    )
    frames, calls, effects = [], [], []
    cold = False
    deadline = original

    async def provider(frame, invoke):
        frames.append(deepcopy(frame))
        return await invoke()

    def restore(requested):
        assert requested == kind
        return deepcopy(frames[-1]["value"]) if cold else None

    driver = SimpleNamespace(state=state, provider=provider, restore_frame=restore)

    async def model(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise GenerationYielded(
                run_id=state.identity.run_id,
                checkpoint=None,
                attempt_seq=1,
                phase="waiting_dependency",
            )
        path = "first.ts" if len(calls) == 1 else "second.ts"
        if kind == "native":
            return {
                "content": [
                    {"type": "tool_use", "id": path, "name": "read_file", "input": {"path": path}}
                ],
                "stop_reason": "tool_use",
            }
        return '<omnia:action name="read_file">{"path":"' + path + '"}</omnia:action>'

    async def execute(action):
        effects.append((action.name, action.path))
        return {"ok": True, "content": "synthetic"}

    monkeypatch.setattr(agent_native, "_call_messages", model)

    async def run():
        chosen_model = "changed" if cold and changed == "model" else "synthetic"
        task = "changed" if cold and changed == "task" else "original"
        if kind == "native":
            return await agent_native._run_native_segment(
                system="synthetic",
                task=task,
                model=chosen_model,
                execute=execute,
                max_steps=2,
                continuation=driver,
                edit_deadline=deadline,
            )
        return await agent_builder._run_agent_segment(
            system_prompt="synthetic",
            user_prompt=task,
            model=chosen_model,
            execute=execute,
            complete=model,
            max_steps=2,
            continuation=driver,
            edit_deadline=deadline,
        )

    with pytest.raises(GenerationYielded):
        await run()
    assert effects == [("read_file", "first.ts")]
    saved_frame = deepcopy(frames[-1])
    cold = True
    deadline += timedelta(seconds=120)
    if changed:
        with pytest.raises(ValueError, match="changed"):
            await run()
        assert len(calls) == 2 and effects == [("read_file", "first.ts")]
    else:
        result = await run()
        assert result.steps == 2
        expected = [("read_file", "first.ts"), ("read_file", "second.ts")]
        if kind == "native":
            expected.append(("build", ""))
        assert effects == expected
        assert frames[-1] == saved_frame
    assert driver.state.execution.budget.absolute_deadline == original


async def test_exhausted_unsupported_window_preserves_work_as_needs_input(clock_owner):
    from sqlalchemy import func, select

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.services.generation.recovery_execution import GenerationYielded

    c = clock_owner
    original = c.driver.state
    async with c.owned():
        with pytest.raises(GenerationYielded) as caught:
            await c.driver.soft_boundary(exhausted=True)
    await c.session.refresh(c.run)
    assert caught.value.phase == c.run.agent_state["recovery_v1"]["phase"] == "needs_input"
    assert c.run.status == "running" and c.run.finished_at is None
    assert c.driver.state.execution.budget == original.execution.budget
    assert c.driver.state.clock.segment_seq == original.clock.segment_seq == 0
    assert c.driver.state.active_work_ms >= original.active_work_ms
    assert c.driver.state.next_check_at is None
    assert await c.session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 0

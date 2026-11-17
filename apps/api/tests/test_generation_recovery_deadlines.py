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


async def test_unproved_recovery_parks_the_original_cursor_without_a_due_claim(clock_owner):
    from yleum_api.services.generation import recovery_execution as execution

    c = clock_owner
    old = c.driver.state
    async with c.owned():
        parked = await execution.commit_current_wait(c.factory, old, phase="needs_input")
        async with c.factory() as session:
            claim = await execution.claim_continuation(
                session, run_id=c.run.id, expected_attempt=old.attempt_seq,
                worker_id="cold", now=datetime.now(UTC) + timedelta(hours=1),
            )
            assert claim is None
            await session.commit()
    await c.session.refresh(c.run)
    assert execution.recovery_state(c.run) == parked
    assert parked.phase == "needs_input" and parked.next_check_at is None
    assert parked.execution == old.execution and parked.attempt_seq == old.attempt_seq
    assert parked.checkpoint == old.checkpoint and parked.clock == old.clock


@pytest.mark.parametrize("provider_phase", ["dispatching", "indeterminate", "closed"])
async def test_response_wait_defers_open_attempt_before_claiming_a_cell(
    clock_owner, monkeypatch, provider_phase,
):
    from unittest.mock import AsyncMock
    from uuid import uuid5

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.models.provider_call import ProviderCall
    from yleum_api.services.generation import recovery_execution, recovery_store
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        RecoveryBinding,
        decode_recovery,
    )

    c = clock_owner
    old = c.driver.state
    workspace = uuid4()
    binding = RecoveryBinding(
        workspace_id=str(workspace), fencing_epoch=3,
        source_sha256="a" * 64, schema_sha256="b" * 64, environment_sha256="c" * 64,
    )
    ref = CheckpointRef(object_key="qa-retained", sha256="d" * 64)
    root = uuid5(c.run.id, "recovery-provider-v1:12")
    attempt = uuid5(root, "provider-attempt-v1")
    state = decode_recovery(old.model_copy(update={
        "cursor": "finalization", "binding": binding, "checkpoint": ref, "context_ref": ref,
        # A crashed acquisition also leaves a due working cursor.
        "phase": "working", "next_check_at": None,
        "execution": old.execution.model_copy(update={
            "stage": "finalization", "substep": "response_wait",
            "logical_step_seq": 12, "provider_effect_id": root, "pending_effect_id": root,
            "physical_state": "released", "release_operation_id": uuid4(),
            "control_workspace_id": workspace, "control_fencing_epoch": 4,
            "execution_context_ref": ref,
        }),
    }).model_dump(mode="json"))
    c.run.agent_state = {**c.run.agent_state, "recovery_v1": state.model_dump(mode="json")}
    attempt_scope = dict(
        run_id=c.run.id, project_id=c.run.project_id, user_id=c.run.user_id,
    )
    status = {"dispatching": "started", "indeterminate": "ambiguous", "closed": "completed"}
    provider_call = ProviderCall(
        **attempt_scope, id=uuid4(), provider_scope="llmgw", route="native_messages",
        requested_model="synthetic", expense_owner="user", stage="source_repair", free=True,
        status=status[provider_phase], finished_at=(
            None if provider_phase == "dispatching" else datetime.now(UTC)
        ),
    )
    c.session.add(provider_call)
    await c.session.flush()
    c.session.add(GenerationProviderAttempt(
        **attempt_scope, attempt_id=attempt, dispatch_token=uuid4(),
        prompt_hash=c.run.prompt_hash, request_sha256="e" * 64, phase=provider_phase,
        provider_call_id=provider_call.id,
        terminal_reason="completed" if provider_phase == "closed" else None,
    ))
    await c.session.flush()
    c.session.add(GenerationRecoveryEffect(
        **attempt_scope, effect_id=root, logical_step_seq=12, effect_ordinal=0, kind="provider",
        input_digest="e" * 64, provider_attempt_id=attempt, attempt_seq=state.attempt_seq,
        claim_token=state.continuation_claim.token,
    ))
    await c.session.commit()
    monkeypatch.setattr(recovery_store, "_baseline_sha", AsyncMock())
    released = AsyncMock(return_value=False)
    monkeypatch.setattr(recovery_execution, "_released_quiescent", released)

    now = datetime.now(UTC)
    async with c.owned(), c.factory() as session:
        claim = await recovery_execution.claim_continuation(
            session, run_id=c.run.id, expected_attempt=state.attempt_seq,
            worker_id="qa-worker", now=now,
        )
        await session.commit()
    assert claim is None
    await c.session.refresh(c.run)
    after = recovery_execution.recovery_state(c.run)
    if provider_phase == "closed":
        released.assert_awaited_once()
        assert after == state
    else:
        released.assert_not_awaited()
        assert after.phase == "waiting_dependency" and after.next_check_at > now
        assert after.attempt_seq == state.attempt_seq and after.execution == state.execution
        assert after.checkpoint == ref and after.binding == binding
        assert after.clock.segment_deadline == state.clock.segment_deadline
        assert after.clock.segment_used_ms == state.clock.segment_used_ms
        assert after.clock.dependency_failures == state.clock.dependency_failures + 1


@pytest.mark.parametrize("outcome", ["stored", "unknown", "pending"])
@pytest.mark.parametrize("kind", ["native", "legacy"])
async def test_timeout_reads_retained_completion_under_the_same_lease(monkeypatch, outcome, kind):
    from unittest.mock import AsyncMock

    import httpx
    from pydantic import SecretStr

    from yleum_api.core import config
    from yleum_api.services import agent_native, llm_client
    from yleum_api.services.generation import (
        provider_attempts,
        recovery_execution,
        recovery_reconciliation,
        response_auth,
    )
    from yleum_api.services.generation.provider_identity import GenerationProviderIdentity
    from yleum_api.services.generation.recovery_contract import ContinuationClaim

    state = _state()
    claim = ContinuationClaim(
        run_id=state.identity.run_id, token=uuid4(), attempt_seq=state.attempt_seq,
        worker_id="qa-worker", expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    state = state.model_copy(update={"continuation_claim": claim})
    driver = recovery_execution.ExecutionDriver(None, state, {}, AsyncMock())
    driver.soft_boundary = AsyncMock()
    driver.wait = AsyncMock(side_effect=RuntimeError("committed wait"))
    monkeypatch.setattr(
        recovery_execution, "_has_retained_completion",
        AsyncMock(return_value=outcome != "unknown"),
    )

    async def save(execution, *, context):
        driver.state = driver.state.model_copy(update={
            "state_revision": driver.state.state_revision + 1, "execution": execution,
        })
        driver.context = context

    driver.save = save
    effects = []
    methods = []
    response = (
        {"content": [{"type": "text", "text": "retained result"}], "stop_reason": "end_turn"}
        if kind == "native" else
        {"choices": [{"message": {"content": "retained result"}, "finish_reason": "stop"}]}
    )
    ids = state.identity
    identity = GenerationProviderIdentity(
        ids.run_id, ids.project_id, ids.user_id, uuid4(), ids.prompt_hash, "b" * 64,
    )
    settings = config.get_settings().model_copy(update={
        "llm_gateway_url": "http://gateway.test",
        "generation_response_retention_enabled": True,
        "generation_response_signing_secret": SecretStr("synthetic-test-secret-" * 3),
    })
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=identity),
    )
    reserve_wire = AsyncMock(return_value=None)
    monkeypatch.setattr(provider_attempts, "reserve_generation_wire", reserve_wire)

    def gateway(request):
        methods.append(request.method)
        purpose = "generation_response.record" if request.method == "POST" else (
            "generation_response.read"
        )
        assert response_auth.verify_generation_response_token(
            request.headers[response_auth.HEADER], purpose=purpose,
        ) == identity
        if request.method == "POST":
            raise httpx.ReadTimeout("synthetic response timeout")
        if outcome == "pending":
            return httpx.Response(200, json={
                "version": 1, "identity": identity.to_wire(), "state": "pending",
            })
        return httpx.Response(200, json={
            "version": 1, "identity": identity.to_wire(), "state": "stored",
            "response": {"format": "json", "status": 200, "body": response},
        })

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: client_type(transport=httpx.MockTransport(gateway), **kw),
    )

    async def invoke():
        read_only = recovery_reconciliation.provider_reconciliation_requested()
        attempt = recovery_reconciliation.prospective_provider_attempt()
        effects.append((read_only, attempt))
        if kind == "legacy":
            reply = await llm_client._chat_post(
                {"model": "synthetic", "metadata": {}},
                "http://gateway.test/v1/chat/completions", 90,
            )
            return reply.json()
        async with httpx.AsyncClient() as client:
            return await agent_native._call_messages(
                client, "http://gateway.test/v1/messages", [], "test system",
            )

    frame = {"version": 1, "kind": kind, "value": {
        "attempt": 0, "call_deadline": (datetime.now(UTC) + timedelta(seconds=90)).isoformat(),
    } if kind == "legacy" else {}}
    if outcome == "stored":
        assert await driver.provider(frame, invoke) == response
        driver.wait.assert_not_awaited()
    else:
        with pytest.raises(RuntimeError, match="committed wait"):
            await driver.provider(frame, invoke)
        driver.wait.assert_awaited_once()
    expected_effects = [(False, effects[0][1])]
    if outcome != "unknown":
        expected_effects.append((True, effects[0][1]))
    assert effects == expected_effects
    assert methods == (["POST"] if outcome == "unknown" else ["POST", "GET"])
    reserve_wire.assert_awaited_once()
    assert driver.state.identity == state.identity
    assert driver.state.execution.budget.steps == state.execution.budget.steps + 1
    assert driver.state.execution.logical_step_seq == state.execution.logical_step_seq + 1


@pytest.mark.parametrize("finish", ["content_filter", "stop", None])
@pytest.mark.parametrize("after_timeout", [False, True])
async def test_only_the_owned_retained_filter_hands_the_draft_to_verification(
    monkeypatch, finish, after_timeout,
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from yleum_api.services import llm_client
    from yleum_api.services.generation import recovery_execution
    from yleum_api.services.generation.recovery_contract import ContinuationClaim

    @asynccontextmanager
    async def sessions():
        yield object()

    state = _state()
    owner = state.identity
    claim = ContinuationClaim(
        run_id=owner.run_id, token=uuid4(), attempt_seq=state.attempt_seq,
        worker_id="qa-worker", expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    state = state.model_copy(update={"continuation_claim": claim})
    driver = recovery_execution.ExecutionDriver(sessions, state, {}, AsyncMock())
    driver.soft_boundary = AsyncMock()
    driver.save = AsyncMock()
    driver.wait = AsyncMock(side_effect=RuntimeError("committed wait"))
    attempt = SimpleNamespace(
        run_id=owner.run_id, project_id=owner.project_id, user_id=owner.user_id,
        attempt_id=uuid4(), prompt_hash=owner.prompt_hash, request_sha256="a" * 64,
    )
    monkeypatch.setattr(recovery_execution, "_retained_completion_attempt", AsyncMock(
        return_value=attempt if finish is not None else None,
    ))
    reader = AsyncMock(return_value={"choices": [{"finish_reason": finish}]})
    monkeypatch.setattr(llm_client, "require_recovered_response", reader)
    error = llm_client.LLMError("filtered", code="provider_output_filtered")
    invoke = AsyncMock(side_effect=[TimeoutError("synthetic"), error] if after_timeout else error)
    frame = {
        "version": 1, "kind": "legacy",
        "value": {"attempt": 0, "call_deadline": datetime.now(UTC).isoformat()},
    }
    if finish == "content_filter":
        with pytest.raises(llm_client.LLMError) as raised:
            await driver.provider(frame, invoke)
        assert raised.value is error and error.provider_charge_ambiguous is False
        assert error.finish_reason == "content_filter"
        driver.wait.assert_not_awaited()
    else:
        with pytest.raises(RuntimeError, match="committed wait"):
            await driver.provider(frame, invoke)
        driver.wait.assert_awaited_once_with()
        assert error.provider_charge_ambiguous is None
    assert invoke.await_count == (2 if after_timeout and finish is not None else 1)
    if finish is not None:
        assert reader.await_args.args[0].attempt_id == attempt.attempt_id
        reader.assert_awaited_once()
    else:
        reader.assert_not_awaited()


@pytest.mark.parametrize("release_cell", [False, True])
async def test_operator_timeout_consumes_one_confirmed_qa_response(
    clock_owner, monkeypatch, release_cell,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pydantic import SecretStr

    from yleum_api.core import config
    from yleum_api.core.errors import ApiError
    from yleum_api.services import generation_deployment_drain
    from yleum_api.services.generation import recovery_diagnostic as diagnostic
    from yleum_api.services.generation import recovery_execution as execution

    c = clock_owner
    scope = {
        "run_id": c.run.id,
        "project_id": c.run.project_id,
        "user_id": c.run.user_id,
    }
    settings = config.get_settings().model_copy(update={
        "generation_recovery_mode": "canary",
        "generation_recovery_execution_enabled": True,
        "generation_provider_attempts_enabled": True,
        "generation_response_retention_enabled": True,
        "generation_recovery_canary_project_ids": str(c.run.project_id),
        "generation_response_signing_secret": SecretStr("synthetic-test-secret-" * 3),
    })
    monkeypatch.setattr(diagnostic, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(
        diagnostic, "_diagnostic_qa_owner", AsyncMock(return_value=True), raising=False,
    )
    await diagnostic.arm_diagnostic_response_timeout(c.session, **scope, release_cell=release_cell)
    await c.session.commit()

    retained = AsyncMock(return_value=None)
    monkeypatch.setattr(execution, "_retained_completion_attempt", retained, raising=False)
    consume = diagnostic.consume_diagnostic_response_timeout
    async with c.owned():
        with monkeypatch.context() as drain:
            drain.setattr(generation_deployment_drain, "require_generation_admission", AsyncMock(
                side_effect=ApiError("generation_draining", "Synthetic drain", 503),
            ))
            assert not await consume(c.factory, c.driver.state)
            retained.assert_not_awaited()
        assert not await consume(c.factory, c.driver.state)
        attempt = uuid4()
        retained.return_value = SimpleNamespace(attempt_id=attempt)
        assert await consume(c.factory, c.driver.state) == ("release" if release_cell else "replay")
        assert not await consume(c.factory, c.driver.state)
    await c.session.refresh(c.run)
    assert c.run.agent_state[diagnostic.TIMEOUT_KEY]["attempt_id"] == str(attempt)
    assert c.run.agent_state[diagnostic.TIMEOUT_KEY]["release_cell"] is release_cell
    assert execution.recovery_state(c.run) == c.driver.state


@pytest.mark.parametrize("repair_started", [False, True])
async def test_recovery_preserves_the_original_minimum_for_a_started_repair(
    monkeypatch, repair_started,
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from yleum_api.services import max_finalization

    @asynccontextmanager
    async def sessions():
        yield object()

    now = datetime.now(UTC)
    coordinator = object.__new__(max_finalization.MaxFinalizationCoordinator)
    coordinator.session_factory = sessions
    coordinator._locked_run = AsyncMock(return_value=SimpleNamespace(
        status="running", agent_state={}, started_at=now - timedelta(minutes=15),
    ))
    boundary = AsyncMock()
    coordinator.continuation = SimpleNamespace(
        state=_state(), soft_boundary=boundary, soft_edit_deadline=lambda value: value,
    )
    monkeypatch.setattr(
        max_finalization, "generation_deadline", lambda _: SimpleNamespace(at=now),
    )
    monkeypatch.setattr(
        max_finalization, "source_edit_deadline",
        lambda *args, **kwargs: now + timedelta(seconds=85),
    )
    repair = AsyncMock(return_value=True)
    args = dict(detail="test", repair=repair, repair_source=None, failure=None,
                repair_started=repair_started)
    if repair_started:
        assert await coordinator._invoke_source_repair(**args)
        repair.assert_awaited_once_with("test")
        boundary.assert_not_awaited()
    else:
        with pytest.raises(TimeoutError, match="before source repair"):
            await coordinator._invoke_source_repair(**args)
        repair.assert_not_awaited()
        boundary.assert_awaited_once_with(exhausted=True)


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


@pytest.mark.parametrize(
    "release", ["owned", "missing", "foreign", "pending", "active", "newer_fence", "old_binding"],
)
async def test_health_probes_only_the_current_owned_release_after_fence_advance(
    clock_owner, monkeypatch, release,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
    from yleum_api.services.generation import provider_health, recovery_dependencies
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        RecoveryBinding,
        decode_recovery,
    )
    from yleum_api.services.generation.recovery_execution import recovery_state
    from yleum_api.services.generation.recovery_quiescence import quiesced_recovery_run
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse

    c = clock_owner
    old = c.driver.state
    workspace = ProjectCellWorkspace(
        project_id=c.run.project_id, owner_id=c.run.user_id, provider="synthetic",
        state="ready", fencing_epoch=5 if release == "newer_fence" else 4,
        generation_run_id=c.run.id if release in {"pending", "active"} else None,
    )
    c.session.add(workspace)
    await c.session.flush()
    receipt = ProjectCellResourceResponse(
        workspace_id=uuid4() if release == "foreign" else workspace.id,
        state="resources_ready", provider_ref="owned", fencing_epoch=4,
        checkpoint_ref=None,
        has_workspace=True, has_agent_home=True, has_postgres=True, has_redis=True,
    )
    operation_id = uuid4()
    if release != "missing":
        c.session.add(ProjectCellOperation(
            id=operation_id, workspace_id=workspace.id, generation_run_id=c.run.id,
            kind="release", status="completed", fencing_epoch=4, finished_at=datetime.now(UTC),
            idempotency_key=str(uuid4()), request_digest="a" * 64,
            result_payload=receipt.to_wire_json(),
        ))
    ref = CheckpointRef(object_key="qa-retained", sha256="d" * 64)
    binding = RecoveryBinding(
        workspace_id=str(workspace.id), fencing_epoch=2 if release == "old_binding" else 3,
        source_sha256="a" * 64, schema_sha256="b" * 64, environment_sha256="c" * 64,
    )
    physical_state = {"pending": "release_pending", "active": "active"}.get(release, "released")
    now = datetime.now(UTC)
    state = decode_recovery(old.model_copy(update={
        "cursor": "finalization", "binding": binding, "checkpoint": ref, "context_ref": ref,
        "phase": "waiting_dependency", "next_check_at": now - timedelta(seconds=1),
        "clock": old.clock.model_copy(update={
            "interval_open": False, "wait_dependency": "credentials", "dependency_failures": 1,
        }),
        "execution": old.execution.model_copy(update={
            "stage": "finalization", "substep": "checkpoint_ready", "execution_context_ref": ref,
            "physical_state": physical_state,
            "release_operation_id": None if release == "active" else operation_id,
            "control_workspace_id": workspace.id,
            "control_fencing_epoch": 3 if release in {"pending", "active"} else 4,
        }),
    }).model_dump(mode="json"))
    c.run.agent_state = {**c.run.agent_state, "recovery_v1": state.model_dump(mode="json")}
    await c.session.commit()

    probe = AsyncMock(return_value=SimpleNamespace(
        available_for=lambda dependency: dependency == "credentials",
    ))
    monkeypatch.setattr(provider_health, "free_health_for_run", probe)
    ready = release == "owned"
    assert await recovery_dependencies.poll_due_dependency(c.factory, c.run.id) is ready
    assert await recovery_dependencies.poll_due_dependency(c.factory, c.run.id) is ready
    async with c.factory() as session:
        run = await session.get(GenerationRun, c.run.id)
        saved = recovery_state(run)
        assert await quiesced_recovery_run(
            session, run, release_sha="a" * 40, snapshot_digest="b" * 64,
        ) is None
    if ready:
        probe.assert_awaited_once_with(
            run_id=c.run.id, project_id=c.run.project_id, user_id=c.run.user_id,
            dependency="credentials",
        )
        assert saved.state_revision == state.state_revision + 1
        assert saved.next_check_at >= now and saved.clock.wait_dependency is None
        assert saved.binding == binding and saved.execution == state.execution
        assert saved.active_work_ms == state.active_work_ms
    else:
        probe.assert_not_awaited()
        assert saved == state


@pytest.mark.parametrize("dispatched", [False, True])
async def test_parked_worker_finishes_its_strict_release_without_resuming_generation(
    clock_owner, monkeypatch, dispatched,
):
    from unittest.mock import AsyncMock

    from sqlalchemy import func, select

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project import Project
    from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
    from yleum_api.services import project_cell_lifecycle
    from yleum_api.services.generation import recovery_execution
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        RecoveryBinding,
        decode_recovery,
    )
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
    from yleum_api.services.project_cells import (
        claim_cell_operation_committed,
        execution_run_id,
        mark_cell_operation_indeterminate,
        reserve_cell_operation,
    )
    from yleum_api.workers import generation

    c = clock_owner
    project = await c.session.get(Project, c.run.project_id)
    project.template = "max_miniapp"
    c.run.execution_backend = "worker"
    c.run.execution_started_at = datetime.now(UTC)
    workspace = ProjectCellWorkspace(
        project_id=c.run.project_id, owner_id=c.run.user_id, provider="synthetic",
        state="ready", generation_run_id=c.run.id, fencing_epoch=3,
    )
    c.session.add(workspace)
    await c.session.flush()
    ref = CheckpointRef(object_key="qa-retained", sha256="d" * 64)
    binding = RecoveryBinding(
        workspace_id=str(workspace.id), fencing_epoch=3,
        source_sha256="a" * 64, schema_sha256="b" * 64, environment_sha256="c" * 64,
    )
    proof = {
        "source_sha256": binding.source_sha256,
        "environment_sha256": binding.environment_sha256,
    }
    operation, _ = await reserve_cell_operation(
        c.session, workspace_id=workspace.id, generation_run_id=c.run.id, kind="release",
        idempotency_key=f"generation:{c.run.id}:release:profile:cycle:0",
        request={"require_environment_proof": proof},
    )
    old = c.driver.state
    state = decode_recovery(old.model_copy(update={
        "cursor": "finalization", "binding": binding, "checkpoint": ref, "context_ref": ref,
        "phase": "needs_input", "next_check_at": None,
        "execution": old.execution.model_copy(update={
            "stage": "finalization", "substep": "checkpoint_ready", "execution_context_ref": ref,
            "physical_state": "release_pending", "release_operation_id": operation.id,
            "control_workspace_id": workspace.id, "control_fencing_epoch": 3,
        }),
    }).model_dump(mode="json"))
    c.run.agent_state = {**c.run.agent_state, "recovery_v1": state.model_dump(mode="json")}
    await c.session.commit()
    if dispatched:
        token = execution_run_id.set(c.run.id)
        try:
            first = await claim_cell_operation_committed(c.factory, operation.id)
        finally:
            execution_run_id.reset(token)
        assert first.fencing_epoch == 4
        async with c.factory() as session:
            await mark_cell_operation_indeterminate(session, operation.id, "lost release response")
            await session.commit()

    async def controller(_client, method_name, request):
        assert method_name == "control"
        assert request.operation_id == operation.id
        assert request.request_digest == operation.request_digest
        assert request.workspace_id == workspace.id and request.fencing_epoch == 4
        assert request.require_environment_proof.model_dump(mode="json") == proof
        return ProjectCellResourceResponse(
            workspace_id=workspace.id, state="resources_ready", provider_ref="owned",
            fencing_epoch=4, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
            has_postgres=True, has_redis=True,
        )

    wire = AsyncMock(side_effect=controller)
    resume = AsyncMock(side_effect=AssertionError("release-only work must not resume generation"))
    monkeypatch.setattr(project_cell_lifecycle, "_invoke_client", wire)
    monkeypatch.setattr(recovery_execution, "resume_generation", resume)
    monkeypatch.setattr(generation, "get_engine", lambda: c.engine)
    async with c.factory() as session:
        _, _, due = await generation._load_dispatch_scan(session, [])
        assert len(due) == 1 and due[0].run_id == c.run.id and due[0].recovery_release
    assert await generation.execute_dispatch(c.run.id)
    assert not await generation.execute_dispatch(c.run.id)
    wire.assert_awaited_once()
    resume.assert_not_awaited()
    async with c.factory() as session:
        run = await session.get(GenerationRun, c.run.id)
        assert run.status == "running"
        saved = recovery_execution.recovery_state(run)
        assert saved.phase == "needs_input" and saved.attempt_seq == state.attempt_seq
        assert saved.binding == binding and saved.clock == state.clock
        assert saved.execution.physical_state == "released"
        assert saved.execution.control_fencing_epoch == 4
        assert saved.execution.release_operation_id == operation.id
        workspace_after = await session.get(ProjectCellWorkspace, workspace.id)
        assert workspace_after.generation_run_id is None and workspace_after.fencing_epoch == 4
        operation_after = await session.get(ProjectCellOperation, operation.id)
        assert operation_after.status == "completed"
        assert operation_after.attempt_count == 1 + int(dispatched)
        assert await session.scalar(select(func.count()).select_from(ProjectCellOperation)) == 1


async def test_owned_stopped_clock_can_settle_while_deploy_admission_is_closed(clock_owner):
    from yleum_api.core.errors import ApiError
    from yleum_api.services.generation.recovery_execution import GenerationYielded
    from yleum_api.services.generation_deployment_drain import begin_drain, drain_status

    c = clock_owner
    release = "a" * 40
    async with c.owned():
        with pytest.raises(GenerationYielded):
            await c.driver.wait()
        async with c.factory() as session:
            await begin_drain(session, release)
            await session.commit()
        with pytest.raises(ApiError) as blocked:
            await c.driver.account_work()
        assert blocked.value.code == "generation_draining"
        await c.driver.account_work(stopped=True)
        assert not c.driver.state.clock.interval_open
    async with c.factory() as session:
        assert (await drain_status(session))["release_sha"] == release


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
    detail = "src/app/page.tsx(5,3): error TS2554: expected an argument"
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


@pytest.mark.parametrize(
    "damage", [
        None, "draft_changed", "fence_changed", "wrong_control", "wrong_stop", "wrong_gap",
        "source_time_left", "only_85_seconds",
    ],
)
async def test_managed_source_gap_can_open_only_one_owned_repair_window(
    proof_clock, monkeypatch, damage,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.test_max_generation_contract import _portable_crm_files
    from tests.test_max_management_generation import connection_files
    from yleum_api.models.project import Project
    from yleum_api.services import max_generation_contract
    from yleum_api.services.agent_builder import AgentResult
    from yleum_api.services.generation import recovery_store
    from yleum_api.services.generation.continuation import SourceNoveltyTracker
    from yleum_api.services.generation.recovery_clock import grant_window
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        IncompleteResponseBudget,
        decode_recovery,
    )
    from yleum_api.services.generation.recovery_pipeline import (
        CompletedAgentTurn,
        FinalizationProgress,
    )
    from yleum_api.services.max_management_generation import (
        ManagedSourceGap,
        ManagedSourceObligation,
        management_source_advisory,
        management_source_gap,
    )
    from yleum_api.services.promotion_permit import workspace_revision_digest

    c, p = proof_clock.owner, proof_clock
    project = await c.session.get(Project, c.run.project_id)
    project.template = "max_miniapp"
    prompt = "Собери приложение"
    files = connection_files("", ("catalog",))
    files[".omnia/cell.json"] = _portable_crm_files()[".omnia/cell.json"]
    files.pop("src/app/page.tsx")
    files["src/app/api/catalog/route.ts"] = "export const GET = () => Response.json([])"
    # The live gate no longer interprets SDK source. Retain the historical
    # rejection explicitly to cover one-time grants and their ownership guards.
    assert management_source_gap(files) is None
    gap = management_source_advisory(files)
    assert isinstance(gap, ManagedSourceGap)
    assert gap.obligation is ManagedSourceObligation.PROFILE_FIELD

    def historical_feedback(actual_prompt, candidate, *, portable):
        assert actual_prompt == prompt and candidate == files and portable is True
        return gap

    monkeypatch.setattr(max_generation_contract, "max_source_completion_gap", historical_feedback)
    binding = p.binding.model_copy(update={"source_sha256": workspace_revision_digest(files)})
    ref = CheckpointRef(object_key="retained", sha256="c" * 64)
    clock = c.driver.state.clock
    if damage in {"source_time_left", "only_85_seconds"}:
        seconds = 385 if damage == "only_85_seconds" else 480
        clock = clock.model_copy(update={
            "segment_deadline": datetime.now(UTC) + timedelta(seconds=seconds),
        })
    state = c.driver.state.model_copy(update={
        "cursor": "finalization",
        "binding": binding,
        "checkpoint": ref,
        "context_ref": ref,
        "clock": clock,
        "execution": c.driver.state.execution.model_copy(update={
            "stage": "finalization", "substep": "checkpoint_ready",
            "execution_context_ref": ref, "physical_state": "active",
            "control_workspace_id": p.workspace.id,
            "control_fencing_epoch": p.workspace.fencing_epoch,
        }),
    })
    c.driver.state = decode_recovery(state.model_dump(mode="json"))
    c.run.agent_state = {**c.run.agent_state, "recovery_v1": state.model_dump(mode="json")}
    control_fence = p.workspace.fencing_epoch - int(damage == "wrong_control")
    c.driver.runtime_control = (p.workspace.id, control_fence)
    detail = "runtime dependency unavailable" if damage == "wrong_gap" else str(gap)
    stop = "max_steps" if damage == "wrong_stop" else "source_deadline"
    turn = CompletedAgentTurn(AgentResult(False, "", files, 17, stop_reason=stop),
                              None, IncompleteResponseBudget())
    progress = FinalizationProgress(
        source_history=SourceNoveltyTracker(files).source_history(), feedback=(detail,),
        pending_detail=detail, pending_fast_check=True,
    )
    c.driver.context = {"pipeline": {"prompt_text": prompt}, "completed": {
        "agent_turn": turn.to_json(), "finalization_progress": progress.model_dump(mode="json"),
    }}
    snapshot_files = (
        {**files, "src/app/globals.css": "main {}"} if damage == "draft_changed" else files
    )
    c.driver.snapshot = AsyncMock(return_value=(binding, snapshot_files))
    physical = SimpleNamespace(
        workspace_revision=binding.source_sha256,
        schema_data_digest=binding.schema_sha256,
        environment_digest=binding.environment_sha256,
    )
    monkeypatch.setattr(
        recovery_store, "project_cell_agent_identity", AsyncMock(return_value=physical),
    )
    if damage == "fence_changed":
        p.workspace.fencing_epoch += 1
    await c.session.commit()

    budget = c.driver.state.execution.budget
    eligible = damage in {None, "only_85_seconds"}
    async with c.owned():
        if damage == "fence_changed":
            with pytest.raises(ValueError, match="fence conflict"):
                await grant_window(c.driver, managed_source_gap=detail)
        else:
            assert await grant_window(c.driver, managed_source_gap=detail) is eligible
        if eligible:
            assert c.driver.state.clock.stage == "repair"
            assert c.driver.state.clock.segment_seq == 1
            assert not await grant_window(c.driver, managed_source_gap=detail)
        else:
            assert not c.driver.state.clock.repair_granted
            assert c.driver.state.clock.segment_seq == 0
    assert c.driver.state.execution.budget == budget
    assert c.driver.state.identity.run_id == c.run.id


@pytest.mark.parametrize("stage, fault", [
    ("edit", None), ("repair", None), ("edit", "no_red"), ("edit", "no_green"),
    ("edit", "old_red"), ("repair", "old_red"), ("edit", "foreign_green"),
    ("edit", "nonterminal_red"), ("repair", "nonterminal_green"), ("edit", "config_changed"),
    ("edit", "same_source"), ("edit", "active_window"), ("edit", "wrong_current_source"),
    ("edit", "missing_behavior"), ("edit", "tampered_behavior"), ("edit", "resume"),
    ("edit", "mutating_red"), ("edit", "infra_red"), ("edit", "unclassified_red"),
])
async def test_compiler_progress_grants_one_current_source_window(
    proof_clock, monkeypatch, stage, fault,
):
    import hashlib
    from dataclasses import asdict, replace

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project_cell import (
        ProjectCellActivityLease,
        ProjectCellProof,
        ProjectCellProofResult,
    )
    from yleum_api.services import max_behavior_proof as behavior
    from yleum_api.services.build_spec import FidelitySpec
    from yleum_api.services.generation import recovery_requirements
    from yleum_api.services.generation.recovery_clock import grant_window
    from yleum_api.services.generation.recovery_execution import (
        ExecutionDriver,
        GenerationYielded,
        claim_continuation,
        recovery_state,
    )
    from yleum_api.services.orchestrator_client import ProjectCellWorkspaceIdentity
    from yleum_api.services.project_cell_proofs import ProofDimension

    p = proof_clock
    c = p.owner
    now = datetime.now(UTC)
    expiry = now + timedelta(minutes=20) if fault == "active_window" else (
        now - timedelta(seconds=1)
    )
    red_age = 30 if fault == "old_red" else 2
    red_at = now - timedelta(minutes=red_age)
    p.lease.started_at = p.lease.finished_at = red_at
    p.result.created_at = red_at
    if fault == "no_red":
        await c.session.delete(p.result)
    if fault == "nonterminal_red":
        p.lease.finished_at = None
    if fault == "mutating_red":
        p.result.redacted_detail = "command changed the frozen proof identity\nfixture"
        p.result.detail_digest = hashlib.sha256(p.result.redacted_detail.encode()).hexdigest()
    if fault == "infra_red":
        p.result.redacted_detail = "Build failed: ECONNRESET"
        p.result.detail_digest = hashlib.sha256(p.result.redacted_detail.encode()).hexdigest()
    if fault == "unclassified_red":
        p.result.redacted_detail = "Build failed for an unknown reason"
        p.result.detail_digest = hashlib.sha256(p.result.redacted_detail.encode()).hexdigest()
    green = replace(
        p.identity,
        workspace_revision=p.identity.workspace_revision if fault == "same_source" else "f" * 64,
        fencing_epoch=8 if fault == "foreign_green" else p.identity.fencing_epoch,
        dependency_digest="1" * 64 if fault == "config_changed" else p.identity.dependency_digest,
    )
    # A real journal permits only one terminal receipt for exact source/scope.
    # With the same source, use one RED receipt and no eligible newer GREEN.
    proof = ProjectCellProof(id=uuid4(), **asdict(green), proof_key=green.proof_key)
    lease = ProjectCellActivityLease(
        operation_id=uuid4(), workspace_id=p.workspace.id, generation_run_id=c.run.id,
        kind="command", state="completed", fencing_epoch=green.fencing_epoch,
        proof_key=green.proof_key, phase="fast_check", started_at=now, heartbeat_at=now,
        finished_at=None if fault == "nonterminal_green" else now,
        deadline_at=now + timedelta(minutes=1), log_bytes=0,
    )
    result = ProjectCellProofResult(
        id=uuid4(), proof_id=proof.id, workspace_id=p.workspace.id, dimension="fast_check",
        dimension_key=green.dimension_key(ProofDimension.FAST_CHECK), outcome="green",
        operation_id=lease.operation_id, artifact_ref=None,
        detail_digest=hashlib.sha256(b"").hexdigest(), redacted_detail="", created_at=now,
    )
    if fault not in {"no_green", "same_source"}:
        c.session.add_all([proof, lease, result])
    before = c.driver.state
    before = before.model_copy(update={
        "binding": p.binding.model_copy(update={"source_sha256": green.workspace_revision}),
        "clock": before.clock.model_copy(update={
            "stage": stage, "segment_deadline": expiry, "repair_granted": stage == "repair",
        }),
    })
    if fault in {"missing_behavior", "tampered_behavior"}:
        named = behavior.required_contract("task_density_v1", template="max_miniapp")
        c.run.prompt_hash = named.request_sha256
        recovery_requirements.store_frozen_requirements(
            c.run, "task_density_v1", FidelitySpec(), named,
        )
        if fault == "tampered_behavior":
            c.run.agent_state = {
                **c.run.agent_state, behavior.RECEIPT_KEY: {"receipt_digest": "bad"},
            }
        before = before.model_copy(update={
            "identity": before.identity.model_copy(update={"prompt_hash": c.run.prompt_hash}),
            "requirements": recovery_requirements.frozen_requirements(c.run),
        })
    c.driver.state = before
    c.run.agent_state = {**c.run.agent_state, "recovery_v1": before.model_dump(mode="json")}
    await c.session.commit()

    async def active_identity(*_args, **_kwargs):
        physical_source = "0" * 64 if fault == "wrong_current_source" else green.workspace_revision
        return ProjectCellWorkspaceIdentity(
            physical_source, green.dependency_digest, green.schema_data_digest,
            green.cell_manifest_digest, green.base_image_digest, green.build_config_digest,
        )

    monkeypatch.setattr(recovery_requirements, "project_cell_agent_identity", active_identity)
    async with c.owned():
        if fault in {"wrong_current_source", "tampered_behavior"}:
            with pytest.raises(ValueError):
                await grant_window(c.driver)
            assert not c.driver.state.clock.compiler_granted
            return
        allowed = fault in {None, "missing_behavior", "resume"}
        if fault == "resume":
            with pytest.raises(GenerationYielded) as yielded:
                await c.driver.soft_boundary(exhausted=True)
            assert yielded.value.run_id == c.run.id
            assert c.driver.state.phase == "waiting_dependency"
            await _release_clock_workspace(c, p.workspace, monkeypatch, yielded.value)
            stopped = c.driver.state
            assert not stopped.clock.interval_open
            async with c.factory() as session:
                claim = await claim_continuation(
                    session, run_id=c.run.id, expected_attempt=0, worker_id="clock-restart",
                    now=datetime.now(UTC) + timedelta(minutes=1),
                )
                assert claim is not None and claim.run_id == c.run.id
                await session.commit()
            async with c.factory() as session:
                resumed = recovery_state(await session.get(GenerationRun, c.run.id))
            assert resumed.clock.segment_used_ms == stopped.clock.segment_used_ms
            assert resumed.clock.segment_used_ms < resumed.clock.window_ms
            assert resumed.clock.segment_seq == 1 and resumed.clock.compiler_granted
            assert resumed.execution.budget == before.execution.budget
            return
        assert await grant_window(c.driver) == allowed
        if not allowed:
            assert c.driver.state.clock.segment_seq == before.clock.segment_seq
            assert not c.driver.state.clock.compiler_granted
            return
        assert c.driver.state.identity == before.identity
        assert c.driver.state.execution.budget == before.execution.budget
        assert c.driver.state.clock.stage == stage
        assert c.driver.state.clock.segment_seq == before.clock.segment_seq + 1
        assert c.driver.state.clock.segment_deadline > now
        assert c.driver.state.clock.compiler_granted
        async with c.factory() as session:
            persisted = recovery_state(await session.get(GenerationRun, c.run.id))
        assert persisted.clock.compiler_granted
        cold = ExecutionDriver(c.factory, persisted, {}, c.driver.snapshot)
        if fault == "missing_behavior":
            with pytest.raises(ValueError, match="semantic receipt unavailable"):
                await grant_window(cold)
        else:
            assert not await grant_window(cold)
        assert cold.state.clock.segment_seq == before.clock.segment_seq + 1


async def _release_clock_workspace(c, workspace, monkeypatch, primary_error):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from yleum_api.services import project_cell_executor as cell
    from yleum_api.services.generation import lifecycle
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
    from yleum_api.services.project_cells import (
        claim_cell_operation_committed,
        complete_cell_operation,
    )

    # Only the controller's external response is synthetic. Release ownership,
    # persistence and quiescence use the production SQL code paths.
    async def controller_release(factory, operation_id, _client):
        claimed = await claim_cell_operation_committed(factory, operation_id)
        response = ProjectCellResourceResponse(
            workspace_id=claimed.workspace_id, state="resources_ready", provider_ref="clock-test",
            fencing_epoch=claimed.fencing_epoch, checkpoint_ref=None, has_workspace=True,
            has_agent_home=True, has_postgres=True, has_redis=True,
        )
        async with factory() as session:
            await complete_cell_operation(session, operation_id, response.to_wire_json())
            await session.commit()
        return SimpleNamespace(status="completed", response=response)

    async def release():
        await cell._release_generation_lease(
            session_factory=c.factory, workspace_id=workspace.id, generation_run_id=c.run.id,
            profile_version="profile-v1",
        )

    monkeypatch.setattr(cell, "execute_cell_operation", controller_release)
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())
    runtime = SimpleNamespace(
        deadline_task=None, handle=SimpleNamespace(release=release), continuation=c.driver,
    )
    await lifecycle.cleanup_generation_runtime(
        runtime, c.run.project_id, c.run.assistant_message_id, primary_error=primary_error,
    )


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


@pytest.mark.parametrize("edge", [None, "partial_tool", "early_check", "transcript_rejection"])
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

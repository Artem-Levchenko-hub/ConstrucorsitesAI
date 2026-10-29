"""Deployment classification preserves saved work and requires physical evidence."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_generation_recovery_execution import _bootstrap_wait
from tests.test_generation_worker import _queued_dispatch
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.services import generation_deployment_drain as drain
from yleum_api.services.generation import recovery_execution, recovery_readers
from yleum_api.services.generation.recovery_contract import RecoveryContractError


def release_capture():
    return {
        "version": 1,
        "release_sha": "a" * 40,
        "owner_nonce": str(uuid4()),
        "core_capture_sha256": "b" * 64,
        "commerce_capture_sha256": "c" * 64,
    }


@pytest.mark.parametrize("fault", [
    "malformed", "missing", "extra", "duplicate", "unknown", "bool", "target",
    "nonce", "hash", "null", "oversize",
])
async def test_resume_cli_rejects_invalid_context_before_database(monkeypatch, fault):
    import io
    import json
    import sys
    from unittest.mock import Mock

    from yleum_api.core import db

    context = release_capture()
    if fault == "missing":
        context.pop("commerce_capture_sha256")
    elif fault == "extra":
        context["trusted"] = True
    elif fault in {"unknown", "bool"}:
        context["version"] = 2 if fault == "unknown" else True
    elif fault == "target":
        context["release_sha"] = "f" * 40
    elif fault == "nonce":
        context["owner_nonce"] = "{" + context["owner_nonce"] + "}"
    elif fault == "hash":
        context["core_capture_sha256"] = "B" * 64
    elif fault == "null":
        context["owner_nonce"] = None
    raw = json.dumps(context)
    if fault == "duplicate":
        raw = raw[:-1] + ', "version": 1}'
    elif fault == "malformed":
        raw = "{"
    elif fault == "oversize":
        raw = " " * 4097 + raw
    engine = Mock(side_effect=AssertionError("invalid CLI input accessed database"))
    monkeypatch.setattr(db, "get_engine", engine)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    with pytest.raises(ValueError):
        await drain._command("resume-check", "a" * 40)
    engine.assert_not_called()


@pytest.mark.parametrize("existing", [None, "f" * 40])
async def test_resume_requires_existing_exact_drain(db_session, test_engine, existing):
    from yleum_api.models.deployment_drain import DeploymentDrain

    if existing:
        await drain.begin_drain(db_session, existing)
        await db_session.commit()
    context = drain.ReleaseCaptureContext(**release_capture())
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as checking:
        with pytest.raises(ValueError):
            await drain._resume_classification(checking, context)
    row = await db_session.get(DeploymentDrain, "generation")
    assert (row.release_sha if row else None) == existing


@pytest.mark.parametrize("fault", [
    "new_unsafe", "corrupt", "unknown", "owner", "session", "release", "receiver",
    "operation", "lease", "restoration",
])
async def test_resume_classifies_all_current_blockers(db_session, test_engine, fault):
    from yleum_api.models.deployment_drain import DeploymentDrain
    from yleum_api.models.project import Project
    from yleum_api.models.project_cell import ProjectCellActivityLease
    from yleum_api.models.restoration import Restoration

    run, workspace, release = await released_wait(db_session)
    raw = dict(run.agent_state["recovery_v1"])
    expected = "generations"
    if fault == "new_unsafe":
        await _queued_dispatch(db_session)
    elif fault in {"corrupt", "unknown"}:
        raw = None if fault == "corrupt" else {"version": 999}
        run.agent_state = {**run.agent_state, "recovery_v1": raw}
    elif fault == "owner":
        other = await _queued_dispatch(db_session)
        other.status = "completed"
        project = await db_session.get(Project, run.project_id)
        project.owner_id = other.user_id
    elif fault == "release":
        release.generation_run_id = None
    elif fault == "receiver":
        db_session.add(GenerationProviderAttempt(
            attempt_id=uuid4(), run_id=run.id, project_id=run.project_id,
            user_id=run.user_id, prompt_hash=run.prompt_hash, request_sha256="a" * 64,
            dispatch_token=uuid4(), phase="pending",
        ))
    elif fault == "operation":
        release.status = "running"
        expected = "operations"
    elif fault == "lease":
        db_session.add(ProjectCellActivityLease(
            workspace_id=workspace.id, generation_run_id=run.id,
            operation_id=uuid4(), fencing_epoch=workspace.fencing_epoch,
            kind="command", started_at=datetime.now(UTC), deadline_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
        ))
        expected = "leases"
    elif fault == "restoration":
        db_session.add(Restoration(
            id=uuid4(), project_id=run.project_id, owner_id=run.user_id,
            workspace_id=workspace.id, source_version_id=uuid4(), source_snapshot_id=uuid4(),
            base_draft_snapshot_id=uuid4(), target_commit_sha="a" * 40, base_commit_sha="b" * 40,
            idempotency_key=str(uuid4()), request_digest="d" * 64, fencing_epoch=5,
        ))
        expected = "restorations"
    await db_session.commit()
    await drain.begin_drain(db_session, "a" * 40)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with test_engine.connect() as owner, factory() as checking:
        if fault == "session":
            await owner.execute(
                text("SELECT pg_advisory_lock(1195724366,:key)"),
                {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
            )
            await owner.commit()
        try:
            result = await drain._resume_classification(
                checking, drain.ReleaseCaptureContext(**release_capture()),
            )
            assert result.active[expected] > 0 and not result.drained
            assert await checking.scalar(text("SHOW transaction_read_only")) == "on"
        finally:
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()
    await db_session.refresh(run)
    assert run.agent_state["recovery_v1"] == raw and run.status == "running"
    assert await db_session.get(DeploymentDrain, "generation") is not None


async def test_resume_lock_keeps_admission_fenced_until_checks_finish(
    db_session, test_engine, monkeypatch,
):
    from yleum_api.core.errors import ApiError
    from yleum_api.services.generation import recovery_quiescence

    run, _, _ = await released_wait(db_session)
    await drain.begin_drain(db_session, "a" * 40)
    await db_session.commit()
    entered, finish = asyncio.Event(), asyncio.Event()
    classify = recovery_quiescence._quiesced_physical_run

    async def paused(*args):
        entered.set()
        await finish.wait()
        return await classify(*args)

    monkeypatch.setattr(recovery_quiescence, "_quiesced_physical_run", paused)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as checking, factory() as entering:
        task = asyncio.create_task(drain._resume_classification(
            checking, drain.ReleaseCaptureContext(**release_capture()),
        ))
        await asyncio.wait_for(entered.wait(), 2)
        admission = asyncio.create_task(drain.require_generation_admission(entering))
        try:
            await asyncio.sleep(0.05)
            assert not admission.done()
            finish.set()
            result = await task
            assert result.drained
            assert not admission.done()  # same lock persists through the whole read transaction
            await checking.rollback()
            with pytest.raises(ApiError, match="temporarily paused"):
                await asyncio.wait_for(admission, 2)
        finally:
            finish.set()
            await checking.rollback()
            for pending in (task, admission):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, admission, return_exceptions=True)
    await db_session.refresh(run)
    assert run.status == "running" and run.finished_at is None


@pytest.mark.parametrize("new_unsafe", [False, True])
async def test_resume_cli_classifies_current_wait_without_claiming_reader_snapshot(
    db_session, test_engine, monkeypatch, capsys, new_unsafe,
):
    import io
    import json
    import sys

    from yleum_api.core import db
    from yleum_api.models.deployment_drain import DeploymentDrain

    run = await _queued_dispatch(db_session)
    run.status = "running"
    raw = _bootstrap_wait(run)
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    if new_unsafe:
        await _queued_dispatch(db_session)
    await db_session.commit()
    await drain.begin_drain(db_session, "a" * 40)
    await db_session.commit()
    context = release_capture()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(context)))
    monkeypatch.setattr(db, "get_engine", lambda: test_engine)
    monkeypatch.setattr(db, "dispose_engine", AsyncMock())
    # Partial replacement has no compatible CURRENT snapshot. The release-only
    # classification must not manufacture one or weaken normal/end gates.
    gate = AsyncMock(side_effect=RecoveryContractError("mixed current inventory"))
    monkeypatch.setattr(drain, "require_recovery_readers", gate)
    if new_unsafe:
        with pytest.raises(SystemExit) as stopped:
            await drain._command("resume-check", "a" * 40)
        assert stopped.value.code == 75
    else:
        await drain._command("resume-check", "a" * 40)
    result = json.loads(capsys.readouterr().out)
    assert result == {
        **context,
        "basis": "owned_release_capture",
        "active": {
            "generations": int(new_unsafe), "operations": 0, "leases": 0, "restorations": 0,
        },
        "drained": not new_unsafe,
    }
    gate.assert_not_awaited()
    assert await db_session.get(DeploymentDrain, "generation") is not None
    assert not (await drain.drain_status(db_session))["drained"]
    with pytest.raises(RecoveryContractError):
        await drain.end_drain(db_session, "a" * 40)
    await db_session.rollback()
    await db_session.refresh(run)
    assert run.agent_state["recovery_v1"] == raw


@pytest.mark.parametrize("case", ["safe_bootstrap", "unknown_contract", "provider_pending"])
async def test_waiting_run_is_not_blanket_excluded_from_deployment(db_session, monkeypatch, case):
    run = await _queued_dispatch(db_session)
    run.status = "running"
    raw = _bootstrap_wait(run)
    if case == "unknown_contract":
        raw = {"version": 999, "opaque": "preserve exact bytes"}
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    if case == "provider_pending":
        db_session.add(
            GenerationProviderAttempt(
                attempt_id=uuid4(),
                run_id=run.id,
                project_id=run.project_id,
                user_id=run.user_id,
                prompt_hash=run.prompt_hash,
                request_sha256="a" * 64,
                dispatch_token=uuid4(),
                phase="pending",
            )
        )
    await db_session.commit()
    before = run.agent_state
    # This test isolates SQL/physical classification; the independent snapshot
    # consumer has its own complete-inventory and own-incarnation falsifiers.
    readers = AsyncMock(return_value="a" * 40)
    monkeypatch.setattr(drain, "require_recovery_readers", readers, raising=False)
    await drain.begin_drain(db_session, "a" * 40)
    await db_session.commit()
    result = await drain.drain_status(db_session)
    assert result["drained"] is (case == "safe_bootstrap")
    assert result["active"]["generations"] == (0 if case == "safe_bootstrap" else 1)
    await db_session.refresh(run)
    assert run.agent_state == before and run.status == "running" and run.finished_at is None


async def released_wait(session):
    from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse

    run = await _queued_dispatch(session)
    run.status = "running"
    workspace = ProjectCellWorkspace(
        id=uuid4(),
        project_id=run.project_id,
        owner_id=run.user_id,
        provider="synthetic",
        state="ready",
        fencing_epoch=5,
    )
    session.add(workspace)
    await session.flush()
    release = ProjectCellOperation(
        id=uuid4(),
        workspace_id=workspace.id,
        generation_run_id=run.id,
        kind="release",
        status="completed",
        fencing_epoch=5,
        finished_at=datetime.now(UTC),
        idempotency_key=str(uuid4()),
        request_digest="a" * 64,
        result_payload=ProjectCellResourceResponse(
            workspace_id=workspace.id,
            state="resources_ready",
            provider_ref="owned",
            fencing_epoch=5,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        ).to_wire_json(),
    )
    session.add(release)
    raw = _bootstrap_wait(run)
    ref = {"object_key": "owned/checkpoint", "sha256": "a" * 64}
    raw.update(
        cursor="agent",
        binding={
            "workspace_id": str(workspace.id),
            "fencing_epoch": 5,
            "source_sha256": "a" * 64,
            "schema_sha256": "b" * 64,
            "environment_sha256": "c" * 64,
        },
        checkpoint=ref,
        context_ref=ref,
    )
    raw["execution"].update(
        stage="agent",
        substep="proof_wait",
        logical_step_seq=1,
        physical_state="released",
        release_operation_id=str(release.id),
        control_workspace_id=str(workspace.id),
        control_fencing_epoch=5,
        execution_context_ref=ref,
    )
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    assert recovery_execution.recovery_state(run) is not None
    await session.commit()
    return run, workspace, release


@pytest.mark.parametrize(
    "case",
    [
        "released",
        "unknown_external_result",
        "missing_release",
        "old_fence",
        "foreign_run",
        "still_owned",
        "physical_active",
        "unknown_git",
        "session_live",
        "reader_missing",
    ],
)
async def test_real_release_receipt_not_timeout_classifies_wait(
    db_session,
    test_engine,
    monkeypatch,
    case,
):
    run, workspace, release = await released_wait(db_session)
    raw = dict(run.agent_state["recovery_v1"])
    if case == "unknown_external_result":
        # An unresolved result is NOT physical activity once its owning writer
        # is independently confirmed released; the result remains unresolved.
        raw["unresolved_operation_ids"] = [str(uuid4())]
    elif case == "missing_release":
        raw["execution"] = {**raw["execution"], "release_operation_id": str(uuid4())}
    elif case == "old_fence":
        workspace.fencing_epoch += 1
    elif case == "foreign_run":
        release.generation_run_id = None
    elif case == "still_owned":
        workspace.generation_run_id = run.id
    elif case == "physical_active":
        raw["execution"] = {**raw["execution"], "physical_state": "active"}
    elif case == "unknown_git":
        raw["cursor"] = "finalization"
        raw["execution"] = {
            **raw["execution"],
            "stage": "finalization",
            "substep": "publication_pending",
            "pending_effect_id": str(uuid4()),
        }
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()
    gate = AsyncMock(return_value="d" * 64)
    if case == "reader_missing":
        gate.side_effect = RecoveryContractError("reader missing")
    monkeypatch.setattr(drain, "require_recovery_readers", gate)
    await drain.begin_drain(db_session, "a" * 40)
    await db_session.commit()
    async with test_engine.connect() as owner:
        if case == "session_live":
            await owner.execute(
                text("SELECT pg_advisory_lock(1195724366,:key)"),
                {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
            )
            await owner.commit()
        result = await drain.drain_status(db_session)
        assert result["drained"] is (case in {"released", "unknown_external_result"})
        assert run.agent_state["recovery_v1"] == raw
        await owner.execute(text("SELECT pg_advisory_unlock_all()"))
        await owner.commit()


@pytest.mark.parametrize("bad", [False, True])
async def test_reader_target_gate_preserves_unsupported_wait_and_drain(
    db_session, monkeypatch, bad
):
    run = await _queued_dispatch(db_session)
    run.status = "running"
    raw = {"version": 999} if bad else _bootstrap_wait(run)
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()
    gate = AsyncMock(return_value="d" * 64)
    monkeypatch.setattr(drain, "require_recovery_readers", gate)
    await drain.begin_drain(db_session, "a" * 40)
    await db_session.commit()
    if bad:
        with pytest.raises(RecoveryContractError):
            await drain.end_drain(db_session, "a" * 40)
        await db_session.rollback()
    else:
        await drain.end_drain(db_session, "a" * 40)
        await db_session.commit()
        assert gate.await_count == 2
        gate.assert_awaited_with(release_sha="a" * 40, own=False)
    await db_session.refresh(run)
    assert run.agent_state["recovery_v1"] == raw


@pytest.mark.parametrize("fault", ["expiry", "epoch"])
async def test_end_drain_rechecks_inventory_after_sql_classification(
    db_session, monkeypatch, fault,
):
    from tests.test_generation_recovery_readers import install_healthy_reader_inventory
    from yleum_api.models.deployment_drain import DeploymentDrain
    from yleum_api.services.generation import recovery_quiescence

    value, redis, _ = install_healthy_reader_inventory(monkeypatch)
    run = await _queued_dispatch(db_session)
    run.status = "running"
    raw = _bootstrap_wait(run)
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()
    await drain.begin_drain(db_session, "a" * 40)
    await db_session.commit()
    classify = recovery_quiescence.quiesced_recovery_run
    classified = []

    async def interrupted_classification(*args, **kwargs):
        result = await classify(*args, **kwargs)
        assert result is not None
        classified.append(run.id)
        if fault == "expiry":
            monkeypatch.setattr(
                recovery_readers.time, "time_ns", lambda: value["expires_at_ms"] * 1_000_000
            )
        else:
            redis.epoch = str(uuid4())
        return result

    monkeypatch.setattr(recovery_quiescence, "quiesced_recovery_run", interrupted_classification)
    with pytest.raises(RecoveryContractError):
        await drain.end_drain(db_session, "a" * 40)
    await db_session.rollback()
    assert classified
    fence = await db_session.get(DeploymentDrain, "generation")
    assert fence is not None and fence.release_sha == "a" * 40
    await db_session.refresh(run)
    assert run.agent_state["recovery_v1"] == raw and run.status == "running"


async def test_actual_continuation_claim_serializes_before_deploy_fence(
    db_session,
    test_engine,
    monkeypatch,
):
    from yleum_api.core.errors import ApiError
    from yleum_api.services.generation_runs import prompt_hash

    run = await _queued_dispatch(db_session)
    run.status = "running"
    run.prompt_hash = prompt_hash("Собери приложение")
    run.agent_state = {**run.agent_state, "recovery_v1": _bootstrap_wait(run)}
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr(
        recovery_readers, "require_recovery_readers", AsyncMock(return_value="d" * 64)
    )
    async with test_engine.connect() as owner, factory() as claimant, factory() as deployer:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            claim = await recovery_execution.claim_continuation(
                claimant,
                run_id=run.id,
                expected_attempt=0,
                worker_id="owned",
                now=datetime.now(UTC),
            )
            assert claim is not None
            fencing = asyncio.create_task(drain.begin_drain(deployer, "a" * 40))
            await asyncio.sleep(0.05)
            assert not fencing.done()
            await claimant.commit()
            await asyncio.wait_for(fencing, 2)
            await deployer.commit()
            with pytest.raises(ApiError, match="temporarily paused"):
                await recovery_execution.claim_continuation(
                    claimant,
                    run_id=run.id,
                    expected_attempt=1,
                    worker_id="owned",
                    now=datetime.now(UTC),
                )
            await claimant.rollback()
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("body_state", ["uploading", "indeterminate", "complete", "missing"])
async def test_finished_financial_journal_does_not_prove_receiver_stopped(
    db_session,
    test_engine,
    monkeypatch,
    body_state,
):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from yleum_api.models.provider_call import ProviderCall

    path = Path(__file__).parents[1] / "migrations/versions/0087_generation_responses.py"
    spec = importlib.util.spec_from_file_location("response_migration", path)
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
        run, _, _ = await released_wait(db_session)
        call = ProviderCall(
            id=uuid4(),
            provider_scope="llmgw",
            route="chat",
            requested_model="synthetic",
            expense_owner="user",
            user_id=run.user_id,
            project_id=run.project_id,
            run_id=run.id,
            stage="agent",
            free=False,
            status="completed",
            finished_at=datetime.now(UTC),
        )
        db_session.add(call)
        await db_session.flush()
        attempt = GenerationProviderAttempt(
            attempt_id=uuid4(),
            run_id=run.id,
            project_id=run.project_id,
            user_id=run.user_id,
            prompt_hash=run.prompt_hash,
            request_sha256="a" * 64,
            dispatch_token=uuid4(),
            phase="closed",
            provider_call_id=call.id,
        )
        db_session.add(attempt)
        await db_session.flush()
        if body_state != "missing":
            await db_session.execute(
                text(
                    "INSERT INTO generation_response_budgets "
                    "(bucket,storage_epoch,quota_bytes,observation_nonce) "
                    "VALUES ('synthetic',:id,100,:id)"
                ),
                {"id": uuid4()},
            )
            await db_session.execute(
                text(
                    "INSERT INTO generation_responses "
                    "(attempt_id,bucket,writer_token,state,allocated_bytes,manifest_key,"
                    "manifest_sha256,response_sha256) VALUES "
                    "(:id,'synthetic',:token,:state,0,'synthetic',:digest,:digest)"
                ),
                {
                    "id": attempt.attempt_id,
                    "token": uuid4(),
                    "state": body_state,
                    "digest": "a" * 64,
                },
            )
        await db_session.commit()
        monkeypatch.setattr(drain, "require_recovery_readers", AsyncMock(return_value="d" * 64))
        await drain.begin_drain(db_session, "a" * 40)
        await db_session.commit()
        result = await drain.drain_status(db_session)
        assert result["drained"] is (body_state == "complete")
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with factory() as checking:
            classified = await drain._resume_classification(
                checking, drain.ReleaseCaptureContext(**release_capture()),
            )
            assert classified.drained is (body_state == "complete")
        await db_session.refresh(run)
        assert run.status == "running" and run.finished_at is None
    finally:
        await db_session.rollback()
        async with test_engine.begin() as connection:
            await connection.run_sync(cleanup)


async def test_actual_adaptation_hold_with_distinct_control_and_candidate_is_quiescent(
    db_session,
    test_engine,
    monkeypatch,
):
    from tests.test_generation_recovery_adaptation_cycle import _save_wait, _session_owner, _setup
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.generation import recovery_adaptation

    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    kinds = [call[0] for call in controller.calls]
    assert kinds.index("business_config") < kinds.index("snapshot") < kinds.index("prepare")
    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle)
        await handle.release()
    await db_session.refresh(run)
    state = recovery_execution.recovery_state(run)
    assert state.execution.control_workspace_id != handle.workspace_id
    assert state.execution.control_fencing_epoch != state.binding.fencing_epoch
    monkeypatch.setattr(drain, "require_recovery_readers", AsyncMock(return_value="d" * 64))
    await drain.begin_drain(db_session, "a" * 40)
    await db_session.commit()
    result = await drain.drain_status(db_session)
    assert result["drained"] is True
    async with factory() as checking:
        assert (await drain._resume_classification(
            checking, drain.ReleaseCaptureContext(**release_capture()),
        )).drained
    hold = await db_session.get(
        GenerationRecoveryEffect,
        recovery_adaptation._id(run.id, "hold", state.execution.lease_cycle_seq),
    )
    hold.receipt_sha256 = "0" * 64
    await db_session.commit()
    assert (await drain.drain_status(db_session))["drained"] is False
    async with factory() as checking:
        assert not (await drain._resume_classification(
            checking, drain.ReleaseCaptureContext(**release_capture()),
        )).drained
    assert run.status == "running" and not any(x[0] == "cleanup" for x in controller.calls)

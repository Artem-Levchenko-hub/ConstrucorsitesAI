import asyncio
import threading
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellWorkspace
from omnia_api.models.restoration import Restoration
from omnia_api.models.user import User
from omnia_api.services.generation import lifecycle, supervisor
from omnia_api.services.generation_runs import (
    GenerationDispatch,
    recover_interrupted_generation_runs,
    retry_terminal_adaptation_notifications,
    store_generation_dispatch,
    terminalize_generation_run_locked,
)
from omnia_api.workers import generation

pytestmark = pytest.mark.asyncio


async def _queued_dispatch(session: AsyncSession) -> GenerationRun:
    owner = User(email=f"capacity-race-{uuid4().hex}@example.test", password_hash="x")
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id,
        name="Capacity race",
        slug=f"capacity-race-{uuid4().hex}",
        template="max_miniapp",
    )
    session.add(project)
    await session.flush()
    user_message = Message(project_id=project.id, role="user", content="Собери приложение")
    assistant_message = Message(project_id=project.id, role="assistant", content="")
    session.add_all((user_message, assistant_message))
    await session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        idempotency_key=f"capacity-race-{uuid4().hex}",
        prompt_hash="c" * 64,
        status="queued_for_capacity",
    )
    session.add(run)
    await session.flush()
    store_generation_dispatch(
        run,
        GenerationDispatch(
            schema_version=1,
            project_id=project.id,
            user_id=owner.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            current_snapshot_id=None,
            prompt_text="Собери приложение",
            model_id="google/gemini-2.5-pro",
            force_model=None,
            is_free=False,
            orchestrate=True,
            selected_elements=[],
        ),
    )
    await session.commit()
    return run


async def _bind_adaptation(session: AsyncSession, run: GenerationRun) -> Restoration:
    workspace = ProjectCellWorkspace(
        project_id=run.project_id,
        owner_id=run.user_id,
        provider="docker_owner_canary",
        state="ready",
        generation_run_id=run.id,
        fencing_epoch=1,
    )
    session.add(workspace)
    await session.flush()
    operation = Restoration(
        id=uuid4(),
        project_id=run.project_id,
        owner_id=run.user_id,
        workspace_id=workspace.id,
        source_version_id=uuid4(),
        source_snapshot_id=uuid4(),
        base_draft_snapshot_id=uuid4(),
        target_commit_sha="a" * 40,
        base_commit_sha="b" * 40,
        idempotency_key=f"worker-adaptation-{run.id}",
        request_digest="c" * 64,
        selected_branch="adaptive",
        adaptation_run_id=run.id,
        state="adapting",
        phase="generation",
        revision=2,
        fencing_epoch=workspace.fencing_epoch,
        request_payload={},
    )
    session.add(operation)
    run.agent_state = {
        "restoration_adaptation": {
            "operation_id": str(operation.id),
            "adaptation_run_id": str(run.id),
        }
    }
    await session.flush()
    return operation


async def _bind_adaptation_proof_handoff(
    session: AsyncSession,
    run: GenerationRun,
) -> Restoration:
    from omnia_api.services import restorations
    from omnia_api.services.promotion_permit import canonical_files_digest

    operation = await _bind_adaptation(session, run)
    workspace = await session.get(ProjectCellWorkspace, operation.workspace_id)
    assert workspace is not None
    candidate_workspace_id = uuid4()
    files = {"src/app/page.tsx": "export default function Page(){return 'adapted'}"}
    artifact_digest = canonical_files_digest(files)
    proof_request = {
        "workspace": {
            "source_workspace_id": str(workspace.id),
            "candidate_workspace_id": str(candidate_workspace_id),
            "operation_id": str(operation.id),
            "project_id": str(run.project_id),
            "owner_id": str(run.user_id),
            "generation_run_id": str(run.id),
            "candidate_fencing_epoch": 2,
            "source_database_digest": "1" * 64,
            "proof_digest": "2" * 64,
            "capabilities": {
                "portable_machine": True,
                "database_admin": "isolated_copy",
                "restoration_adaptation_database_copy_v1": True,
            },
        },
        "candidate_workspace_id": str(candidate_workspace_id),
        "candidate_fencing_epoch": 2,
        "candidate_workspace_revision": "3" * 64,
        "candidate_proof_key": "4" * 64,
        "candidate_artifact_digest": artifact_digest,
        "proof_attempt": 1,
    }
    intent = {
        "proof_request": proof_request,
        "candidate_files": files,
        "candidate_artifact_digest": artifact_digest,
    }
    operation.state = "applying"
    operation.phase = "activation_proof_intent"
    operation.activation_request = intent
    operation.activation_request_digest = restorations._digest(intent)
    run.agent_state = {
        **run.agent_state,
        "max_finalization": {
            "restoration_adaptation_proof_attempt": {
                "number": 1,
                "status": "issued",
                "candidate_workspace_id": str(candidate_workspace_id),
                "candidate_fencing_epoch": 2,
                "candidate_workspace_revision": "3" * 64,
                "candidate_proof_key": "4" * 64,
                "candidate_artifact_digest": artifact_digest,
            }
        },
    }
    await session.flush()
    return operation


async def test_adaptive_pre_offer_cancel_terminalizes_run_and_sets_tombstone(
    db_session,
) -> None:
    from omnia_api.services.restorations import cancel_restoration, public_operation

    run = await _queued_dispatch(db_session)
    run.status = "running"
    operation = await _bind_adaptation(db_session, run)
    await db_session.commit()

    result = await cancel_restoration(
        db_session,
        run.project_id,
        run.user_id,
        operation.id,
        cast(Any, object()),
    )

    await db_session.refresh(run)
    await db_session.refresh(operation)
    assert result.state == "cancelled"
    assert operation.activation_cancel_requested_at is not None
    assert operation.activation_request is None
    assert operation.activation_notification_state == "pending"
    assert public_operation(operation).can_cancel is False
    assert run.status == "cancelled"
    assert run.agent_state["restoration_adaptation_owner_status"] == "terminal_pending"
    assert run.agent_state["restoration_adaptation_activation"] == {
        "state": "cancelled",
        "operation_id": str(operation.id),
        "activation_id": None,
        "receipt_digest": None,
        "publication_consumed": False,
    }


async def test_adaptive_proof_intent_cancel_terminalizes_without_replay(
    db_session,
) -> None:
    from omnia_api.services.restorations import cancel_restoration

    run = await _queued_dispatch(db_session)
    run.status = "running"
    operation = await _bind_adaptation(db_session, run)
    operation.state = "applying"
    operation.phase = "activation_proof_intent"
    operation.activation_request = {"proof_request": {"proof_attempt": 1}}
    operation.activation_request_digest = "d" * 64
    run.agent_state = {
        **run.agent_state,
        "max_finalization": {
            "restoration_adaptation_proof_attempt": {
                "number": 1,
                "status": "issued",
            }
        },
    }
    await db_session.commit()

    result = await cancel_restoration(
        db_session,
        run.project_id,
        run.user_id,
        operation.id,
        cast(Any, object()),
    )

    await db_session.refresh(run)
    await db_session.refresh(operation)
    assert result.state == "cancelled"
    assert operation.activation_request is None
    assert operation.activation_request_digest is None
    assert run.status == "cancelled"
    assert run.agent_state["restoration_adaptation_activation"]["state"] == "cancelled"


async def test_delayed_manifest_read_does_not_block_or_overwrite_adaptive_cancel(
    db_session,
    test_engine,
    monkeypatch,
) -> None:
    from omnia_api.models.snapshot import Snapshot
    from omnia_api.schemas.restoration import (
        ActivationBusinessProbe,
        ActivationPreparedTarget,
        RestorationAdaptationActivationCommand,
        RestorationAdaptationActivationOffer,
        RestorationAdaptationActivationStatus,
        canonical_activation_digest,
    )
    from omnia_api.services import repo, restorations
    from omnia_api.services.promotion_permit import canonical_files_digest

    run = await _queued_dispatch(db_session)
    run.status = "running"
    operation = await _bind_adaptation(db_session, run)
    project = await db_session.get(Project, run.project_id)
    workspace = await db_session.get(ProjectCellWorkspace, operation.workspace_id)
    assert project is not None and workspace is not None
    files = {"src/app/page.tsx": "export default function Page(){return 'adapted'}"}
    base_sha = await asyncio.to_thread(
        repo.init_from_files,
        project.id,
        {"src/app/page.tsx": "export default function Page(){return 'base'}"},
        "base",
    )
    snapshot = Snapshot(project_id=project.id, commit_sha=base_sha, prompt_text="base")
    db_session.add(snapshot)
    await db_session.flush()
    project.current_snapshot_id = snapshot.id
    operation.base_draft_snapshot_id = snapshot.id
    operation.base_commit_sha = base_sha
    planned_sha = await asyncio.to_thread(
        repo.prepare_restoration_adaptation_commit,
        project.id,
        files,
        base_sha,
        operation.id,
    )
    operation.adaptation_planned_commit_sha = planned_sha
    candidate_workspace_id = uuid4()
    artifact_digest = canonical_files_digest(files)
    manifest_digest = await asyncio.to_thread(
        repo.restoration_adaptation_source_manifest_digest,
        project.id,
        planned_sha,
    )
    raw_probe = {
        "version": 1,
        "endpoint": "/api/restoration-probe",
        "data_contract_digest": "8" * 64,
        "witnesses": [
            {
                "entity": "qa_tasks",
                "id_column": "id",
                "owner_column": "max_user_id",
                "value_column": "title",
                "create_values": {"status": "pending"},
            }
        ],
        "max_payload_bytes": 4096,
        "pointers": {
            "items": "/items",
            "item": "/item",
            "item_id": "/item/id",
            "owner_id": "/item/ownerId",
            "entity": "/item/entity",
            "marker": "/item/marker",
            "phase": "/item/phase",
            "contract_digest": "/probeContractDigest",
        },
    }
    probe = ActivationBusinessProbe(
        **raw_probe,
        contract_digest=canonical_activation_digest(raw_probe),
    )
    run.agent_state = {
        **run.agent_state,
        "restoration_adaptation": {
            "operation_id": str(operation.id),
            "project_id": str(project.id),
            "owner_id": str(run.user_id),
            "adaptation_run_id": str(run.id),
            "base_draft_snapshot_id": str(snapshot.id),
        },
        "max_finalization": {
            "restoration_adaptation_proof": {
                "state": "proof_ready",
                "operation_id": str(operation.id),
                "source_workspace_id": str(workspace.id),
                "candidate_workspace_id": str(candidate_workspace_id),
                "candidate_fencing_epoch": 2,
                "candidate_workspace_revision": "2" * 64,
                "proof_attempt": 1,
                "proof_digest": "3" * 64,
                "source_workspace_revision": "4" * 64,
                "candidate_artifact_digest": artifact_digest,
                "candidate_source_manifest_digest": manifest_digest,
                "probe_contract_digest": probe.contract_digest,
                "probe_rehearsal_digest": "6" * 64,
                "probe_rehearsal_database_digest": "7" * 64,
            }
        },
    }
    offer_request = restorations._activation_offer_request(
        operation=operation,
        run=run,
        proof=run.agent_state["max_finalization"]["restoration_adaptation_proof"],
    )
    saved_intent = {
        "offer_request": offer_request.model_dump(mode="json"),
        "candidate_files": files,
        "candidate_artifact_digest": artifact_digest,
    }
    operation.activation_request = saved_intent
    operation.activation_request_digest = canonical_activation_digest(saved_intent)
    operation.state = "applying"
    operation.phase = "activation_offer_intent"
    await db_session.commit()
    entered = threading.Event()
    release = threading.Event()
    cancel_calls = 0

    async def offer(request):
        raw_offer = {
            "state": "offered",
            **request.model_dump(mode="json"),
            "activation_id": str(uuid4()),
            "expected_source_fencing_epoch": workspace.fencing_epoch,
            "target_fencing_epoch": workspace.fencing_epoch + 1,
            "source_workspace_revision": "4" * 64,
            "source_code_volume": "source-code",
            "live_database_volume": "live-db",
            "live_database_identity_digest": "9" * 64,
            "candidate_artifact_digest": artifact_digest,
            "candidate_source_manifest_digest": manifest_digest,
            "candidate_files_digest": restorations._activation_candidate_files_digest(files),
            "archive_digest": "a" * 64,
            "business_probe": probe.model_dump(mode="json"),
            "probe_contract_digest": probe.contract_digest,
            "probe_rehearsal_digest": "6" * 64,
            "probe_rehearsal_database_digest": "7" * 64,
        }
        result = RestorationAdaptationActivationOffer(
            **raw_offer,
            offer_digest=canonical_activation_digest(raw_offer),
        )
        return result

    async def cancel(command: RestorationAdaptationActivationCommand):
        nonlocal cancel_calls
        cancel_calls += 1
        offer = command.offer
        source = ActivationPreparedTarget(
            workspace_id=offer.workspace_id,
            fencing_epoch=offer.expected_source_fencing_epoch,
            code_volume=offer.source_code_volume,
            code_digest=offer.source_workspace_revision,
            database_volume=offer.live_database_volume,
            database_identity_digest=offer.live_database_identity_digest,
        )
        target = ActivationPreparedTarget(
            workspace_id=offer.workspace_id,
            fencing_epoch=offer.target_fencing_epoch,
            code_volume="target-code",
            code_digest=offer.archive_digest,
            database_volume=offer.live_database_volume,
            database_identity_digest=offer.live_database_identity_digest,
        )
        core = {
            "state": "cancelled",
            "effects_admitted": False,
            "operation_id": str(offer.operation_id),
            "generation_run_id": str(offer.generation_run_id),
            "project_id": str(offer.project_id),
            "owner_id": str(offer.owner_id),
            "activation_id": str(offer.activation_id),
            "activation_digest": command.activation_digest(),
            "proof_digest": offer.proof_digest,
            "fencing_epoch": offer.target_fencing_epoch,
            "source_volume_identity": source.model_dump(mode="json"),
            "target_volume_identity": target.model_dump(mode="json"),
            "health_digest": None,
        }
        return RestorationAdaptationActivationStatus(
            state="cancelled",
            effects_admitted=False,
            offer=offer,
            planned_commit_sha=command.planned_commit_sha,
            activation_digest=command.activation_digest(),
            fencing_epoch=offer.target_fencing_epoch,
            source_volume_identity=source,
            target_volume_identity=target,
            health_digest=None,
            receipt_digest=canonical_activation_digest(core),
        )

    async def quota_must_not_settle(*_args, **_kwargs):
        pytest.fail("cancelled adaptive activation must not consume quota")

    monkeypatch.setattr(
        restorations, "project_cell_offer_restoration_adaptation_activation", offer
    )
    monkeypatch.setattr(
        restorations, "project_cell_cancel_restoration_adaptation_activation", cancel
    )
    monkeypatch.setattr(restorations, "consume_free_generation", quota_must_not_settle)
    original_manifest_digest = repo.restoration_adaptation_source_manifest_digest

    def delayed_manifest_digest(project_id, commit_sha):
        entered.set()
        assert release.wait(5)
        return original_manifest_digest(project_id, commit_sha)

    monkeypatch.setattr(
        repo, "restoration_adaptation_source_manifest_digest", delayed_manifest_digest
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async def resume():
        async with factory() as resume_session:
            return await restorations._resume_restoration_adaptation_activation(
                resume_session,
                project_id=project.id,
                owner_id=run.user_id,
                operation_id=operation.id,
            )

    task = asyncio.create_task(resume())
    assert await asyncio.to_thread(entered.wait, 5)
    async with factory() as cancel_session:
        cancelled = await asyncio.wait_for(
            restorations.cancel_restoration(
                cancel_session,
                project.id,
                run.user_id,
                operation.id,
                cast(Any, object()),
            ),
            2,
        )
    assert cancelled.phase == "activation_cancel"
    release.set()
    assert (await asyncio.wait_for(task, 10)).state == "cancelled"

    await db_session.refresh(operation)
    await db_session.refresh(run)
    assert cancel_calls == 1
    assert operation.activation_cancel_requested_at is not None
    assert operation.state == "cancelled"
    assert operation.phase == "activation_cancelled"
    assert operation.activation_settled_at is None
    assert operation.activation_notification_state == "pending"
    assert run.status == "cancelled"
    assert run.agent_state["restoration_adaptation_owner_status"] == "terminal_pending"
    assert run.agent_state["restoration_adaptation_activation"]["publication_consumed"] is False
    async with factory() as replay_session:
        replay = await restorations.cancel_restoration(
            replay_session,
            project.id,
            run.user_id,
            operation.id,
            cast(Any, object()),
        )
    assert replay.state == "cancelled"
    assert cancel_calls == 1


async def test_live_worker_survives_api_recovery_and_duplicate_delivery(
    db_session,
    test_engine,
    monkeypatch,
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    entered, finish = asyncio.Event(), asyncio.Event()
    executions = []
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def work(**kwargs):
        executions.append(kwargs["run_id"])
        entered.set()
        await finish.wait()
        async with factory() as session:
            row = await session.get(GenerationRun, run.id)
            row.status = "completed"
            row.finished_at = datetime.now(UTC)
            await session.commit()

    async def tracked(work, **kwargs):
        await work

    monkeypatch.setattr(lifecycle, "_process_prompt", work)
    monkeypatch.setattr(supervisor, "_run_tracked_prompt", tracked)
    task = asyncio.create_task(generation.execute_dispatch(run.id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not await generation.execute_dispatch(run.id)
        async with factory() as api_session:
            assert await recover_interrupted_generation_runs(api_session) == 0
        assert await supervisor.resume_capacity_queued_generations() == 0
        finish.set()
        assert await asyncio.wait_for(task, 5)
        await db_session.refresh(run)
        assert run.status == "completed"
        assert executions == [run.id]
    finally:
        finish.set()
        await task


async def test_claimed_orphan_terminalizes_bound_adaptation(
    db_session, test_engine, monkeypatch
):
    from sqlalchemy import text

    from omnia_api.services import orchestrator_client

    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = datetime.now(UTC)
    operation = await _bind_adaptation(db_session, run)
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    callbacks = []

    async def notify(**kwargs):
        callbacks.append(kwargs)
        if len(callbacks) <= 3:
            raise RuntimeError("transient callback failure")

    monkeypatch.setattr(
        orchestrator_client,
        "project_cell_update_restoration_adaptation_owner_status",
        notify,
    )
    assert not await generation.execute_dispatch(run.id)
    lock_params = {
        "ns": generation._LOCK_NAMESPACE,
        "key": generation._lock_key(run.id),
    }
    async with test_engine.connect() as probe:
        assert await probe.scalar(
            text("SELECT pg_try_advisory_lock(:ns, :key)"), lock_params
        )
        await probe.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), lock_params)
        await probe.commit()
    await db_session.refresh(run)
    await db_session.refresh(operation)
    assert run.status == "failed"
    assert "unknown effects were not replayed" in run.error
    assert operation.state == "adapting"
    assert run.agent_state["restoration_adaptation_owner_status"] == "terminal_pending"

    assert await retry_terminal_adaptation_notifications(db_session) == 0
    await db_session.refresh(run)
    assert run.agent_state["restoration_adaptation_owner_status"] == "terminal_pending"
    assert await retry_terminal_adaptation_notifications(db_session) == 1
    await db_session.refresh(run)
    await db_session.refresh(operation)
    assert operation.state == "failed"
    assert operation.phase == "generation"
    assert run.agent_state["restoration_adaptation_owner_status"] == "terminal_notified"
    assert len(callbacks) == 4
    assert callbacks[-1]["operation_id"] == operation.id
    assert callbacks[-1]["state"] == "terminal"


async def test_claimed_adaptation_proof_handoff_waits_for_reconciliation(
    db_session, test_engine, monkeypatch
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = datetime.now(UTC)
    run.status = "running"
    operation = await _bind_adaptation_proof_handoff(db_session, run)
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    executions: list[object] = []

    async def must_not_replay(**kwargs):
        executions.append(kwargs)

    monkeypatch.setattr(lifecycle, "_process_prompt", must_not_replay)

    assert not await generation.execute_dispatch(run.id)

    await db_session.refresh(run)
    await db_session.refresh(operation)
    assert executions == []
    assert run.status == "running"
    assert run.error is None
    assert operation.state == "applying"
    assert operation.phase == "activation_proof_intent"
    assert operation.activation_request is not None


async def test_claimed_proof_ready_offer_handoff_waits_for_reconciliation(
    db_session, test_engine, monkeypatch
):
    from omnia_api.services import restorations

    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = datetime.now(UTC)
    run.status = "running"
    operation = await _bind_adaptation_proof_handoff(db_session, run)
    proof_intent = operation.activation_request
    assert isinstance(proof_intent, dict)
    proof_request = proof_intent["proof_request"]
    assert isinstance(proof_request, dict)
    proof = {
        "state": "proof_ready",
        "candidate_workspace_id": proof_request["candidate_workspace_id"],
        "candidate_fencing_epoch": proof_request["candidate_fencing_epoch"],
        "candidate_workspace_revision": proof_request["candidate_workspace_revision"],
        "proof_attempt": proof_request["proof_attempt"],
        "proof_digest": "5" * 64,
    }
    state = dict(run.agent_state)
    finalization = dict(state["max_finalization"])
    finalization["restoration_adaptation_proof_attempt"] = {
        **finalization["restoration_adaptation_proof_attempt"],
        "status": "completed",
    }
    finalization["restoration_adaptation_proof"] = proof
    state["max_finalization"] = finalization
    run.agent_state = state
    offer_request = restorations._activation_offer_request(
        operation=operation,
        run=run,
        proof=proof,
    )
    offer_intent = {
        "offer_request": offer_request.model_dump(mode="json"),
        "candidate_files": proof_intent["candidate_files"],
        "candidate_artifact_digest": proof_intent["candidate_artifact_digest"],
    }
    operation.phase = "activation_offer_intent"
    operation.activation_request = offer_intent
    operation.activation_request_digest = restorations._digest(offer_intent)
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)

    assert not await generation.execute_dispatch(run.id)

    await db_session.refresh(run)
    await db_session.refresh(operation)
    assert run.status == "running"
    assert run.error is None
    assert operation.state == "applying"
    assert operation.phase == "activation_offer_intent"


async def test_claimed_adaptation_mismatched_proof_outbox_fails_closed(
    db_session, test_engine, monkeypatch
):
    from omnia_api.services import restorations

    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = datetime.now(UTC)
    run.status = "running"
    operation = await _bind_adaptation_proof_handoff(db_session, run)
    raw = dict(operation.activation_request or {})
    request = dict(raw["proof_request"])
    request["candidate_proof_key"] = "f" * 64
    raw["proof_request"] = request
    operation.activation_request = raw
    operation.activation_request_digest = restorations._digest(raw)
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)

    assert not await generation.execute_dispatch(run.id)

    await db_session.refresh(run)
    await db_session.refresh(operation)
    assert run.status == "failed"
    assert "unknown effects were not replayed" in run.error
    assert operation.state == "applying"
    assert operation.phase == "activation_proof_intent"


@pytest.mark.parametrize(
    ("field", "malformed"),
    [
        ("candidate_workspace_revision", "not-a-sha256"),
        ("candidate_proof_key", "G" * 64),
    ],
)
async def test_claimed_adaptation_matching_malformed_proof_outbox_is_orphaned(
    db_session, test_engine, monkeypatch, field, malformed
):
    from omnia_api.services import restorations

    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = datetime.now(UTC)
    run.status = "running"
    operation = await _bind_adaptation_proof_handoff(db_session, run)
    raw = dict(operation.activation_request or {})
    request = dict(raw["proof_request"])
    request[field] = malformed
    raw["proof_request"] = request
    operation.activation_request = raw
    operation.activation_request_digest = restorations._digest(raw)
    state = dict(run.agent_state)
    finalization = dict(state["max_finalization"])
    attempt = dict(finalization["restoration_adaptation_proof_attempt"])
    attempt[field] = malformed
    finalization["restoration_adaptation_proof_attempt"] = attempt
    state["max_finalization"] = finalization
    run.agent_state = state
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)

    assert not await generation.execute_dispatch(run.id)

    await db_session.refresh(run)
    assert run.status == "failed"
    assert "unknown effects were not replayed" in run.error


async def test_claimed_adaptation_rejected_proof_becomes_real_orphan(
    db_session, test_engine, monkeypatch
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = datetime.now(UTC)
    run.status = "running"
    operation = await _bind_adaptation_proof_handoff(db_session, run)
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)

    assert not await generation.execute_dispatch(run.id)

    state = dict(run.agent_state)
    finalization = dict(state["max_finalization"])
    finalization["restoration_adaptation_proof_attempt"] = {
        **finalization["restoration_adaptation_proof_attempt"],
        "status": "completed",
    }
    finalization["restoration_adaptation_proof"] = {
        "state": "migration_required",
        "reason_code": "candidate_schema_changed",
    }
    state["max_finalization"] = finalization
    run.agent_state = state
    operation.state = "adapting"
    operation.phase = "generation"
    operation.activation_request = None
    operation.activation_request_digest = None
    await db_session.commit()

    assert not await generation.execute_dispatch(run.id)

    await db_session.refresh(run)
    await db_session.refresh(operation)
    assert run.status == "failed"
    assert "unknown effects were not replayed" in run.error
    assert run.agent_state["restoration_adaptation_owner_status"] == "terminal_pending"
    assert operation.state == "adapting"
    assert operation.phase == "generation"


@pytest.mark.parametrize("effects_admitted", [False, True])
async def test_cancelled_generation_bridges_only_pre_ponr_activation(
    db_session, effects_admitted
):
    run = await _queued_dispatch(db_session)
    run.status = "running"
    operation = await _bind_adaptation(db_session, run)
    operation.state = "reconciling"
    operation.phase = "activation_status" if effects_admitted else "activation_offer_intent"
    operation.activation_request = {"offer_request": {}}
    operation.activation_effects_admitted = effects_admitted
    run.agent_state = {
        **run.agent_state,
        "max_finalization": {
            "restoration_adaptation_proof": {"state": "proof_ready"}
        },
    }
    await db_session.flush()

    await terminalize_generation_run_locked(db_session, run, status="cancelled")
    await db_session.commit()
    assert run.agent_state["restoration_adaptation_owner_status"] == (
        "activation_cancel_pending"
    )

    assert await retry_terminal_adaptation_notifications(db_session) == 1
    await db_session.refresh(operation)
    await db_session.refresh(run)
    assert run.agent_state["restoration_adaptation_owner_status"] == (
        "sealed_proof_retained"
    )
    if effects_admitted:
        assert operation.phase == "activation_status"
        assert operation.activation_effects_admitted is True
    else:
        assert operation.state == "reconciling"
        assert operation.phase == "activation_cancel"
        assert operation.activation_cancel_requested_at is not None


async def test_terminal_callback_and_activation_lock_order_cannot_deadlock(
    db_session, test_engine
):
    from omnia_api.services.restorations import _owned_operation

    run = await _queued_dispatch(db_session)
    run.status = "running"
    operation = await _bind_adaptation(db_session, run)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    operation_locked = asyncio.Event()
    run_locked = asyncio.Event()

    async def activation_order() -> None:
        async with factory() as session:
            await _owned_operation(
                session, run.project_id, run.user_id, operation.id
            )
            operation_locked.set()
            await run_locked.wait()
            await session.get(GenerationRun, run.id, with_for_update=True)
            await session.commit()

    async def terminal_order() -> None:
        await operation_locked.wait()
        async with factory() as session:
            locked = await session.get(GenerationRun, run.id, with_for_update=True)
            assert locked is not None
            run_locked.set()
            await terminalize_generation_run_locked(session, locked, status="failed")
            await session.commit()

    async with asyncio.timeout(5):
        await asyncio.gather(activation_order(), terminal_order())


async def test_cancel_before_dispatch_never_executes(db_session, test_engine, monkeypatch):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.status = "cancel_requested"
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    executions = []

    async def work(**kwargs):
        executions.append(kwargs)

    async def tracked(work, **kwargs):
        await work

    monkeypatch.setattr(lifecycle, "_process_prompt", work)
    monkeypatch.setattr(supervisor, "_run_tracked_prompt", tracked)
    await generation.execute_dispatch(run.id)
    assert executions == []
    await db_session.refresh(run)
    assert run.status == "cancelled"


async def test_terminal_run_cleanup_waits_for_execution_lock(db_session, test_engine, monkeypatch):
    from sqlalchemy import text

    from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace

    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = datetime.now(UTC)
    run.status = "failed"
    run.error = "original failure"
    run.finished_at = datetime.now(UTC)
    workspace = ProjectCellWorkspace(
        project_id=run.project_id,
        owner_id=run.user_id,
        provider="docker",
        state="ready",
    )
    db_session.add(workspace)
    await db_session.flush()
    operation = ProjectCellOperation(
        workspace_id=workspace.id,
        generation_run_id=run.id,
        execution_run_id=run.id,
        kind="release",
        status="running",
        request_digest="a" * 64,
        idempotency_key="terminal-release",
    )
    db_session.add(operation)
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    params = {"ns": generation._LOCK_NAMESPACE, "key": generation._lock_key(run.id)}
    async with test_engine.connect() as owner:
        await owner.execute(text("SELECT pg_advisory_lock(:ns, :key)"), params)
        try:
            assert not await generation.execute_dispatch(run.id)
            await db_session.refresh(operation)
            assert operation.status == "running"
        finally:
            await owner.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), params)
    assert not await generation.execute_dispatch(run.id)
    await db_session.refresh(operation)
    await db_session.refresh(run)
    assert operation.status == "indeterminate"
    assert run.status == "failed"
    assert run.error == "original failure"


async def test_database_deadline_cancels_real_task_without_rewriting_failure(
    db_session,
    test_engine,
    monkeypatch,
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def work(**kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def no_redis_signal(*args):
        await asyncio.Future()

    async def clear(*args):
        pass

    monkeypatch.setattr(lifecycle, "_process_prompt", work)
    monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", no_redis_signal)
    monkeypatch.setattr(supervisor, "clear_generation_cancel", clear)
    task = asyncio.create_task(generation.execute_dispatch(run.id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await db_session.refresh(run)
        run.status = "failed"
        run.error = "generation deadline exceeded; phase=agent"
        run.finished_at = datetime.now(UTC)
        await db_session.commit()
        await asyncio.wait_for(task, 5)
        assert cancelled.is_set()
        await db_session.refresh(run)
        assert run.status == "failed"
        assert run.error == "generation deadline exceeded; phase=agent"
    finally:
        if not task.done():
            task.cancel()
            await task


async def test_dispatcher_retries_unclaimed_work_after_connection_failure(
    db_session,
    test_engine,
    monkeypatch,
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    recovered = asyncio.Event()
    attempts = 0

    class Redis:
        async def set(self, *args, **kwargs):
            pass

    async def execute(run_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("database unavailable before claiming")
        recovered.set()
        return True

    monkeypatch.setattr(generation, "get_redis", Redis)
    monkeypatch.setattr(generation, "execute_dispatch", execute)
    worker = asyncio.create_task(generation.run_forever())
    try:
        await asyncio.wait_for(recovered.wait(), 7)
        assert attempts == 2
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

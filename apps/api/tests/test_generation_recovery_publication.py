"""Real finalization proof and SQL publication; no real Git/S3/provider writes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_generation_recovery_execution import _bootstrap_wait
from tests.test_max_finalization import _install_exact_release_probe, _new_harness
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.services.generation import agent_publication
from yleum_api.services.generation import recovery_execution as ex
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    ContinuationClaim,
    RecoveryContractError,
    decode_recovery,
)
from yleum_api.services.generation.recovery_pipeline import recovery_binding


@pytest.mark.parametrize("lost_ack,damage", [
    ("git", None), ("sql", None), ("event", None),
    *[("event", damage) for damage in (
        "current_pointer", "foreign_snapshot", "cancel", "claim", "receipt",
        "before_sql_cancel", "before_sql_claim",
    )],
])
async def test_owned_publication_ack_loss_never_repeats_git_snapshot_or_quota(
    db_session,
    test_engine,
    monkeypatch,
    lost_ack,
    damage,
):
    cleanup = _install_exact_release_probe(monkeypatch)
    harness = await _new_harness(db_session, test_engine, toolchain_digest="5" * 64)
    coordinator = harness.coordinator
    proof = await coordinator.finalize(files=harness.files, prompt="Build tracker")
    run = await db_session.get(GenerationRun, coordinator.generation_run_id)
    message = Message(project_id=run.project_id, role="assistant", content="")
    db_session.add(message)
    await db_session.flush()
    run.assistant_message_id = message.id
    claim = ContinuationClaim(
        run_id=run.id,
        token=uuid4(),
        attempt_seq=1,
        worker_id="publication-test",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    ref = CheckpointRef(object_key="synthetic/complete-context", sha256="c" * 64)
    raw = _bootstrap_wait(run)
    raw.update(
        phase="working",
        next_check_at=None,
        cursor="finalization",
        attempt_seq=1,
        continuation_claim=claim.model_dump(mode="json"),
        context_ref=ref.model_dump(mode="json"),
        checkpoint=ref.model_dump(mode="json"),
        binding=recovery_binding(await coordinator.executor.current_identity()).model_dump(
            mode="json"
        ),
    )
    raw["execution"].update(
        stage="finalization",
        substep="terminal_gate",
        physical_state="active",
        execution_context_ref=ref.model_dump(mode="json"),
    )
    state = decode_recovery(raw)
    run.agent_state = {**(run.agent_state or {}), "recovery_v1": raw}
    await db_session.commit()
    from yleum_api.services.generation import recovery_store
    from yleum_api.services.orchestrator_client import ProjectCellWorkspaceIdentity

    async def current(workspace_id, *, generation_run_id, fencing_epoch):
        physical = await coordinator.executor.current_identity()
        assert (workspace_id, generation_run_id, fencing_epoch) == (
            physical.workspace_id,
            physical.generation_run_id,
            physical.fencing_epoch,
        )
        return ProjectCellWorkspaceIdentity(
            physical.workspace_revision,
            physical.dependency_digest,
            physical.schema_data_digest,
            physical.cell_manifest_digest,
            physical.base_image_digest,
            physical.build_config_digest,
        )

    monkeypatch.setattr(recovery_store, "project_cell_agent_identity", current)

    class LostCommitAck(AsyncSession):
        async def commit(self):
            lose_ack = self.info.pop("lose_publication_ack", False)
            await super().commit()
            if lose_ack:
                raise OSError("synthetic SQL commit acknowledgement lost")

    factory = async_sessionmaker(test_engine, class_=LostCommitAck, expire_on_commit=False)
    driver = ex.ExecutionDriver(factory, state, {}, None)
    calls, quota = [], []
    before_sql = damage in {"before_sql_cancel", "before_sql_claim"}
    loop = asyncio.get_running_loop()

    async def lose_owner_after_git():
        async with factory() as session:
            current_run = await session.get(GenerationRun, run.id)
            if damage == "before_sql_cancel":
                current_run.status = "cancel_requested"
            else:
                current_state = ex.recovery_state(current_run)
                changed = current_state.model_copy(update={"continuation_claim":
                    claim.model_copy(update={"token": uuid4()})})
                current_run.agent_state = {**current_run.agent_state,
                                          "recovery_v1": changed.model_dump(mode="json")}
            await session.commit()

    def commit(*args, **kwargs):
        calls.append((args, kwargs))
        if lost_ack == "git":
            raise OSError("synthetic repository upload acknowledgement lost")
        if before_sql:
            asyncio.run_coroutine_threadsafe(lose_owner_after_git(), loop).result(timeout=10)
        return "b" * 40

    async def consume(session):
        quota.append(run.id)
        session.info["lose_publication_ack"] = lost_ack == "sql"

    monkeypatch.setattr(agent_publication.repo_svc, "commit_files", commit)
    monkeypatch.setattr(
        agent_publication, "publish_event", AsyncMock(side_effect=OSError("postcommit event lost"))
    )
    monkeypatch.setattr(
        "yleum_api.services.snapshot_preview_capture.capture_snapshot_frontend", AsyncMock()
    )
    kwargs = dict(
        _att_capture=None,
        _attestation_stack="",
        _consume_free_generation=consume,
        _max_finalization_proof=proof.proof,
        _promotion_permit=proof.proof.permit,
        _orch_name=None,
        accumulated="Готово",
        baseline=SourceBaseline(None, None, {}),
        factory=factory,
        files=harness.files,
        ids=GenerationIds(run.id, run.project_id, run.user_id, run.user_message_id, message.id),
        model_id="synthetic",
        progress=SimpleNamespace(steps=2),
        project_info=SimpleNamespace(template="max_miniapp"),
        prompt_text="Build tracker",
        runtime=GenerationRuntime(handle=coordinator.executor, coordinator=coordinator),
        continuation=driver,
    )
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = ex._SESSION_OWNER.set(ex.SessionOwnership(run.id, owner))
        try:
            with pytest.raises(RecoveryContractError if before_sql else OSError):
                await agent_publication.publish_agent_candidate(**kwargs)
            await db_session.refresh(run)
            current = ex.recovery_state(run)
            # The authoritative receipt is read without re-running finalization,
            # source preparation, Git, quota or a second model request.
            if damage == "before_sql_cancel":
                with pytest.raises(RecoveryContractError):
                    await agent_publication.committed_publication(factory, current)
            else:
                outcome = await agent_publication.committed_publication(factory, current)
                assert outcome is (lost_ack != "git" and not before_sql)
            assert len(calls) == 1
            expected_count = int(lost_ack != "git" and not before_sql)
            assert len(quota) == expected_count
            count = await db_session.scalar(
                select(func.count())
                .select_from(Snapshot)
                .where(Snapshot.project_id == run.project_id)
            )
            assert count == expected_count
            from yleum_api.services.generation import agent_pipeline

            monkeypatch.setattr(agent_pipeline, "get_engine", lambda: test_engine)
            original_pipeline = AsyncMock(side_effect=AssertionError("publication prefix replay"))
            monkeypatch.setattr(agent_pipeline, "run_agent_generation", original_pipeline)
            if before_sql:
                with pytest.raises(RecoveryContractError):
                    await agent_pipeline.resume_agent_generation(claim)
            elif damage:
                async with factory() as session:
                    owned_run = await session.get(GenerationRun, run.id)
                    if damage == "current_pointer":
                        project = await session.get(Project, run.project_id)
                        project.current_snapshot_id = None
                    elif damage == "foreign_snapshot":
                        foreign = Project(owner_id=run.user_id, name="Foreign",
                                          slug=f"foreign-{uuid4()}", template="blank")
                        session.add(foreign)
                        await session.flush()
                        snapshot = await session.scalar(select(Snapshot).where(
                            Snapshot.project_id == run.project_id))
                        snapshot.project_id = foreign.id
                    elif damage == "cancel":
                        owned_run.status = "cancel_requested"
                    elif damage == "claim":
                        newer = current.model_copy(update={"continuation_claim":
                            claim.model_copy(update={"token": uuid4()})})
                        owned_run.agent_state = {**owned_run.agent_state,
                                                "recovery_v1": newer.model_dump(mode="json")}
                    else:
                        from yleum_api.models.generation_recovery_effect import (
                            GenerationRecoveryEffect,
                        )
                        effect = await session.get(GenerationRecoveryEffect,
                                                   current.execution.pending_effect_id)
                        effect.receipt_sha256 = "0" * 64
                    await session.commit()
                with pytest.raises(RecoveryContractError):
                    await agent_pipeline.resume_agent_generation(claim)
            elif lost_ack == "git":
                with pytest.raises(RecoveryContractError, match="outcome remains unresolved"):
                    await agent_pipeline.resume_agent_generation(claim)
            else:
                await agent_pipeline.resume_agent_generation(claim)
            original_pipeline.assert_not_awaited()
            assert len(calls) == 1 and len(quota) == expected_count
        finally:
            ex._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()
            cleanup()

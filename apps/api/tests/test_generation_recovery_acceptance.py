"""Functional acceptance must survive technical green and fallback publication."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.test_generation_recovery_requirements import loaded
from tests.test_generation_recovery_requirements import receipt_case as receipt_case
from tests.test_max_finalization import _install_exact_release_probe, _new_harness
from yleum_api.core.config import get_settings
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project_cell import ProjectCellCandidate
from yleum_api.services.build_spec import FidelitySpec
from yleum_api.services.generation.recovery_requirements import store_frozen_requirements
from yleum_api.services.max_finalization import MaxFinalizationStatus


@pytest.mark.parametrize("entry", ["finalize", "set_status"])
async def test_completed_terminal_and_llm_done_admission_share_project_first_lock(
    db_session, test_engine, monkeypatch, entry
):
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from yleum_api.models.project import Project
    from yleum_api.services import generation_runs as runs
    from yleum_api.services.generation.recovery_contract import RecoveryContractError

    _harness, run, message, _state, _calls = await _canonical(db_session, test_engine, monkeypatch)
    run.response_mode = "clarify"
    message.tokens_out = 1  # the real llm.done admission fast path
    await db_session.commit()
    project_locked, release_admission, finalizer_started = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    finalizer_pid = []

    class AdmissionSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            sql = str(statement)
            if "FROM projects" in sql and "FOR UPDATE" in sql and not project_locked.is_set():
                project_locked.set()
                await release_admission.wait()
            return result

    class FinalizerSession(AsyncSession):
        async def __aenter__(self):
            await super().__aenter__()
            finalizer_pid.append(await self.scalar(text("select pg_backend_pid()")))
            await self.execute(text("SET LOCAL statement_timeout = '6000ms'"))
            finalizer_started.set()
            return self

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    finalizer_factory = async_sessionmaker(
        test_engine, class_=FinalizerSession, expire_on_commit=False
    )
    if entry == "set_status":
        monkeypatch.setattr(runs, "async_sessionmaker", lambda *_args, **_kwargs: finalizer_factory)
        monkeypatch.setattr("yleum_api.core.db.get_engine", lambda: test_engine)

    async def admission():
        async with AdmissionSession(test_engine, expire_on_commit=False) as session:
            await session.execute(text("SET LOCAL statement_timeout = '6000ms'"))
            return await runs.reserve_generation_run(
                session,
                project_id=run.project_id,
                user_id=run.user_id,
                idempotency_key="after-llm-done-lock-test",
                prompt="Next request",
            )

    async def completion():
        if entry == "set_status":
            return await runs.set_generation_run_status(run.id, "completed")
        async with finalizer_factory() as session:
            return await runs._finalize_generation_run(session, run.id)

    admitted = asyncio.create_task(admission())
    completed = None
    try:
        await asyncio.wait_for(project_locked.wait(), timeout=5)
        completed = asyncio.create_task(completion())
        await asyncio.wait_for(finalizer_started.wait(), timeout=5)
        # Observe the real PostgreSQL lock wait, not a mocked guard or a sleep
        # that might let the Run-first query race past the admission transaction.
        async with test_engine.connect() as probe:
            for _ in range(100):
                waiting = await probe.scalar(
                    text("select wait_event_type from pg_stat_activity where pid=:pid"),
                    {"pid": finalizer_pid[0]},
                )
                await probe.rollback()
                if waiting == "Lock":
                    break
                await asyncio.sleep(0.02)
            assert waiting == "Lock"
        release_admission.set()
        outcomes = await asyncio.wait_for(
            asyncio.gather(admitted, completed, return_exceptions=True), timeout=8
        )
        assert all(isinstance(value, RecoveryContractError) for value in outcomes), [
            type(value).__name__ for value in outcomes
        ]
        async with factory() as check:
            current = await check.get(GenerationRun, run.id)
            assert current.status == "running" and current.finished_at is None
            assert await check.get(Project, run.project_id) is not None
            assert (
                len(
                    list(
                        (
                            await check.scalars(
                                select(GenerationRun).where(
                                    GenerationRun.project_id == run.project_id
                                )
                            )
                        ).all()
                    )
                )
                == 1
            )
    finally:
        release_admission.set()
        for task in (admitted, completed):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (admitted, completed) if task is not None), return_exceptions=True
        )


async def test_green_build_with_missing_action_cannot_promote(db_session, test_engine, monkeypatch):
    _install_exact_release_probe(monkeypatch)
    harness = await _new_harness(db_session, test_engine, toolchain_digest="5" * 64)
    coordinator = harness.coordinator
    run = await db_session.get(GenerationRun, coordinator.generation_run_id)
    store_frozen_requirements(run, "Build tracker", FidelitySpec(), None)
    await db_session.commit()
    monkeypatch.setattr(get_settings(), "generation_recovery_mode", "on")
    result = await coordinator.finalize(files=harness.files, prompt="Build tracker")
    assert result.proof.full_build is not None and result.proof.full_build.outcome == "green"
    assert result.status is not MaxFinalizationStatus.COMPLETE
    candidates = list(
        (
            await db_session.scalars(
                select(ProjectCellCandidate).where(ProjectCellCandidate.generation_run_id == run.id)
            )
        ).all()
    )
    assert not any(c.status == "accepted" for c in candidates)
    await db_session.refresh(run)
    assert run.status != "completed"


async def test_completed_fallback_without_acceptance_never_terminalizes(
    db_session, test_engine, monkeypatch
):
    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    from yleum_api.services.generation_runs import terminalize_generation_run_locked

    harness = await _new_harness(db_session, test_engine, toolchain_digest="5" * 64)
    run = await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
    store_frozen_requirements(run, "Build tracker", FidelitySpec(), None)
    await db_session.commit()
    monkeypatch.setattr(get_settings(), "generation_recovery_mode", "on")
    with pytest.raises(RecoveryContractError):
        await terminalize_generation_run_locked(db_session, run, status="completed")
    assert run.status == "running" and run.finished_at is None


@pytest.mark.parametrize(
    "damage",
    [None, "source", "schema", "environment", "fence", "copied", "manual", "max", "prompt"],
)
async def test_current_full_manifest_only_accepts_own_server_receipts(receipt_case, damage):
    import copy

    from yleum_api.services.generation.recovery_acceptance import check_requirement_acceptance

    c = receipt_case
    witness = await loaded(c)
    binding, requirements = c.binding, c.requirements
    if damage in {"source", "schema", "environment"}:
        binding = binding.model_copy(update={damage + "_sha256": "0" * 64})
    elif damage == "fence":
        binding = binding.model_copy(update={"fencing_epoch": 8})
    elif damage == "copied":
        witness = copy.copy(witness)
    elif damage in {"manual", "max", "prompt"}:
        requirements = tuple(
            r.model_copy(
                update={
                    "check_kind": "manual"
                    if damage == "manual"
                    else "max_identity"
                    if damage == "max"
                    else r.check_kind,
                    "origin_prompt_sha256": "0" * 64
                    if damage == "prompt"
                    else r.origin_prompt_sha256,
                }
            )
            for r in requirements
        )
    result = check_requirement_acceptance(requirements, [witness], binding=binding)
    assert result.ready is (damage is None)
    if damage is not None:
        required = next(r.id for r in requirements if r.required)
        assert required in (*result.missing, *result.failed)


async def test_missing_action_and_forged_compiler_green_are_not_acceptance(receipt_case):
    from yleum_api.services.generation.recovery_acceptance import check_requirement_acceptance
    from yleum_api.services.generation.recovery_contract import RequirementWitness

    c = receipt_case
    requirement = next(r for r in c.requirements if r.required)
    assert check_requirement_acceptance(c.requirements, [], binding=c.binding).missing == (
        requirement.id,
    )
    witness = RequirementWitness(
        requirement_id=requirement.id,
        receipt_id="compiler:green",
        source_sha256=c.binding.source_sha256,
        schema_sha256=c.binding.schema_sha256,
        environment_sha256=c.binding.environment_sha256,
        passed=True,
    )
    assert not check_requirement_acceptance(c.requirements, [witness], binding=c.binding).ready


async def _canonical(db_session, test_engine, monkeypatch, *, version=2,
                     prompt="task_density_v1", admitted_run=None):
    import hashlib
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from tests.test_generation_recovery_execution import _bootstrap_wait
    from tests.test_generation_recovery_readers import install_healthy_reader_inventory
    from tests.test_max_behavior_proof import driver
    from yleum_api.models.message import Message
    from yleum_api.services import max_behavior_proof as behavior
    from yleum_api.services.generation import recovery_requirements as rr
    from yleum_api.services.generation import recovery_store
    from yleum_api.services.generation.recovery_actions import recovery_binding
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        ContinuationClaim,
        decode_recovery,
    )
    from yleum_api.services.orchestrator_client import ProjectCellWorkspaceIdentity

    install_healthy_reader_inventory(monkeypatch, refresh=True)
    _install_exact_release_probe(monkeypatch)
    harness = await _new_harness(db_session, test_engine, toolchain_digest="5" * 64,
                                 admitted_run=admitted_run)
    coordinator = harness.coordinator
    coordinator.behavior_driver, calls = driver(None)
    run = await db_session.get(GenerationRun, coordinator.generation_run_id)
    if admitted_run is not None:
        message = await db_session.get(Message, run.assistant_message_id)
        await rr.freeze_for_run(db_session, run_id=run.id, project_id=run.project_id,
                                user_id=run.user_id, prompt=prompt, template="max_miniapp")
        requirements = rr.frozen_requirements(run)
    else:
        user_message = await db_session.get(Message, run.user_message_id)
        user_message.content = prompt
        run.prompt_hash = hashlib.sha256(user_message.content.encode()).hexdigest()
        message = Message(project_id=run.project_id, role="assistant", content="")
        db_session.add(message)
        await db_session.flush()
        run.assistant_message_id = message.id
        named = behavior.required_contract(user_message.content, template="max_miniapp")
        requirements = store_frozen_requirements(run, user_message.content, FidelitySpec(), named)
    physical = await coordinator.executor.current_identity()
    binding = recovery_binding(physical)
    raw = _bootstrap_wait(run)
    ref = CheckpointRef(object_key="test/owned/context", sha256="c" * 64)
    claim = ContinuationClaim(
        run_id=run.id,
        token=uuid4(),
        attempt_seq=1,
        worker_id="acceptance-test",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    raw.update(
        phase="working",
        next_check_at=None,
        cursor="finalization",
        attempt_seq=1,
        continuation_claim=claim.model_dump(mode="json"),
        context_ref=ref.model_dump(mode="json"),
        checkpoint=ref.model_dump(mode="json"),
        binding=binding.model_dump(mode="json"),
        requirements=[r.model_dump(mode="json") for r in requirements],
    )
    raw["execution"].update(
        stage="finalization",
        substep="terminal_gate",
        physical_state="active",
        execution_context_ref=ref.model_dump(mode="json"),
    )
    if version == 3:
        from tests.test_generation_recovery_deadlines import _clock

        raw.update(version=3, clock=_clock(datetime.now(UTC) + timedelta(minutes=5)))
    state = decode_recovery(raw)
    if version == 3:
        raw = state.model_dump(mode="json")
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()

    async def current(workspace_id, *, generation_run_id, fencing_epoch):
        live = await coordinator.executor.current_identity()
        assert (workspace_id, generation_run_id, fencing_epoch) == (
            live.workspace_id,
            live.generation_run_id,
            live.fencing_epoch,
        )
        return ProjectCellWorkspaceIdentity(
            live.workspace_revision,
            live.dependency_digest,
            live.schema_data_digest,
            live.cell_manifest_digest,
            live.base_image_digest,
            live.build_config_digest,
        )

    monkeypatch.setattr(rr, "project_cell_agent_identity", current)
    monkeypatch.setattr(recovery_store, "project_cell_agent_identity", current)
    monkeypatch.setattr(get_settings(), "generation_recovery_mode", "off")
    return harness, run, message, state, calls


@pytest.mark.parametrize(
    "damage", [None, "source", "schema", "owner", "fence", "ambiguous", "partial"]
)
async def test_real_sql_receipt_loading_and_candidate_acceptance(
    db_session, test_engine, monkeypatch, damage
):
    from dataclasses import replace
    from uuid import uuid4

    from tests.test_max_behavior_proof import driver
    from yleum_api.services.generation.recovery_acceptance import require_current_acceptance
    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    from yleum_api.services.max_behavior_proof import BehaviorProofError

    harness, run, _message, state, calls = await _canonical(db_session, test_engine, monkeypatch)
    if damage == "partial":
        harness.coordinator.behavior_driver, calls = driver(None, partial=True)
    result = await harness.coordinator.finalize(files=harness.files, prompt="task_density_v1")
    if damage == "partial":
        assert result.status != MaxFinalizationStatus.COMPLETE
        assert not list(
            (
                await db_session.scalars(
                    select(ProjectCellCandidate).where(
                        ProjectCellCandidate.generation_run_id == run.id,
                        ProjectCellCandidate.status == "accepted",
                    )
                )
            ).all()
        )
        return
    assert result.status == MaxFinalizationStatus.COMPLETE
    assert "browser" in calls
    await db_session.refresh(run)
    binding = state.binding
    if damage in {"source", "schema"}:
        original = await harness.coordinator.executor.current_identity()

        async def changed():
            return replace(
                original,
                **{"workspace_revision" if damage == "source" else "schema_data_digest": "0" * 64},
            )

        harness.coordinator.executor = replace(
            harness.coordinator.executor, current_identity=changed
        )
    elif damage == "fence":
        from yleum_api.models.project_cell import ProjectCellWorkspace

        workspace = await db_session.get(
            ProjectCellWorkspace, harness.coordinator.executor.workspace_id
        )
        workspace.fencing_epoch += 1
        await db_session.commit()
    elif damage == "owner":
        from yleum_api.models.project import Project
        from yleum_api.models.user import User

        other = User(email=f"acceptance-other-{uuid4().hex}@example.com", password_hash="x")
        db_session.add(other)
        await db_session.flush()
        project = await db_session.get(Project, run.project_id)
        project.owner_id = other.id
        await db_session.commit()
    elif damage == "ambiguous":
        candidate = await db_session.get(ProjectCellCandidate, result.checkpoint.candidate_id)
        db_session.add(
            ProjectCellCandidate(
                workspace_id=candidate.workspace_id,
                generation_run_id=run.id,
                fencing_epoch=candidate.fencing_epoch,
                source_revision=candidate.source_revision,
                migration_digest=candidate.migration_digest,
                database_backup_ref=candidate.database_backup_ref,
                build_ref=candidate.build_ref,
                verification_ref=candidate.verification_ref,
                status="prepared",
                cancelled=False,
            )
        )
        await db_session.commit()
    if damage:
        with pytest.raises((RecoveryContractError, BehaviorProofError)):
            await require_current_acceptance(
                db_session, run_id=run.id, project_id=run.project_id, binding=binding
            )
    else:
        seal = await require_current_acceptance(
            db_session, run_id=run.id, project_id=run.project_id, binding=binding
        )
        assert seal["candidate_id"] == str(result.checkpoint.candidate_id)
        assert seal["prompt_hash"] == run.prompt_hash


@pytest.mark.parametrize("version,scope", [(2, "named"), (3, "named"), (3, "empty"), (3, "manual")])
async def test_saved_v2_obligations_survive_off_and_summary_fallback(
    db_session, test_engine, monkeypatch, version, scope
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from yleum_api.services.generation import agent_publication
    from yleum_api.services.generation.contracts import (
        GenerationIds,
        GenerationRuntime,
        SourceBaseline,
    )
    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    from yleum_api.services.generation_runs import terminalize_generation_run_locked

    prompt = "Build tracker" if scope == "manual" else "task_density_v1"
    harness, run, message, state, calls = await _canonical(
        db_session, test_engine, monkeypatch, version=version, prompt=prompt
    )
    monkeypatch.setattr(get_settings(), "generation_recovery_execution_enabled", False)
    monkeypatch.setattr(get_settings(), "generation_recovery_soft_windows_enabled", False)
    if scope == "empty":
        raw = state.model_dump(mode="json")
        raw["requirements"] = []
        run.agent_state = {**run.agent_state, "recovery_v1": raw}
        await db_session.commit()
    if scope == "manual":
        result = await harness.coordinator.finalize(files=harness.files, prompt=prompt)
        assert result.proof.full_build.outcome == "green"
        assert result.status is not MaxFinalizationStatus.COMPLETE
    consume = AsyncMock()
    with pytest.raises(RecoveryContractError):
        await agent_publication.publish_agent_candidate(
            _att_capture=None,
            _attestation_stack="",
            _consume_free_generation=consume,
            _max_finalization_proof=None,
            _orch_name=None,
            accumulated="done",
            baseline=SourceBaseline(None, None, {}),
            factory=harness.coordinator.session_factory,
            files={},
            ids=GenerationIds(run.id, run.project_id, run.user_id, run.user_message_id, message.id),
            model_id="synthetic",
            progress=SimpleNamespace(steps=[]),
            project_info=SimpleNamespace(template="max_miniapp"),
            prompt_text="task_density_v1",
            runtime=GenerationRuntime(
                handle=harness.coordinator.executor, coordinator=harness.coordinator
            ),
        )
    with pytest.raises(RecoveryContractError):
        await terminalize_generation_run_locked(db_session, run, status="completed")
    assert run.status == "running" and run.finished_at is None
    consume.assert_not_called()
    assert not calls


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize(
    "damage", [None, "manifest", "source", "candidate", "old_binding", "copied_run"]
)
async def test_confirmed_gated_publication_completes_after_release_without_replaying(
    db_session,
    test_engine,
    monkeypatch,
    damage,
    version,
    client,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from sqlalchemy import text

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.models.project_cell import ProjectCellWorkspace
    from yleum_api.services.generation import agent_publication as publication
    from yleum_api.services.generation import recovery_execution as ex
    from yleum_api.services.generation.contracts import (
        GenerationIds,
        GenerationRuntime,
        SourceBaseline,
    )
    from yleum_api.services.generation.recovery_contract import (
        RecoveryContractError,
    )
    from yleum_api.services.generation_runs import terminalize_generation_run_locked

    harness, run, message, state, _calls = await _canonical(
        db_session, test_engine, monkeypatch, version=version
    )
    saved_clock = run.agent_state["recovery_v1"].get("clock")
    proof = await harness.coordinator.finalize(files=harness.files, prompt="task_density_v1")
    assert proof.status == MaxFinalizationStatus.COMPLETE
    await db_session.refresh(run)
    driver = ex.ExecutionDriver(harness.coordinator.session_factory, state, {}, AsyncMock())
    commits, consumes = [], []

    def commit(*args, **kwargs):
        commits.append(True)
        return "a" * 40

    async def consume(session):
        consumes.append(True)

    monkeypatch.setattr(publication.repo_svc, "commit_files", commit)
    monkeypatch.setattr(publication, "publish_event", AsyncMock())
    monkeypatch.setattr(
        "yleum_api.services.snapshot_preview_capture.capture_snapshot_frontend", AsyncMock()
    )
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = ex._SESSION_OWNER.set(ex.SessionOwnership(run.id, owner))
        try:
            await publication.publish_agent_candidate(
                _att_capture=None,
                _attestation_stack="",
                _consume_free_generation=consume,
                _max_finalization_proof=proof.proof,
                _promotion_permit=proof.proof.permit,
                _orch_name=None,
                accumulated="done",
                baseline=SourceBaseline(None, None, {}),
                factory=harness.coordinator.session_factory,
                files=harness.files,
                ids=GenerationIds(
                    run.id, run.project_id, run.user_id, run.user_message_id, message.id
                ),
                model_id="synthetic",
                progress=SimpleNamespace(steps=[]),
                project_info=SimpleNamespace(template="max_miniapp"),
                prompt_text="task_density_v1",
                runtime=GenerationRuntime(
                    handle=harness.coordinator.executor, coordinator=harness.coordinator
                ),
                continuation=driver,
            )
        finally:
            ex._SESSION_OWNER.reset(token)
            await owner.execute(
                text("SELECT pg_advisory_unlock(1195724366,:key)"),
                {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
            )
            await owner.commit()
    await db_session.refresh(run)
    effect = await db_session.get(GenerationRecoveryEffect, publication._publication_id(run.id))
    assert effect.state == "confirmed" and effect.pre_binding["kind"] == "publication_v2"
    workspace = await db_session.get(
        ProjectCellWorkspace, harness.coordinator.executor.workspace_id
    )
    workspace.generation_run_id = None
    await db_session.commit()

    async def no_physical(*args, **kwargs):
        raise AssertionError("historical completion cannot reacquire or probe released workspace")

    monkeypatch.setattr(
        "yleum_api.services.generation.recovery_requirements.project_cell_agent_identity",
        no_physical,
    )
    if damage == "manifest":
        run.agent_state = {**run.agent_state, "recovery_requirements_v1": {}}
    elif damage == "source":
        raw = dict(run.agent_state["recovery_v1"])
        raw["binding"] = {**raw["binding"], "source_sha256": "0" * 64}
        run.agent_state = {**run.agent_state, "recovery_v1": raw}
    elif damage in {"candidate", "old_binding", "copied_run"}:
        bound = dict(effect.pre_binding)
        if damage == "candidate":
            bound["acceptance"] = {**bound["acceptance"], "candidate_id": str(uuid4())}
            bound["acceptance_sha256"] = publication._digest(bound["acceptance"])
        elif damage == "copied_run":
            bound["identity"] = {**bound["identity"], "run_id": str(uuid4())}
        else:
            bound.pop("acceptance")
            bound.pop("acceptance_sha256")
            bound["kind"] = "publication_v1"
        effect.pre_binding = bound
        effect.input_digest = publication._digest(bound)
        effect.receipt_ref = {**effect.receipt_ref, "input_digest": effect.input_digest}
        effect.receipt_sha256 = publication._digest(effect.receipt_ref)
    await db_session.commit()
    if damage:
        with pytest.raises((RecoveryContractError, ValueError)):
            await terminalize_generation_run_locked(db_session, run, status="completed")
        assert run.status == "running" and run.finished_at is None
    else:
        await terminalize_generation_run_locked(db_session, run, status="completed")
        await db_session.commit()
        assert run.status == "completed"
        await terminalize_generation_run_locked(db_session, run, status="completed")
    assert commits == consumes == [True]
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.user import User

    owner_user = await db_session.get(User, run.user_id)
    async def current_user():
        return owner_user

    app.dependency_overrides[get_current_user] = current_user
    try:
        response = await client.get(f"/api/projects/{run.project_id}/generation")
        assert response.status_code == 200
        body = response.json()
        assert body["acceptance"]["current_request_complete"] is (damage is None)
        if damage is None:
            assert body["acceptance"]["snapshot_id"] == run.agent_state["snapshot_id"]
            assert body["recovery"]["last_verified_action"] == "publication_confirmed"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    await db_session.refresh(run)
    assert run.agent_state["recovery_v1"].get("clock") == saved_clock


def test_legacy_publication_binding_is_decoded_without_new_acceptance_authority():
    from uuid import uuid4

    from yleum_api.services.generation.agent_publication import (
        PublicationBinding,
        PublicationBindingV2,
        decode_publication_binding,
    )
    from yleum_api.services.generation.recovery_contract import RecoveryBinding, RecoveryIdentity

    binding = PublicationBinding(
        identity=RecoveryIdentity(
            run_id=uuid4(),
            project_id=uuid4(),
            user_id=uuid4(),
            prompt_hash="a" * 64,
            accepted_snapshot_id=None,
            supersedes_run_id=None,
        ),
        source=RecoveryBinding(
            workspace_id=str(uuid4()),
            fencing_epoch=1,
            source_sha256="b" * 64,
            schema_sha256="c" * 64,
            environment_sha256="d" * 64,
        ),
        baseline_sha=None,
        assistant_message_id=uuid4(),
        model_id="test",
        prompt_sha256="a" * 64,
    )
    decoded = decode_publication_binding(binding.model_dump(mode="json"))
    assert type(decoded) is PublicationBinding and not isinstance(decoded, PublicationBindingV2)
    assert decoded.model_dump(mode="json") == binding.model_dump(mode="json")


async def test_footer_pointer_keyboard_and_three_viewports_have_no_width_only_oracle(receipt_case):
    from yleum_api.services.generation.recovery_acceptance import check_requirement_acceptance

    c = receipt_case
    witness = await loaded(c)
    requirements = tuple(
        r.model_copy(
            update={
                "check_kind": "browser",
                "text": (
                    "footer links uncovered, pointer and visible keyboard focus "
                    "at 390/768/1440 after edit"
                ),
            }
        )
        if r.required
        else r
        for r in c.requirements
    )
    verdict = check_requirement_acceptance(requirements, [witness], binding=c.binding)
    assert not verdict.ready and verdict.missing


@pytest.mark.parametrize("damage", ["absent", "empty", "copied_run", "cancel"])
async def test_admitted_scope_cannot_be_removed_or_borrowed(
    db_session, test_engine, monkeypatch, damage
):
    from uuid import uuid4

    from yleum_api.services.generation.recovery_acceptance import require_current_acceptance
    from yleum_api.services.generation.recovery_contract import RecoveryContractError

    _harness, run, _message, state, _calls = await _canonical(db_session, test_engine, monkeypatch)
    raw = dict(run.agent_state)
    if damage == "absent":
        raw.pop("recovery_requirements_v1")
    elif damage == "empty":
        raw["recovery_v1"] = {**raw["recovery_v1"], "requirements": []}
    elif damage == "copied_run":
        raw["recovery_v1"] = {
            **raw["recovery_v1"],
            "identity": {**raw["recovery_v1"]["identity"], "run_id": str(uuid4())},
        }
    else:
        run.status = "cancel_requested"
    run.agent_state = raw
    await db_session.commit()
    with pytest.raises(RecoveryContractError):
        await require_current_acceptance(
            db_session, run_id=run.id, project_id=run.project_id, binding=state.binding
        )

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.models.generation_billing import GenerationBillingPolicy
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.services.generation import recovery_public as public
from yleum_api.services.generation.recovery_contract import RecoveryIdentity, RecoveryState
from yleum_api.services.generation_billing import POLICY_VERSION
from yleum_api.services.promotion_permit import (
    MAX_FULL_BUILD_CONTRACT_VERSION,
    MAX_RELEASE_PROOF_CONTRACT_VERSION,
)


def records():
    user, project_id, run_id, message_id, snapshot_id = (uuid4() for _ in range(5))
    now = datetime.now(UTC)
    project = Project(id=project_id, owner_id=user, current_snapshot_id=snapshot_id)
    snapshot = Snapshot(id=snapshot_id, project_id=project_id, commit_sha="a" * 40)
    message = Message(
        id=message_id, project_id=project_id, role="assistant", snapshot_id=snapshot_id
    )
    run = GenerationRun(
        id=run_id,
        project_id=project_id,
        user_id=user,
        assistant_message_id=message_id,
        prompt_hash="b" * 64,
        status="completed",
        response_mode="build",
        created_at=now,
        finished_at=now,
        agent_state={"snapshot_id": str(snapshot_id), "commit_sha": snapshot.commit_sha},
    )
    policy = GenerationBillingPolicy(run_id=run_id, version=POLICY_VERSION, is_free=True)
    return dict(
        project=project,
        run=run,
        message=message,
        snapshot=snapshot,
        policy=policy,
        effects=[],
        user_id=user,
    )


def accepted(data):
    snapshot = data["snapshot"]
    data["run"].agent_state["billing_accepted_candidate"] = {
        "snapshot_id": str(snapshot.id),
        "commit_sha": snapshot.commit_sha,
        "permit_digest": "c" * 64,
        "proof_key": "d" * 64,
        "artifact_digest": "e" * 64,
        "build_contract": MAX_FULL_BUILD_CONTRACT_VERSION,
        "release_contract": MAX_RELEASE_PROOF_CONTRACT_VERSION,
    }


async def test_green_build_or_visible_old_snapshot_is_not_current_acceptance():
    data = records()
    view = await public.project_loaded(**data)
    assert not view.acceptance.current_request_complete
    assert view.acceptance.accepted_at is None
    assert view.acceptance.snapshot_id is None


async def test_legacy_free_accepted_candidate_is_complete_without_financial_state():
    data = records()
    accepted(data)
    view = await public.project_loaded(**data)
    assert view.acceptance.current_request_complete
    assert view.acceptance.accepted_at is None


@pytest.mark.parametrize("corrupt", [None, {}, {"version": 99}, "private diagnostic"])
async def test_present_corrupt_recovery_never_falls_back_to_legacy(corrupt):
    data = records()
    accepted(data)
    data["run"].agent_state["recovery_v1"] = corrupt
    view = await public.project_loaded(**data)
    assert view.recovery is None and view.recovery_unavailable
    assert not view.acceptance.current_request_complete
    assert "private diagnostic" not in view.model_dump_json()


@pytest.mark.parametrize(
    "phase",
    ["working", "repairing", "waiting_dependency", "reconciling", "verifying", "needs_input"],
)
async def test_strict_saved_phase_survives_with_no_raw_observations(phase):
    data = records()
    run = data["run"]
    state = RecoveryState(
        identity=RecoveryIdentity(
            run_id=run.id,
            project_id=run.project_id,
            user_id=run.user_id,
            prompt_hash=run.prompt_hash,
            accepted_snapshot_id=None,
            supersedes_run_id=None,
        ),
        phase=phase,
    )
    run.agent_state["recovery_v1"] = state.model_dump(mode="json")
    view = await public.project_loaded(**data)
    assert view.recovery.phase == phase and view.recovery.version == 1
    assert view.recovery.last_verified_action is None
    assert set(view.recovery.model_dump()) == {
        "version",
        "phase",
        "last_verified_action",
        "reason",
        "next_check_at",
        "attempt_seq",
    }
    assert run.prompt_hash not in view.model_dump_json()


@pytest.mark.parametrize("edge", ["owner_transfer", "foreign_run", "foreign_message", "wrong_role"])
async def test_projection_rechecks_owner_run_and_assistant_binding(edge):
    data = records()
    accepted(data)
    if edge == "owner_transfer":
        data["project"].owner_id = uuid4()
    if edge == "foreign_run":
        data["run"].user_id = uuid4()
    if edge == "foreign_message":
        data["message"].project_id = uuid4()
    if edge == "wrong_role":
        data["message"].role = "user"
    with pytest.raises(ApiError):
        await public.project_loaded(**data)


@pytest.mark.parametrize("edge", ["running", "unfinished", "pointer", "commit", "policy", "permit"])
async def test_legacy_accepted_marker_requires_terminal_and_exact_artifact_chain(edge):
    data = records()
    accepted(data)
    if edge == "running":
        data["run"].status = "running"
    if edge == "unfinished":
        data["run"].finished_at = None
    if edge == "pointer":
        data["project"].current_snapshot_id = uuid4()
    if edge == "commit":
        data["run"].agent_state["commit_sha"] = "f" * 40
    if edge == "policy":
        data["policy"].version = "future"
    if edge == "permit":
        data["run"].agent_state["billing_accepted_candidate"]["permit_digest"] = "not-proof"
    assert not (await public.project_loaded(**data)).acceptance.current_request_complete


def publication(data, *, no_snapshot=False):
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.generation.agent_publication import (
        PublicationBinding,
        PublicationReceipt,
        _digest,
        _publication_id,
    )
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        ExecutionCursor,
        RecoveryBinding,
        RecoveryStateV2,
    )

    run = data["run"]
    ref = CheckpointRef(object_key="synthetic/context", sha256="a" * 64)
    state = RecoveryStateV2(
        identity=RecoveryIdentity(
            run_id=run.id,
            project_id=run.project_id,
            user_id=run.user_id,
            prompt_hash=run.prompt_hash,
            accepted_snapshot_id=None,
            supersedes_run_id=None,
        ),
        cursor="finalization",
        binding=RecoveryBinding(
            source_sha256="a" * 64,
            schema_sha256="b" * 64,
            environment_sha256="c" * 64,
            workspace_id=str(uuid4()),
            fencing_epoch=1,
        ),
        checkpoint=ref,
        context_ref=ref,
        execution=ExecutionCursor(
            stage="finalization",
            substep="publication_committed",
            pending_effect_id=_publication_id(run.id),
            execution_context_ref=ref,
            physical_state="active",
        ),
    )
    bound = PublicationBinding(
        identity=state.identity,
        source=state.binding,
        baseline_sha=None,
        assistant_message_id=run.assistant_message_id,
        model_id="synthetic",
        prompt_sha256=run.prompt_hash,
    ).model_dump(mode="json")
    if no_snapshot:
        data["snapshot"] = None
        data["project"].current_snapshot_id = None
        data["message"].snapshot_id = None
    receipt = PublicationReceipt(
        input_digest=_digest(bound),
        snapshot_id=data["snapshot"].id if data["snapshot"] else None,
        commit_sha=data["snapshot"].commit_sha if data["snapshot"] else None,
        assistant_message_id=run.assistant_message_id,
    ).model_dump(mode="json")
    effect = GenerationRecoveryEffect(
        effect_id=_publication_id(run.id),
        run_id=run.id,
        project_id=run.project_id,
        user_id=run.user_id,
        kind="proof",
        payload_version=1,
        logical_step_seq=0,
        effect_ordinal=0,
        input_digest=_digest(bound),
        pre_binding=bound,
        state="confirmed",
        receipt_ref=receipt,
        receipt_sha256=_digest(receipt),
    )
    data["effects"] = [effect]
    run.agent_state["recovery_v1"] = state.model_dump(mode="json")


@pytest.mark.parametrize(
    "edge", ["valid", "copied", "digest", "running", "unfinished", "old_pointer", "no_snapshot"]
)
async def test_historical_v1_receipt_cannot_satisfy_saved_execution_acceptance(edge):
    data = records()
    publication(data, no_snapshot=edge == "no_snapshot")
    if edge == "copied":
        data["effects"][0].effect_id = uuid4()
    if edge == "digest":
        data["effects"][0].receipt_sha256 = "f" * 64
    if edge == "running":
        data["run"].status = "running"
    if edge == "unfinished":
        data["run"].finished_at = None
    if edge == "old_pointer":
        data["project"].current_snapshot_id = uuid4()
    view = await public.project_loaded(**data)
    # Saved V2/V3 requires the functional seal introduced by publication_v2.
    # A technically valid historical receipt cannot acquire that authority.
    assert view.acceptance.current_request_complete is False
    assert view.acceptance.snapshot_id is None
    assert view.recovery.last_verified_action is None


async def test_one_sql_read_refreshes_current_rows_without_financial_mutations(db_session):
    from sqlalchemy import event

    from tests.test_generation_runs import _owner_and_project
    from yleum_api.services.generation_runs import reserve_generation_run

    owner, project = await _owner_and_project(db_session)
    run, _ = await reserve_generation_run(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="public-read-one",
        prompt="synthetic",
    )
    message = Message(project_id=project.id, role="assistant", content="")
    db_session.add(message)
    await db_session.flush()
    run.assistant_message_id = message.id
    run.status = "running"
    state = RecoveryState(
        identity=RecoveryIdentity(
            run_id=run.id,
            project_id=project.id,
            user_id=owner.id,
            prompt_hash=run.prompt_hash,
            accepted_snapshot_id=None,
            supersedes_run_id=None,
        ),
        phase="waiting_dependency",
    )
    run.agent_state = {"recovery_v1": state.model_dump(mode="json")}
    await db_session.commit()
    calls = []
    engine = db_session.bind.sync_engine

    def capture(_conn, _cursor, statement, *args):
        calls.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        view = await public.project_generation_public(
            db_session, project_id=project.id, user_id=owner.id
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(calls) == 1 and calls[0].lstrip().startswith("SELECT")
    assert view.id == run.id and view.recovery.phase == "waiting_dependency"
    assert view.acceptance.current_request_complete is False
    foreign = uuid4()
    with pytest.raises(ApiError):
        await public.project_generation_public(db_session, project_id=project.id, user_id=foreign)


async def test_progress_canonical_message_survives_payload_override_after_commit(
    test_engine, monkeypatch
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.test_generation_progress_contract import _new_progress, _read_progress
    from yleum_api.services.generation import progress

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    recorder = await _new_progress(factory)
    seen = []

    async def emitted(*args):
        committed, _ = await _read_progress(recorder)
        assert committed[-1].payload["message_id"] == str(recorder.assistant_message_id)
        seen.append(args)

    monkeypatch.setattr(progress, "publish_event", emitted)
    await recorder.record_generation_event(
        "generation.recovery", {"message_id": str(uuid4()), "reason": "dependency_unavailable"}
    )
    rows, _steps = await _read_progress(recorder)
    assert rows[-1].payload["message_id"] == str(recorder.assistant_message_id)
    assert seen
    assert seen[0][2]["data"]["message_id"] == str(recorder.assistant_message_id)


@pytest.mark.parametrize("version,corrupt", [(1, False), (3, False), (3, True)])
async def test_latest_rest_projects_wait_and_denies_owner_transfer(
    client, db_session, version, corrupt
):
    from tests.test_generation_runs import _owner_and_project
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.services.generation_runs import reserve_generation_run

    owner, project = await _owner_and_project(db_session)
    run, _ = await reserve_generation_run(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="rest-wait",
        prompt="synthetic",
    )
    run.status = "running"
    state = RecoveryState(
        identity=RecoveryIdentity(
            run_id=run.id,
            project_id=project.id,
            user_id=owner.id,
            prompt_hash=run.prompt_hash,
            accepted_snapshot_id=None,
            supersedes_run_id=None,
        ),
        phase="waiting_dependency",
    )
    raw = state.model_dump(mode="json")
    if version == 3:
        from tests.test_generation_recovery_deadlines import _clock
        from tests.test_generation_recovery_execution import _bootstrap_wait
        raw = _bootstrap_wait(run) | {"version": 3, "clock": _clock(datetime.now(UTC))}
        if corrupt:
            raw["clock"]["window_ms"] = -1
    run.agent_state = {"recovery_v1": raw}
    await db_session.commit()

    async def current_user():
        return owner

    app.dependency_overrides[get_current_user] = current_user
    try:
        response = await client.get(f"/api/projects/{project.id}/generation")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(run.id)
        if corrupt:
            assert body["recovery"] is None and body["recovery_unavailable"] is True
        else:
            assert body["recovery"]["phase"] == "waiting_dependency"
        assert "interval_id" not in response.text and "clock" not in response.text
        assert body["acceptance"]["current_request_complete"] is False
        # A fresh legitimate user cannot read the former owner's current run.
        other, _ = await _owner_and_project(db_session)
        project.owner_id = other.id
        await db_session.commit()
        assert (await client.get(f"/api/projects/{project.id}/generation")).status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)

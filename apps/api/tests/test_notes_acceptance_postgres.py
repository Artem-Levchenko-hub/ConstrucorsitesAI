"""Actual owning SQL/finalizer/publication; physical/browser transport is isolated.

The separate real Chromium suite exercises the same installed runner, including
lost persistence. These tests never pretend their transport double is a DB probe.
"""

from __future__ import annotations

import copy
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.test_generation_recovery_acceptance import _canonical
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.services import max_behavior_proof as behavior
from yleum_api.services.generation import agent_publication as publication
from yleum_api.services.generation import notes_acceptance_receipts as notes_receipts
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation.contracts import GenerationIds, GenerationRuntime, SourceBaseline
from yleum_api.services.generation.notes_acceptance import CHECKS, PROMPTS, RECEIPT_KEY
from yleum_api.services.generation.recovery_acceptance import require_current_acceptance
from yleum_api.services.generation.recovery_contract import RecoveryContractError
from yleum_api.services.generation_runs import terminalize_generation_run_locked
from yleum_api.services.max_finalization import MaxFinalizationStatus
from yleum_api.services.notes_acceptance_browser import NotesDriver

_connections = []


@pytest.fixture(autouse=True)
async def close_owned_sessions():
    try:
        yield
    finally:
        while _connections:
            connection, key = _connections.pop()
            await connection.execute(
                text("select pg_advisory_unlock(1195724366,:key)"), {"key": key}
            )
            await connection.commit()
            await connection.close()


async def notes_case(db_session, test_engine, monkeypatch, prompt, *, damage=None):
    harness, run, message, state, _ = await _canonical(
        db_session, test_engine, monkeypatch, version=3, prompt=prompt
    )
    calls = []
    owner = await test_engine.connect()
    key = int.from_bytes(run.id.bytes[-4:], "big", signed=True)
    await owner.execute(text("select pg_advisory_lock(1195724366,:key)"), {"key": key})
    await owner.commit()
    _connections.append((owner, key))
    monkeypatch.setattr(
        execution,
        "_SESSION_OWNER",
        ContextVar("notes_test_owner", default=execution.SessionOwnership(run.id, owner)),
    )

    async def compiled(request):
        return behavior.CompiledAssetWitness(
            request.binding,
            (behavior.ObservedAsset("/_next/static/chunks/notes.js", "a" * 64, 128),),
            "b" * 64,
            "served_candidate_compilation_v1",
        )

    async def observe(_self, request, witness, operation_id):
        # Actual before-write SQL intent is already independently committed.
        async with harness.coordinator.session_factory() as check:
            from yleum_api.models.generation_run import GenerationRun

            current = await check.get(GenerationRun, run.id)
            record = current.agent_state[RECEIPT_KEY]
            assert record["state"] == "started"
            assert record["operation_id"] == str(operation_id)
        calls.append(operation_id)
        if damage == "unknown_after_post":
            raise RuntimeError("synthetic lost acknowledgement after one browser POST")
        outcomes = dict.fromkeys(CHECKS, True)
        if damage == "missing_persistence":
            del outcomes["fresh_context"]
        return outcomes

    monkeypatch.setattr(NotesDriver, "observe", observe)
    from yleum_api.services.generation import recovery_requirements as requirements

    monkeypatch.setattr(
        notes_receipts, "project_cell_agent_identity", requirements.project_cell_agent_identity
    )
    harness.coordinator.behavior_driver = NotesDriver(compiled, "/unused/transport-double")
    return harness, run, message, state, calls


@pytest.mark.parametrize("prompt", PROMPTS)
async def test_two_ordinary_requests_reach_owned_seal_publication_and_historical_rest(
    db_session,
    test_engine,
    monkeypatch,
    client,
    prompt,
):
    harness, run, message, state, calls = await notes_case(
        db_session, test_engine, monkeypatch, prompt
    )
    assert RECEIPT_KEY not in run.agent_state  # genuine absence permits the first observation
    initial_manifest = copy.deepcopy(run.agent_state["recovery_requirements_v1"])
    initial_clock = copy.deepcopy(run.agent_state["recovery_v1"]["clock"])
    result = await harness.coordinator.finalize(files=harness.files, prompt=prompt)
    assert result.status == MaxFinalizationStatus.COMPLETE
    await db_session.refresh(run)
    seal = await require_current_acceptance(
        db_session, run_id=run.id, project_id=run.project_id, binding=state.binding
    )
    assert seal["version"] == 2 and seal["profile"] == "notes_acceptance_v1"
    message_id = message.id
    await db_session.rollback()  # publication owns its own Project -> Run lock transaction
    driver = execution.ExecutionDriver(harness.coordinator.session_factory, state, {}, AsyncMock())
    commits, consumes = [], []

    def commit(*_args, **_kwargs):
        commits.append(True)
        return "a" * 40

    async def consume(_session):
        consumes.append(True)

    monkeypatch.setattr(publication.repo_svc, "commit_files", commit)
    monkeypatch.setattr(publication, "publish_event", AsyncMock())
    monkeypatch.setattr(
        "yleum_api.services.snapshot_preview_capture.capture_snapshot_frontend", AsyncMock()
    )
    await db_session.refresh(run)
    owner = _connections[-1][0]
    token = execution._SESSION_OWNER.set(execution.SessionOwnership(run.id, owner))
    try:
        await publication.publish_agent_candidate(
            _att_capture=None,
            _attestation_stack="",
            _consume_free_generation=consume,
            _max_finalization_proof=result.proof,
            _promotion_permit=result.proof.permit,
            _orch_name=None,
            accumulated="done",
            baseline=SourceBaseline(None, None, {}),
            factory=harness.coordinator.session_factory,
            files=harness.files,
            ids=GenerationIds(run.id, run.project_id, run.user_id, run.user_message_id, message_id),
            model_id="synthetic",
            progress=SimpleNamespace(steps=[]),
            project_info=SimpleNamespace(template="max_miniapp"),
            prompt_text=prompt,
            runtime=GenerationRuntime(
                handle=harness.coordinator.executor, coordinator=harness.coordinator
            ),
            continuation=driver,
        )
    finally:
        execution._SESSION_OWNER.reset(token)
    await db_session.refresh(run)
    effect = await db_session.get(GenerationRecoveryEffect, publication._publication_id(run.id))
    assert effect.state == "confirmed" and effect.pre_binding["kind"] == "publication_v2"
    assert effect.pre_binding["acceptance"]["version"] == 2
    workspace = await db_session.get(
        ProjectCellWorkspace, harness.coordinator.executor.workspace_id
    )
    workspace.generation_run_id = None
    await db_session.commit()
    monkeypatch.setattr(
        notes_receipts,
        "project_cell_agent_identity",
        AsyncMock(side_effect=AssertionError("historical read is not a new probe")),
    )
    await terminalize_generation_run_locked(db_session, run, status="completed")
    await db_session.commit()
    await terminalize_generation_run_locked(db_session, run, status="completed")
    assert run.status == "completed" and commits == consumes == [True] and len(calls) == 1
    assert run.agent_state["recovery_requirements_v1"] == initial_manifest
    assert run.agent_state["recovery_v1"]["clock"] == initial_clock
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.user import User

    user = await db_session.get(User, run.user_id)

    async def current_user():
        return user

    app.dependency_overrides[get_current_user] = current_user
    try:
        response = await client.get(f"/api/projects/{run.project_id}/generation")
        assert response.status_code == 200
        assert response.json()["acceptance"]["current_request_complete"] is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize("damage", ["unknown_after_post", "missing_persistence"])
async def test_unknown_or_incomplete_probe_never_replays_or_publishes(
    db_session,
    test_engine,
    monkeypatch,
    damage,
):
    harness, run, _, state, calls = await notes_case(
        db_session, test_engine, monkeypatch, PROMPTS[0], damage=damage
    )
    result = await harness.coordinator.finalize(files=harness.files, prompt=PROMPTS[0])
    assert result.status != MaxFinalizationStatus.COMPLETE
    await db_session.refresh(run)
    assert run.agent_state[RECEIPT_KEY]["state"] == "started" and len(calls) == 1
    from yleum_api.models.project_cell import ProjectCellActivityLease

    lease = await db_session.get(ProjectCellActivityLease, calls[0])
    assert lease.state == "active"
    with pytest.raises((RecoveryContractError, behavior.BehaviorProofError)):
        await require_current_acceptance(
            db_session, run_id=run.id, project_id=run.project_id, binding=state.binding
        )
    await db_session.rollback()
    again = await harness.coordinator.finalize(files=harness.files, prompt=PROMPTS[0])
    assert again.status != MaxFinalizationStatus.COMPLETE and len(calls) == 1


@pytest.mark.parametrize(
    "damage",
    [
        "foreign_owner",
        "foreign_candidate",
        "missing_context",
        "source",
        "forged_aggregate",
        "manual_extra",
    ],
)
async def test_notes_aggregate_cannot_cover_foreign_or_unobserved_scope(
    db_session,
    test_engine,
    monkeypatch,
    damage,
):
    prompt = (
        PROMPTS[0] + " Отправляй уведомления по email." if damage == "manual_extra" else PROMPTS[0]
    )
    harness, run, _, state, calls = await notes_case(db_session, test_engine, monkeypatch, prompt)
    result = await harness.coordinator.finalize(files=harness.files, prompt=prompt)
    if damage == "manual_extra":
        assert result.status != MaxFinalizationStatus.COMPLETE and calls == []
        return
    assert result.status == MaxFinalizationStatus.COMPLETE
    await db_session.refresh(run)
    record = copy.deepcopy(run.agent_state[RECEIPT_KEY])
    receipt = record["receipt"]
    if damage == "foreign_owner":
        receipt["owner_id"] = str(uuid4())
    elif damage == "foreign_candidate":
        receipt["binding"]["candidate_id"] = str(uuid4())
    elif damage == "missing_context":
        del receipt["outcomes"]["fresh_context"]
    elif damage == "source":
        receipt["source_binding"]["source_sha256"] = "0" * 64
    else:
        receipt["contract"]["coverage"] = []
    # Recomputing public hashes is insufficient: owning binding and all checks
    # are reloaded independently, rather than trusting a syntactic PASS object.
    receipt["receipt_digest"] = behavior.digest(
        {k: v for k, v in receipt.items() if k != "receipt_digest"}
    )
    record["envelope"] = {key: receipt[key] for key in record["envelope"]}
    run.agent_state = {**run.agent_state, RECEIPT_KEY: record}
    await db_session.commit()
    with pytest.raises((RecoveryContractError, behavior.BehaviorProofError)):
        await require_current_acceptance(
            db_session, run_id=run.id, project_id=run.project_id, binding=state.binding
        )


@pytest.mark.parametrize("change", ["claim", "cancel", "fence", "session", "drain"])
async def test_current_authority_loss_before_notes_write_denies_without_replay(
    db_session,
    test_engine,
    monkeypatch,
    change,
):
    harness, run, _, _state, calls = await notes_case(
        db_session, test_engine, monkeypatch, PROMPTS[0]
    )
    original = harness.coordinator.behavior_driver.collect_compiled

    async def compiled(request):
        result = await original(request)
        async with harness.coordinator.session_factory() as session:
            from yleum_api.models.generation_run import GenerationRun

            current = await session.get(GenerationRun, run.id)
            if change == "claim":
                root = copy.deepcopy(current.agent_state)
                root["recovery_v1"]["continuation_claim"]["token"] = str(uuid4())
                current.agent_state = root
            elif change == "cancel":
                current.status = "cancel_requested"
            elif change == "fence":
                workspace = await session.get(ProjectCellWorkspace, request.binding.workspace_id)
                workspace.fencing_epoch += 1
            elif change == "session":
                execution._SESSION_OWNER.set(None)
            else:
                from yleum_api.services.generation_deployment_drain import begin_drain

                await begin_drain(session, "c" * 40)
            await session.commit()
        return result

    harness.coordinator.behavior_driver = NotesDriver(compiled, "/unused/transport-double")
    result = await harness.coordinator.finalize(files=harness.files, prompt=PROMPTS[0])
    assert result.status != MaxFinalizationStatus.COMPLETE
    assert calls == []
    await db_session.refresh(run)
    assert run.agent_state[RECEIPT_KEY]["state"] == "started"
    from uuid import UUID

    from yleum_api.models.project_cell import ProjectCellActivityLease

    lease = await db_session.get(
        ProjectCellActivityLease, UUID(run.agent_state[RECEIPT_KEY]["operation_id"])
    )
    assert lease.state == "failed"


@pytest.mark.parametrize("stored", [None, {}, [], False])
async def test_present_malformed_notes_record_never_becomes_a_fresh_attempt(
    db_session,
    test_engine,
    monkeypatch,
    stored,
):
    harness, run, _, _state, calls = await notes_case(
        db_session, test_engine, monkeypatch, PROMPTS[0]
    )
    reserve = AsyncMock(wraps=notes_receipts.start_activity)
    monkeypatch.setattr(notes_receipts, "start_activity", reserve)
    run.agent_state = {**run.agent_state, RECEIPT_KEY: stored}
    await db_session.commit()
    result = await harness.coordinator.finalize(files=harness.files, prompt=PROMPTS[0])
    assert result.status != MaxFinalizationStatus.COMPLETE
    assert calls == []
    await db_session.refresh(run)
    reserve.assert_not_awaited()
    assert RECEIPT_KEY in run.agent_state and run.agent_state[RECEIPT_KEY] == stored

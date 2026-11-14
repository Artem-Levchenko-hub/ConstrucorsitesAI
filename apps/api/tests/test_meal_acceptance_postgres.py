"""Owning PostgreSQL accepts only settled, exact Meal candidate observations."""

import json
from contextvars import ContextVar
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from tests.test_generation_recovery_acceptance import _canonical
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellCandidate,
    ProjectCellProof,
    ProjectCellWorkspace,
)
from yleum_api.models.snapshot import Snapshot
from yleum_api.services import max_behavior_proof as behavior
from yleum_api.services.generation import agent_publication as publication
from yleum_api.services.generation import meal_acceptance_receipts as receipts
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation import recovery_requirements
from yleum_api.services.generation.meal_acceptance import (
    BUILD_PROMPT,
    EDIT_PROMPT,
    STATE_KEY,
    reviewed_meal_contract,
)
from yleum_api.services.generation.recovery_acceptance import (
    MealAcceptanceSeal,
    require_current_acceptance,
    validate_historical_acceptance,
)
from yleum_api.services.generation.recovery_actions import recovery_binding
from yleum_api.services.generation.recovery_contract import RecoveryContractError
from yleum_api.services.max_finalization import MaxFinalizationStatus


def _fixture(project_id, fixture_id):
    return {
        "shopping": [
            {"id": "row-a", "ingredient_id": "ing-a", "name": "Овёс", "bought": True},
            {"id": "row-b", "ingredient_id": "ing-b", "name": "Рис", "bought": False},
        ],
        "checkout": {
            "order_id": str(receipts._fixture_order(project_id, fixture_id)),
            "recipe_ids": [f"meal-{i}" for i in range(10)],
        },
    }


async def _assert_prior_published_fixture(session, run, message, state, seal, candidate):
    snapshot = Snapshot(project_id=run.project_id, commit_sha="a" * 40)
    session.add(snapshot)
    await session.flush()
    project = await session.get(Project, run.project_id)
    project.current_snapshot_id = snapshot.id
    message.snapshot_id = snapshot.id
    run.status = "completed"
    effect_id = publication._publication_id(run.id)
    bound = publication.PublicationBindingV2(
        identity=state.identity,
        source=state.binding,
        baseline_sha=None,
        assistant_message_id=run.assistant_message_id,
        model_id="fixture",
        prompt_sha256=run.prompt_hash,
        acceptance=seal,
        acceptance_sha256=publication._digest(seal.model_dump(mode="json")),
    ).model_dump(mode="json")
    input_digest = publication._digest(bound)
    receipt = publication.PublicationReceipt(
        input_digest=input_digest,
        snapshot_id=snapshot.id,
        commit_sha=snapshot.commit_sha,
        assistant_message_id=run.assistant_message_id,
    ).model_dump(mode="json")
    session.add(
        GenerationRecoveryEffect(
            effect_id=effect_id,
            run_id=run.id,
            project_id=run.project_id,
            user_id=run.user_id,
            logical_step_seq=state.execution.logical_step_seq,
            effect_ordinal=0,
            kind="proof",
            input_digest=input_digest,
            state="confirmed",
            attempt_seq=state.attempt_seq,
            claim_token=state.continuation_claim.token,
            pre_binding=bound,
            receipt_ref=receipt,
            receipt_sha256=publication._digest(receipt),
        )
    )
    raw = run.agent_state["recovery_v1"]
    run.agent_state = {
        **run.agent_state,
        "snapshot_id": str(snapshot.id),
        "commit_sha": snapshot.commit_sha,
        "recovery_v1": {
            **raw,
            "execution": {
                **raw["execution"],
                "substep": "publication_committed",
                "pending_effect_id": str(effect_id),
            },
        },
    }
    candidate.status = "rejected"  # A later accepted candidate can replace this row.
    await session.commit()
    subsequent = GenerationRun(project_id=run.project_id, user_id=run.user_id)
    subsequent_state = state.model_copy(
        update={
            "identity": state.identity.model_copy(
                update={"accepted_snapshot_id": snapshot.id},
            )
        }
    )
    assert (
        await receipts._prior_fixture(session, subsequent, subsequent_state)
        == (run.agent_state[STATE_KEY]["receipt"]["fixture"])
    )
    message.snapshot_id = None
    await session.flush()
    with pytest.raises(RecoveryContractError, match="publication snapshot ownership changed"):
        await receipts._prior_fixture(session, subsequent, subsequent_state)


@pytest.mark.parametrize(
    "prompt,mode", [(BUILD_PROMPT, "build"), (EDIT_PROMPT, "edit")], ids=["build", "edit"]
)
@pytest.mark.parametrize(
    "outcome",
    [
        "green",
        "red",
        "unknown",
        "mint-failed",
        "cleanup-closed",
        "postcheck-failed",
    ],
)
async def test_meal_finalizer_seals_only_current_exact_candidate(
    db_session,
    test_engine,
    monkeypatch,
    prompt,
    mode,
    outcome,
):
    harness, run, message, state, _ = await _canonical(
        db_session,
        test_engine,
        monkeypatch,
        version=3,
        prompt=prompt,
    )
    run.response_mode = mode
    run_id, project_id = run.id, run.project_id
    raw_state = run.agent_state["recovery_v1"]
    claim = {
        **raw_state["continuation_claim"],
        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    run.agent_state = {**run.agent_state, "recovery_v1": {**raw_state, "continuation_claim": claim}}
    await db_session.commit()
    contract = reviewed_meal_contract(run, "max_miniapp")
    assert contract is not None and contract.mode == mode
    assert ("shopping_filters" in contract.checks) == (mode == "edit")
    fixture_id = uuid4()
    fixture = _fixture(project_id, fixture_id)
    prior = {**fixture, "id": str(fixture_id)} if mode == "edit" else None
    calls = []
    compilations = []
    prepares = []
    if mode == "edit":
        monkeypatch.setattr(receipts, "_prior_fixture", AsyncMock(return_value=prior))

    async def compiled(request):
        compilations.append(True)
        if outcome == "postcheck-failed" and len(compilations) == 2:
            raise behavior.BehaviorProofError("BEHAVIOR_COMPILATION_UNAVAILABLE")
        return behavior.CompiledAssetWitness(
            request.binding,
            (behavior.ObservedAsset("/_next/static/chunks/meal.js", "a" * 64, 128),),
            "b" * 64,
            "served_candidate_compilation_v1",
        )

    async def prepare(_self, request, witness, operation):
        prepares.append(operation)
        if outcome == "mint-failed" and len(prepares) == 1:
            raise behavior.BehaviorProofError("MEAL_ACCEPTANCE_SESSION_UNAVAILABLE")
        return request, witness, operation

    async def observe(_self, payload):
        request, witness, operation = payload
        calls.append(operation)
        assert request.contract == contract and witness.binding == request.binding
        assert request.prior == prior
        async with harness.coordinator.session_factory() as session:
            current = await session.get(GenerationRun, run_id)
            pending = current.agent_state[STATE_KEY]
            assert pending["state"] == "started" and pending["operation"] == str(operation)
        if outcome == "red":
            return {"status": "RED", "check": "pdf_download", "cleanup_closed": True}
        if outcome == "cleanup-closed":
            return {"status": "ERROR", "cleanup_closed": True}
        return {
            "status": "PASS",
            "outcomes": dict.fromkeys(contract.checks, True),
            "fixture": _fixture(project_id, request.fixture_id(operation)),
            "cleanup_closed": outcome in {"green", "mint-failed", "postcheck-failed"},
        }

    monkeypatch.setattr(
        receipts, "project_cell_agent_identity", recovery_requirements.project_cell_agent_identity
    )
    monkeypatch.setattr(receipts.MealDriver, "observe", observe)
    monkeypatch.setattr(receipts.MealDriver, "prepare", prepare)
    monkeypatch.setattr(
        "yleum_api.services.max_finalization.max_source_completion_gap",
        lambda *a, **kw: None,
    )
    harness.coordinator.behavior_driver = receipts.MealDriver(compiled, "/isolated/fixture")
    async with test_engine.connect() as owner:
        key = int.from_bytes(run.id.bytes[-4:], "big", signed=True)
        await owner.execute(text("select pg_advisory_lock(1195724366,:key)"), {"key": key})
        await owner.commit()
        monkeypatch.setattr(
            execution,
            "_SESSION_OWNER",
            ContextVar(
                "meal_test_owner",
                default=execution.SessionOwnership(run.id, owner),
            ),
        )
        try:
            first = await harness.coordinator.finalize(files=harness.files, prompt=prompt)
            if outcome == "mint-failed":
                assert first.status == MaxFinalizationStatus.FAILED and calls == []
                assert first.redacted_detail == "MEAL_ACCEPTANCE_SESSION_UNAVAILABLE"
                await db_session.refresh(run)
                assert STATE_KEY not in run.agent_state
                assert await db_session.get(ProjectCellActivityLease, prepares[0]) is None
                await db_session.rollback()
            second = await harness.coordinator.finalize(files=harness.files, prompt=prompt)
            assert len(calls) == 1
            await db_session.refresh(run)
            candidates = (
                await db_session.scalars(
                    select(ProjectCellCandidate).where(
                        ProjectCellCandidate.generation_run_id == run.id,
                    )
                )
            ).all()
            assert len(candidates) == 1
            if outcome in {"green", "mint-failed"}:
                assert second.status == MaxFinalizationStatus.COMPLETE
                if outcome == "green":
                    assert first.status == MaxFinalizationStatus.COMPLETE
                assert candidates[0].status == "accepted"
                raw = await require_current_acceptance(
                    db_session,
                    run_id=run.id,
                    project_id=run.project_id,
                    binding=state.binding,
                )
                assert raw["run_id"] == str(run.id) and raw["version"] == 3
                seal = MealAcceptanceSeal.model_validate_json(json.dumps(raw))
                await validate_historical_acceptance(
                    db_session,
                    run=run,
                    binding=state.binding,
                    seal=seal,
                )
                with pytest.raises(RecoveryContractError):
                    await validate_historical_acceptance(
                        db_session,
                        run=run,
                        binding=state.binding,
                        seal=seal.model_copy(update={"candidate_id": uuid4()}),
                    )
                if outcome == "green" and mode == "build":
                    monkeypatch.setattr(receipts, "runner_digest", lambda: "c" * 64)
                    await _assert_prior_published_fixture(
                        db_session,
                        run,
                        message,
                        state,
                        seal,
                        candidates[0],
                    )
            else:
                assert candidates[0].status == "prepared"
                status = (
                    MaxFinalizationStatus.NEEDS_EDIT
                    if outcome == "red"
                    else MaxFinalizationStatus.FAILED
                )
                assert first.status == second.status == status
                if outcome == "red":
                    assert first.redacted_detail == "MEAL_ACCEPTANCE_FAILED:pdf_download"
                if outcome in {"unknown", "cleanup-closed"}:
                    activity = await db_session.get(ProjectCellActivityLease, calls[0])
                    assert activity.state == ("active" if outcome == "unknown" else "failed")
                if outcome == "postcheck-failed":
                    assert first.redacted_detail == "BEHAVIOR_COMPILATION_UNAVAILABLE"
                    assert second.redacted_detail == "MEAL_ACCEPTANCE_OUTCOME_UNKNOWN"
                    activity = await db_session.get(ProjectCellActivityLease, calls[0])
                    assert activity.state == "completed"
                with pytest.raises(behavior.BehaviorProofError):
                    await require_current_acceptance(
                        db_session,
                        run_id=run.id,
                        project_id=run.project_id,
                        binding=state.binding,
                    )
        finally:
            await db_session.rollback()
            await owner.execute(text("select pg_advisory_unlock(1195724366,:key)"), {"key": key})
            await owner.commit()


def test_meal_runbook_has_exact_reviewed_briefs():
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "tools/qa/max_meal_acceptance.md"
    assert [
        line[2:] for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("> ")
    ] == [BUILD_PROMPT, EDIT_PROMPT]


@pytest.mark.parametrize("first_red", [False, True], ids=["confirmed-then-rebind", "red-a-b-a"])
async def test_meal_cold_candidate_recovery(db_session, test_engine, monkeypatch, first_red):
    harness, run, _, recovery, _ = await _canonical(
        db_session,
        test_engine,
        monkeypatch,
        version=3,
        prompt=BUILD_PROMPT,
    )
    run.response_mode = "build"
    await db_session.commit()
    coordinator = harness.coordinator
    base = await coordinator.executor.current_identity()
    source = base
    operations = []
    fixture_ids = []

    async def current():
        return source

    async def compiled(request):
        return behavior.CompiledAssetWitness(
            request.binding,
            (behavior.ObservedAsset("/_next/static/chunks/meal.js", "a" * 64, 128),),
            "b" * 64,
            "served_candidate_compilation_v1",
        )

    async def prepare(_self, request, _witness, operation):
        return request, operation

    async def observe(_self, payload):
        request, operation = payload
        operations.append(operation)
        fixture_id = request.fixture_id(operation)
        fixture_ids.append(fixture_id)
        if first_red:
            return {"status": "RED", "check": "pdf_download", "cleanup_closed": True}
        return {
            "status": "PASS",
            "outcomes": dict.fromkeys(request.contract.checks, True),
            "fixture": _fixture(run.project_id, fixture_id),
            "cleanup_closed": True,
        }

    async def candidate(identity):
        workspace = await db_session.get(ProjectCellWorkspace, identity.workspace_id)
        workspace.fencing_epoch = identity.fencing_epoch
        run.agent_state = {
            **run.agent_state,
            "recovery_v1": {
                **recovery.model_dump(mode="json"),
                "binding": recovery_binding(identity).model_dump(mode="json"),
            },
        }
        item = ProjectCellCandidate(
            workspace_id=identity.workspace_id,
            generation_run_id=run.id,
            fencing_epoch=identity.fencing_epoch,
            source_revision=identity.workspace_revision,
            migration_digest="d" * 64,
            database_backup_ref="database-backup/sha256/" + "a" * 64,
            build_ref="build/sha256/" + "e" * 64,
            verification_ref="verification/sha256/" + "b" * 64,
            status="prepared",
            cancelled=False,
        )
        db_session.add_all(
            [item, ProjectCellProof(**asdict(identity), proof_key=identity.proof_key)]
        )
        await db_session.commit()
        return item

    monkeypatch.setattr(coordinator, "_identity", current)
    monkeypatch.setattr(receipts.MealDriver, "observe", observe)
    monkeypatch.setattr(receipts.MealDriver, "prepare", prepare)
    coordinator.behavior_driver = receipts.MealDriver(compiled, "/isolated/fixture")
    item_a = await candidate(base)
    async with test_engine.connect() as owner:
        key = int.from_bytes(run.id.bytes[-4:], "big", signed=True)
        await owner.execute(text("select pg_advisory_lock(1195724366,:key)"), {"key": key})
        await owner.commit()
        monkeypatch.setattr(
            execution,
            "_SESSION_OWNER",
            ContextVar(
                "meal_cold_test_owner",
                default=execution.SessionOwnership(run.id, owner),
            ),
        )
        try:
            if first_red:
                with pytest.raises(receipts.MealVerificationFailed) as first:
                    await receipts.require_meal_observation(
                        coordinator, item_a, base, item_a.build_ref
                    )
                assert first.value.code == "MEAL_ACCEPTANCE_FAILED:pdf_download"
            else:
                await receipts.require_meal_observation(coordinator, item_a, base, item_a.build_ref)
            await db_session.refresh(run)
            source = replace(
                base,
                fencing_epoch=base.fencing_epoch + 1,
                workspace_revision="f" * 64 if first_red else base.workspace_revision,
            )
            item_b = await candidate(source)
            if first_red:
                with pytest.raises(receipts.MealVerificationFailed):
                    await receipts.require_meal_observation(
                        coordinator, item_b, source, item_b.build_ref
                    )
                await db_session.refresh(run)
                source = replace(base, fencing_epoch=source.fencing_epoch + 1)
                back = await candidate(source)
                with pytest.raises(receipts.MealVerificationFailed) as repeated:
                    await receipts.require_meal_observation(
                        coordinator, back, source, back.build_ref
                    )
                assert repeated.value.code == first.value.code
                assert len(set(fixture_ids)) == 2
            else:
                assert item_a.status == "prepared"
                await receipts.require_meal_observation(
                    coordinator, item_b, source, item_b.build_ref
                )
                await db_session.refresh(run)
                receipt = run.agent_state[STATE_KEY]["receipt"]
                assert receipt["binding"]["candidate_id"] == str(item_b.id)
                assert receipt["binding"]["generation_run_id"] == str(run.id)
                assert receipt["fixture"]["id"] == str(fixture_ids[0])
                assert len(set(fixture_ids)) == 1
            assert len(set(operations)) == 2
        finally:
            await db_session.rollback()
            await owner.execute(text("select pg_advisory_unlock(1195724366,:key)"), {"key": key})
            await owner.commit()

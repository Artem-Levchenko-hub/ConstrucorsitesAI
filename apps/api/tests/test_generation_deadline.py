"""Generation work has no lifetime; terminal controller hand-offs stay bounded.

Editing and repairs may span many healthy operations. Individual operations,
cancellation and sealed proof recovery keep their own finite safety contracts.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.test_max_finalization import (
    _CLIENTS_ROUTE,
    _VISITS_ROUTE,
    _adaptation_run,
    _install_exact_release_probe,
    _new_harness,
)
from yleum_api.core.config import get_settings
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project_cell import ProjectCellActivityLease, ProjectCellWorkspace
from yleum_api.models.restoration import Restoration
from yleum_api.services.generation_deadline import (
    GenerationDeadline,
    generation_deadline,
    note_proof_sealed,
    note_proof_settled,
    note_repair_stage_started,
)
from yleum_api.services.max_finalization import (
    generation_deadline_wait,
    run_generation_deadline_watchdog,
    watch_generation_deadline,
)
from yleum_api.services.orchestrator_client import RestorationAdaptationProof
from yleum_api.services.project_cell_proofs import ProofIdentity

_T0 = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
_ACTIVATION = timedelta(seconds=2400)


def _run(*, adaptation: bool = True, status: str = "running") -> GenerationRun:
    run_id = uuid.uuid4()
    state: dict[str, object] = {}
    if adaptation:
        state["restoration_adaptation"] = {
            "operation_id": str(uuid.uuid4()),
            "adaptation_run_id": str(run_id),
        }
    return GenerationRun(
        id=run_id,
        project_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        idempotency_key="k",
        prompt_hash="a" * 64,
        status=status,
        agent_state=state,
        created_at=_T0,
        started_at=_T0,
    )


def _seal(run: GenerationRun) -> None:
    root = dict(run.agent_state)
    state = dict(root.get("max_finalization") or {})
    state["restoration_adaptation_proof_attempt"] = {"number": 1, "status": "issued"}
    root["max_finalization"] = state
    run.agent_state = root


def _settle(run: GenerationRun, proof_state: str) -> None:
    root = dict(run.agent_state)
    state = dict(root["max_finalization"])
    state["restoration_adaptation_proof_attempt"] = {"number": 1, "status": "completed"}
    state["restoration_adaptation_proof"] = {"state": proof_state}
    root["max_finalization"] = state
    run.agent_state = root


def test_only_the_controller_hand_off_has_a_generation_level_ceiling() -> None:
    settings = get_settings()
    # The hand-off ceiling has to outlast the controller's own prove + offer + apply.
    assert settings.restoration_adaptation_activation_seconds >= 900 + 120 + 930


def test_a_progressing_ordinary_run_has_no_wall_clock_deadline() -> None:
    run = _run(adaptation=False)

    deadline = generation_deadline(run)

    assert (deadline.stage, deadline.at) == ("edit", None)
    assert "max_finalization" not in run.agent_state or "deadline" not in dict(
        run.agent_state["max_finalization"]  # type: ignore[arg-type]
    )


def test_a_progressing_adaptation_edit_has_no_wall_clock_deadline() -> None:
    deadline = generation_deadline(_run())
    assert (deadline.stage, deadline.at) == ("edit", None)


def test_a_progressing_repair_has_no_wall_clock_deadline() -> None:
    run = _run()
    note_repair_stage_started(run, _T0 + timedelta(seconds=1440))

    deadline = generation_deadline(run)

    assert (deadline.stage, deadline.at) == ("repair", None)


def test_the_repair_window_opens_once() -> None:
    run = _run()
    note_repair_stage_started(run, _T0 + timedelta(seconds=1440))
    note_repair_stage_started(run, _T0 + timedelta(seconds=2000))

    book = dict(run.agent_state["max_finalization"])["deadline"]  # type: ignore[arg-type]
    assert book["repair_started_at_ms"] == int(
        (_T0 + timedelta(seconds=1440)).timestamp() * 1000
    )


def test_a_sealed_intent_gets_its_own_ceiling_not_the_editing_one() -> None:
    run = _run()
    note_repair_stage_started(run, _T0 + timedelta(seconds=1440))
    _seal(run)
    note_proof_sealed(run, _T0 + timedelta(seconds=1700))

    deadline = generation_deadline(run)

    assert deadline.stage == "proof"
    # Far past the editing limit, but not unbounded: a stuck hand-off still releases
    # the Project Cell instead of blocking the project forever.
    assert deadline.at == _T0 + timedelta(seconds=1700) + _ACTIVATION


def test_a_seal_without_its_mark_is_still_bounded() -> None:
    run = _run()
    note_repair_stage_started(run, _T0 + timedelta(seconds=1440))
    _seal(run)

    deadline = generation_deadline(run)

    assert deadline.stage == "proof"
    assert deadline.at == _T0 + _ACTIVATION


@pytest.mark.parametrize("proof_state", ["proof_ready", "migration_required"])
def test_only_an_unsettled_proof_holds_the_deadline_off(proof_state: str) -> None:
    run = _run()
    _seal(run)
    _settle(run, proof_state)

    deadline = generation_deadline(run)

    # proof_ready seals the activation that follows; a rejected proof returns to repairs.
    assert (deadline.stage == "proof") is (proof_state == "proof_ready")


def test_a_finished_activation_no_longer_seals() -> None:
    run = _run()
    _seal(run)
    _settle(run, "proof_ready")
    run.agent_state = {
        **run.agent_state,
        "restoration_adaptation_activation": {"state": "cancelled"},
    }

    assert generation_deadline(run) == GenerationDeadline("edit", None)


@pytest.mark.parametrize("adaptation", [False, True])
def test_a_requested_cancel_remains_bounded(adaptation: bool) -> None:
    run = _run(adaptation=adaptation, status="cancel_requested")

    deadline = generation_deadline(run)

    assert (deadline.stage, deadline.at) == ("edit", _T0)


def test_a_rejected_proof_resumes_unbounded_repairs() -> None:
    run = _run()
    note_repair_stage_started(run, _T0 + timedelta(seconds=1440))
    _seal(run)
    note_proof_sealed(run, _T0 + timedelta(seconds=1700))
    note_proof_sealed(run, _T0 + timedelta(seconds=1900))  # a replayed intent keeps the first mark
    _settle(run, "migration_required")
    note_proof_settled(run, _T0 + timedelta(seconds=2300))

    deadline = generation_deadline(run)

    assert (deadline.stage, deadline.at) == ("repair", None)
    note_proof_settled(run, _T0 + timedelta(seconds=9000))
    assert generation_deadline(run) == deadline


def test_a_damaged_book_cannot_restore_a_wall_clock_limit() -> None:
    run = _run()
    run.agent_state = {
        **run.agent_state,
        "max_finalization": {"deadline": {"repair_started_at_ms": "soon", "sealed_ms": -5}},
    }

    assert generation_deadline(run).at is None


@pytest.fixture
def _exact_release_probe(monkeypatch: pytest.MonkeyPatch):
    cleanup = _install_exact_release_probe(monkeypatch)
    yield
    cleanup()


async def _bound_adaptation(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> tuple[GenerationRun, Restoration, async_sessionmaker[AsyncSession]]:
    harness = await _new_harness(db_session, test_engine)
    run = await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
    assert run is not None
    workspace = await db_session.scalar(
        select(ProjectCellWorkspace).where(ProjectCellWorkspace.generation_run_id == run.id)
    )
    assert workspace is not None
    operation = Restoration(
        id=uuid.uuid4(),
        project_id=run.project_id,
        owner_id=run.user_id,
        workspace_id=workspace.id,
        source_version_id=uuid.uuid4(),
        source_snapshot_id=uuid.uuid4(),
        base_draft_snapshot_id=uuid.uuid4(),
        target_commit_sha="a" * 40,
        base_commit_sha="b" * 40,
        idempotency_key=f"deadline-{run.id}",
        request_digest="c" * 64,
        selected_branch="adaptive",
        adaptation_run_id=run.id,
        state="adapting",
        phase="generation",
        revision=2,
        fencing_epoch=workspace.fencing_epoch,
        request_payload={},
    )
    db_session.add(operation)
    run.agent_state = {
        "restoration_adaptation": {
            "operation_id": str(operation.id),
            "adaptation_run_id": str(run.id),
        }
    }
    await db_session.commit()
    return run, operation, async_sessionmaker(test_engine, expire_on_commit=False)


async def test_a_progressing_adaptation_is_not_terminalized_by_elapsed_time(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    run, _operation, factory = await _bound_adaptation(db_session, test_engine)

    expired = await watch_generation_deadline(
        session_factory=factory,
        generation_run_id=run.id,
        now=datetime.now(UTC) + timedelta(hours=1),
    )

    await db_session.refresh(run)
    assert expired is False
    assert (run.status, run.error, run.finished_at) == ("running", None, None)


async def test_a_lost_cancel_signal_terminalizes_the_running_command(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    run, _operation, factory = await _bound_adaptation(db_session, test_engine)
    workspace = await db_session.scalar(
        select(ProjectCellWorkspace).where(ProjectCellWorkspace.generation_run_id == run.id)
    )
    assert workspace is not None
    now = datetime.now(UTC)
    lease = ProjectCellActivityLease(
        operation_id=uuid.uuid4(),
        workspace_id=workspace.id,
        generation_run_id=run.id,
        kind="command",
        state="active",
        fencing_epoch=workspace.fencing_epoch,
        proof_key="7" * 64,
        phase="final_build",
        started_at=now,
        heartbeat_at=now,
        deadline_at=now + timedelta(minutes=5),
    )
    db_session.add(lease)
    run.status = "cancel_requested"
    await db_session.commit()

    cancelled = await watch_generation_deadline(
        session_factory=factory, generation_run_id=run.id, now=now + timedelta(hours=1)
    )

    await db_session.refresh(run)
    await db_session.refresh(lease)
    assert cancelled is True and run.status == "cancelled"
    assert lease.state == "cancelled"
    assert run.error is not None and run.error.startswith("generation cancelled; ")
    assert f"agent_operation_id={lease.operation_id}; " in run.error


async def test_a_sealed_proof_intent_is_not_cut_by_the_deadline(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    run, operation, factory = await _bound_adaptation(db_session, test_engine)
    _seal(run)
    sealed_at = datetime.now(UTC)
    note_proof_sealed(run, sealed_at)
    operation.state, operation.phase = "applying", "activation_proof_intent"
    await db_session.commit()
    # Far past the ordinary limit, and still inside the hand-off ceiling.
    late = sealed_at + _ACTIVATION - timedelta(minutes=1)

    expired = await watch_generation_deadline(
        session_factory=factory, generation_run_id=run.id, now=late
    )
    wait = await generation_deadline_wait(
        session_factory=factory, generation_run_id=run.id, now=late
    )

    await db_session.refresh(run)
    await db_session.refresh(operation)
    assert expired is False
    assert (run.status, run.error, run.finished_at) == ("running", None, None)
    assert (operation.state, operation.phase) == ("applying", "activation_proof_intent")
    assert "restoration_adaptation_owner_status" not in run.agent_state
    # The watchdog keeps looking: a rejected proof returns to unbounded repair.
    assert wait is not None and 0 < wait <= 60


async def test_a_stuck_hand_off_is_released_with_its_proof_retained(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    run, operation, factory = await _bound_adaptation(db_session, test_engine)
    _seal(run)
    sealed_at = datetime.now(UTC)
    note_proof_sealed(run, sealed_at)
    operation.state, operation.phase = "applying", "activation_proof_intent"
    await db_session.commit()

    expired = await watch_generation_deadline(
        session_factory=factory,
        generation_run_id=run.id,
        now=sealed_at + _ACTIVATION + timedelta(seconds=1),
    )

    await db_session.refresh(run)
    assert expired is True and run.status == "failed"
    assert run.error is not None and "stage=proof; " in run.error
    # The intent survives: the reconciler still finishes it forward.
    assert run.agent_state["restoration_adaptation_owner_status"] == "sealed_proof_retained"


async def test_restoration_reconciler_enforces_the_sealed_ceiling_after_prompt_exit(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    from yleum_api.services.restoration_reconciliation import _observe

    run, operation, factory = await _bound_adaptation(db_session, test_engine)
    _seal(run)
    sealed_at = datetime.now(UTC) - _ACTIVATION - timedelta(minutes=1)
    note_proof_sealed(run, sealed_at)
    operation.state = "applying"
    operation.phase = "activation_proof_intent"
    operation.next_reconcile_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    # The prompt-owned watchdog is already gone. Even when controller observation
    # fails, the durable restoration worker enforces the sealed hand-off ceiling.
    await _observe(
        factory,
        object(),  # type: ignore[arg-type]
        (operation.id, operation.project_id, operation.owner_id),
        moment=datetime.now(UTC),
        lease_seconds=1,
    )

    await db_session.refresh(run)
    assert run.status == "failed"
    assert run.error is not None and "stage=proof; " in run.error
    assert run.agent_state["restoration_adaptation_owner_status"] == "sealed_proof_retained"


async def test_a_rejected_proof_resumes_without_a_repair_deadline(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    run, _operation, factory = await _bound_adaptation(db_session, test_engine)
    started = run.started_at or run.created_at
    note_repair_stage_started(run, started + timedelta(seconds=1440))
    _seal(run)
    note_proof_sealed(run, started + timedelta(seconds=1700))
    _settle(run, "migration_required")
    note_proof_settled(run, started + timedelta(seconds=2300))
    await db_session.commit()
    expired = await watch_generation_deadline(
        session_factory=factory,
        generation_run_id=run.id,
        now=started + timedelta(days=1),
    )

    await db_session.refresh(run)
    assert expired is False
    assert (run.status, run.error, run.finished_at) == ("running", None, None)


async def test_the_watchdog_stops_looking_once_the_run_is_over(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    run, _operation, factory = await _bound_adaptation(db_session, test_engine)

    before = await generation_deadline_wait(session_factory=factory, generation_run_id=run.id)
    run.status = "completed"
    await db_session.commit()
    after = await generation_deadline_wait(session_factory=factory, generation_run_id=run.id)

    assert before == 15.0
    assert after is None


@pytest.mark.usefixtures("_exact_release_probe")
async def test_checks_and_repairs_continue_after_a_long_running_edit(
    db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    harness = await _new_harness(db_session, test_engine)
    run = await db_session.get(GenerationRun, harness.coordinator.generation_run_id)
    assert run is not None
    run.agent_state = {
        "restoration_adaptation": {
            "operation_id": str(uuid.uuid4()),
            "adaptation_run_id": str(run.id),
        }
    }
    run.started_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()
    repaired: list[str] = []

    async def repair(detail: str) -> None:
        repaired.append(detail)
        harness.files["src/app/page.tsx"] = (
            "export default function Page(){return <main>Каталог товаров</main>}"
        )

    # The harness has no preservation contract, so the pass after the repair ends the run.
    # The one-day-old start proves elapsed lifetime does not prevent that repair.
    await harness.coordinator.finalize_with_repair(prompt="Каталог", repair=repair)

    await db_session.refresh(run)
    assert len(repaired) == 1
    book = dict(run.agent_state["max_finalization"])["deadline"]  # type: ignore[arg-type]
    assert isinstance(book["repair_started_at_ms"], int)
@pytest.mark.usefixtures("_exact_release_probe")
async def test_the_real_adaptive_path_seals_the_proof_against_the_deadline(
    db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yleum_api.services import restorations

    harness = await _new_harness(db_session, test_engine)
    operation_id = await _adaptation_run(db_session, harness)
    harness.files.update(
        {
            "src/app/api/clients/route.ts": _CLIENTS_ROUTE,
            "src/app/api/visits/route.ts": _VISITS_ROUTE,
            "src/app/api/omnia/health/route.ts": _VISITS_ROUTE,
        }
    )
    run_id = harness.coordinator.generation_run_id
    owner_id = (await db_session.get(GenerationRun, run_id)).user_id  # type: ignore[union-attr]
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    # Past the editing limit, inside the hand-off ceiling the seal opens.
    far_future = datetime.now(UTC) + timedelta(seconds=1800)
    during_proof: list[tuple[bool, str | None, bool]] = []

    async def prove(
        candidate: ProofIdentity, artifact_digest: str, proof_attempt: int
    ) -> RestorationAdaptationProof:
        # The intent is durable and the orchestrator is proving: the deadline has long
        # passed, and the watchdog must leave the run alone.
        cut = await watch_generation_deadline(
            session_factory=factory, generation_run_id=run_id, now=far_future
        )
        async with factory() as session:
            seen = await session.get(GenerationRun, run_id)
            assert seen is not None
            state = dict(seen.agent_state["max_finalization"])  # type: ignore[arg-type]
            during_proof.append(
                (cut, state.get("current_phase"), "sealed_since_ms" in state["deadline"])
            )
        ready = proof_attempt == 2
        return RestorationAdaptationProof(
            state="proof_ready" if ready else "migration_required",
            reason_code=None if ready else "candidate_business_data_changed",
            source_workspace_id=candidate.workspace_id,
            candidate_workspace_id=candidate.workspace_id,
            operation_id=operation_id,
            project_id=harness.coordinator.project_id,
            owner_id=owner_id,
            generation_run_id=candidate.generation_run_id,
            candidate_fencing_epoch=candidate.fencing_epoch,
            proof_attempt=proof_attempt,
            source_workspace_revision=candidate.workspace_revision,
            candidate_workspace_revision=candidate.workspace_revision,
            candidate_proof_key=candidate.proof_key,
            candidate_artifact_digest=artifact_digest,
            source_database_digest="1" * 64,
            candidate_database_digest="1" * 64 if ready else "9" * 64,
            source_schema_digest="2" * 64,
            candidate_schema_digest="2" * 64,
            source_business_digest="3" * 64,
            candidate_business_digest="3" * 64 if ready else "8" * 64,
            source_technical_digest="4" * 64,
            candidate_technical_digest="4" * 64,
            probe_contract_digest="5" * 64,
            probe_rehearsal_digest="6" * 64,
            probe_rehearsal_database_digest="7" * 64,
            candidate_source_manifest_digest="8" * 64,
            proof_digest=f"{proof_attempt:064x}",
            capabilities={
                "portable_machine": True,
                "database_admin": "isolated_copy",
                "restoration_adaptation_database_copy_v1": True,
                **({"restoration_adaptation_proof_v1": True} if ready else {}),
            },
        )

    harness.coordinator.executor = replace(
        harness.coordinator.executor,
        prove_restoration_adaptation=prove,
        capabilities={
            "portable_machine": True,
            "database_admin": "isolated_copy",
            "restoration_adaptation_database_copy_v1": True,
        },
    )

    async def activate(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(restorations, "activate_restoration_adaptation", activate)

    first = await harness.coordinator.finalize(files=harness.files, prompt="Adapt")

    run = await db_session.get(GenerationRun, run_id, populate_existing=True)
    assert run is not None
    assert first.status.value == "needs_edit"
    assert during_proof == [(False, "promote", True)]
    state = dict(run.agent_state["max_finalization"])  # type: ignore[arg-type]
    # A rejected proof returns to unbounded repair work.
    assert "sealed_since_ms" not in state["deadline"]
    assert "sealed_ms" not in state["deadline"]
    assert generation_deadline(run).at is None
    # The checks left their timings, as on the ordinary path.
    assert {"prepare", "final_build", "runtime_probe"} <= set(state["phase_ms"])

    second = await harness.coordinator.finalize(files=harness.files, prompt="Adapt")

    assert second.status.value == "complete"
    assert during_proof[1] == (False, "promote", True)
    assert run.status == "running"


async def test_the_watchdog_returns_to_unbounded_repair_after_a_rejected_proof(
    db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yleum_api.services import max_finalization

    run, _operation, factory = await _bound_adaptation(db_session, test_engine)
    run.started_at = datetime.now(UTC) - timedelta(hours=2)
    _seal(run)
    note_proof_sealed(run, datetime.now(UTC))
    await db_session.commit()
    monkeypatch.setattr(max_finalization, "_SEALED_RECHECK_SECONDS", 0.05)

    watchdog = asyncio.create_task(
        run_generation_deadline_watchdog(session_factory=factory, generation_run_id=run.id)
    )
    await asyncio.sleep(0.3)
    await db_session.refresh(run)
    # The sealed controller hand-off is still inside its own ceiling.
    assert not watchdog.done() and run.status == "running"

    _settle(run, "migration_required")
    note_proof_settled(run)
    await db_session.commit()
    await asyncio.sleep(0.2)

    await db_session.refresh(run)
    assert not watchdog.done()
    assert (run.status, run.error, run.finished_at) == ("running", None, None)

    run.status = "completed"
    await db_session.commit()
    await asyncio.wait_for(watchdog, timeout=5)


async def test_the_watchdog_ends_with_the_run(
    db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yleum_api.services import max_finalization

    run, _operation, factory = await _bound_adaptation(db_session, test_engine)
    _seal(run)
    await db_session.commit()
    monkeypatch.setattr(max_finalization, "_SEALED_RECHECK_SECONDS", 0.05)
    watchdog = asyncio.create_task(
        run_generation_deadline_watchdog(session_factory=factory, generation_run_id=run.id)
    )
    await asyncio.sleep(0.15)

    run.status = "completed"
    await db_session.commit()

    await asyncio.wait_for(watchdog, timeout=5)
    await db_session.refresh(run)
    assert (run.status, run.error) == ("completed", None)

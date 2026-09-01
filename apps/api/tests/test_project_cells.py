from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import (
    ProjectCellCandidate,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from omnia_api.models.user import User
from omnia_api.services.project_cell_candidates import (
    _matching_candidate,
    cancel_candidate,
    prepare_candidate,
    promote_candidate,
)
from omnia_api.services.project_cells import (
    ProjectCellBusy,
    ProjectCellIdempotencyConflict,
    ProjectCellNotFound,
    ProjectCellOwnershipError,
    ProjectCellStateConflict,
    ProjectCellValidationError,
    _canonical_operation_envelope,
    claim_cell_operation,
    claim_cell_operation_committed,
    complete_cell_operation,
    fail_cell_operation,
    get_or_create_workspace,
    mark_cell_operation_indeterminate,
    recover_interrupted_cell_operations,
    reserve_cell_operation,
)

pytestmark = pytest.mark.asyncio

ALLOWED_KINDS = (
    "ensure",
    "wake",
    "pause",
    "stop",
    "destroy",
    "status",
    "restore",
    "reconcile",
)


async def test_candidate_promotion_is_atomic_compare_and_swap(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
    run: GenerationRun,
) -> None:
    first = await _prepare_release_candidate(db_session, workspace, run, "a" * 40)
    await promote_candidate(
        db_session, candidate_id=first.id, generation_run_id=run.id, fencing_epoch=7
    )
    second = await _prepare_release_candidate(db_session, workspace, run, "c" * 40)
    stale = await _prepare_release_candidate(db_session, workspace, run, "d" * 40)

    await promote_candidate(
        db_session, candidate_id=second.id, generation_run_id=run.id, fencing_epoch=7
    )
    with pytest.raises(ProjectCellStateConflict, match="accepted candidate changed"):
        await promote_candidate(
            db_session, candidate_id=stale.id, generation_run_id=run.id, fencing_epoch=7
        )

    accepted = list(
        await db_session.scalars(
            select(ProjectCellCandidate).where(ProjectCellCandidate.status == "accepted")
        )
    )
    assert [item.id for item in accepted] == [second.id]
    assert first.status == "rejected"


async def test_cancelled_or_stale_candidate_cannot_promote(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
    run: GenerationRun,
) -> None:
    cancelled = await _prepare_release_candidate(db_session, workspace, run, "e" * 40)
    await cancel_candidate(
        db_session,
        candidate_id=cancelled.id,
        generation_run_id=run.id,
        fencing_epoch=7,
    )
    with pytest.raises(ProjectCellStateConflict, match="not promotable"):
        await promote_candidate(
            db_session, candidate_id=cancelled.id, generation_run_id=run.id, fencing_epoch=7
        )

    stale = await _prepare_release_candidate(db_session, workspace, run, "f" * 40)
    workspace.fencing_epoch = 8
    await db_session.flush()
    with pytest.raises(ProjectCellStateConflict, match="stale"):
        await promote_candidate(
            db_session, candidate_id=stale.id, generation_run_id=run.id, fencing_epoch=7
        )


async def test_candidate_requires_complete_safe_immutable_evidence(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
    run: GenerationRun,
) -> None:
    run.status = "running"
    workspace.generation_run_id = run.id
    workspace.fencing_epoch = 1
    await db_session.flush()
    with pytest.raises(ProjectCellValidationError, match="database_backup_ref"):
        await prepare_candidate(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            fencing_epoch=1,
            source_revision="a" * 40,
            migration_digest="b" * 64,
            database_backup_ref="secret?token=value",
            build_ref=_content_addressed_ref("build", "build-ok"),
            verification_ref=_content_addressed_ref("verification", "verify-ok"),
        )

    with pytest.raises(ProjectCellValidationError, match="build_ref"):
        await prepare_candidate(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            fencing_epoch=1,
            source_revision="a" * 40,
            migration_digest="b" * 64,
            database_backup_ref=_content_addressed_ref(
                "database-backup", "backup-ok"
            ),
            build_ref="build/main",
            verification_ref=_content_addressed_ref("verification", "verify-ok"),
        )


async def test_cancelled_generation_run_cannot_promote_candidate(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
    run: GenerationRun,
) -> None:
    candidate = await _prepare_release_candidate(db_session, workspace, run, "9" * 40)
    run.status = "cancel_requested"
    await db_session.flush()

    with pytest.raises(ProjectCellStateConflict, match="generation run is not running"):
        await promote_candidate(
            db_session, candidate_id=candidate.id, generation_run_id=run.id, fencing_epoch=7
        )


async def test_prepare_candidate_replays_exact_request_and_allows_terminal_rerun(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
    run: GenerationRun,
) -> None:
    first = await _prepare_release_candidate(
        db_session,
        workspace,
        run,
        "1" * 40,
        evidence_seed="first",
    )
    replay = await _prepare_release_candidate(
        db_session,
        workspace,
        run,
        "1" * 40,
        evidence_seed="first",
    )
    assert replay.id == first.id

    await cancel_candidate(
        db_session,
        candidate_id=first.id,
        generation_run_id=run.id,
        fencing_epoch=7,
    )
    rerun_after_cancel = await _prepare_release_candidate(
        db_session,
        workspace,
        run,
        "1" * 40,
        evidence_seed="first",
    )
    assert rerun_after_cancel.id != first.id

    base = await _prepare_release_candidate(
        db_session,
        workspace,
        run,
        "2" * 40,
        evidence_seed="base",
    )
    await promote_candidate(
        db_session,
        candidate_id=base.id,
        generation_run_id=run.id,
        fencing_epoch=7,
    )
    replacement = await _prepare_release_candidate(
        db_session,
        workspace,
        run,
        "3" * 40,
        evidence_seed="replacement",
    )
    await promote_candidate(
        db_session,
        candidate_id=replacement.id,
        generation_run_id=run.id,
        fencing_epoch=7,
    )
    rerun_after_reject = await _prepare_release_candidate(
        db_session,
        workspace,
        run,
        "2" * 40,
        evidence_seed="base",
    )
    assert rerun_after_reject.id != base.id

    assert await _matching_candidate(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=base.generation_run_id,
        fencing_epoch=base.fencing_epoch,
        source_revision=base.source_revision,
        migration_digest=base.migration_digest,
        database_backup_ref=base.database_backup_ref,
        build_ref=base.build_ref,
        verification_ref=base.verification_ref,
        expected_accepted_candidate_id=base.expected_accepted_candidate_id,
    ) is None


async def test_cancel_candidate_requires_current_lease_and_active_run(
    db_session: AsyncSession,
    owner: User,
    project: Project,
    workspace: ProjectCellWorkspace,
    run: GenerationRun,
) -> None:
    candidate = await _prepare_release_candidate(db_session, workspace, run, "4" * 40)

    other_run = await _new_run(
        db_session,
        project=project,
        user=owner,
        label="other-run",
        status="completed",
    )
    with pytest.raises(ProjectCellStateConflict, match="another generation run"):
        await cancel_candidate(
            db_session,
            candidate_id=candidate.id,
            generation_run_id=other_run.id,
            fencing_epoch=7,
        )

    with pytest.raises(ProjectCellStateConflict, match="stale"):
        await cancel_candidate(
            db_session,
            candidate_id=candidate.id,
            generation_run_id=run.id,
            fencing_epoch=8,
        )

    run.status = "cancel_requested"
    await db_session.flush()
    await cancel_candidate(
        db_session,
        candidate_id=candidate.id,
        generation_run_id=run.id,
        fencing_epoch=7,
    )
    assert candidate.status == "cancelled"
    assert candidate.cancelled is True


async def test_promote_candidate_reloads_workspace_fence_under_lock(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    run_id, workspace_id, candidate_id = await _seed_candidate_fixture(
        factory,
        revision="5" * 40,
    )

    async with factory() as first, factory() as second:
        assert await first.get(ProjectCellWorkspace, workspace_id) is not None
        assert await first.get(ProjectCellCandidate, candidate_id) is not None
        assert await first.get(GenerationRun, run_id) is not None

        workspace = await second.get(ProjectCellWorkspace, workspace_id)
        assert workspace is not None
        workspace.fencing_epoch = 8
        await second.commit()

        with pytest.raises(ProjectCellStateConflict, match="stale"):
            await promote_candidate(
                first,
                candidate_id=candidate_id,
                generation_run_id=run_id,
                fencing_epoch=7,
            )
        await first.rollback()


async def test_promote_candidate_reloads_generation_run_status_under_lock(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    run_id, _workspace_id, candidate_id = await _seed_candidate_fixture(
        factory,
        revision="6" * 40,
    )

    async with factory() as first, factory() as second:
        assert await first.get(GenerationRun, run_id) is not None
        assert await first.get(ProjectCellCandidate, candidate_id) is not None

        run = await second.get(GenerationRun, run_id)
        assert run is not None
        run.status = "cancel_requested"
        await second.commit()

        with pytest.raises(ProjectCellStateConflict, match="generation run is not running"):
            await promote_candidate(
                first,
                candidate_id=candidate_id,
                generation_run_id=run_id,
                fencing_epoch=7,
            )
        await first.rollback()


async def _new_user(session: AsyncSession, label: str) -> User:
    user = User(email=f"cell-service-{label}-{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _new_project(session: AsyncSession, owner: User, label: str = "project") -> Project:
    project = Project(
        owner_id=owner.id,
        name=f"Project Cell {label}",
        slug=f"cell-service-{label}-{uuid.uuid4().hex}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    return project


async def _new_run(
    session: AsyncSession,
    project: Project,
    user: User,
    label: str = "run",
    *,
    status: str = "pending",
) -> GenerationRun:
    run = GenerationRun(
        project_id=project.id,
        user_id=user.id,
        idempotency_key=f"cell-service-{label}-{uuid.uuid4().hex}",
        prompt_hash="hash",
        status=status,
    )
    session.add(run)
    await session.flush()
    return run


async def _new_workspace(
    session: AsyncSession,
    project: Project,
    user: User,
    *,
    run: GenerationRun | None = None,
    state: str = "ready",
) -> ProjectCellWorkspace:
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=user.id,
        provider="docker_owner_canary",
        state=state,
        generation_run_id=run.id if run is not None else None,
    )
    session.add(workspace)
    await session.flush()
    return workspace


async def _prepare_release_candidate(
    session: AsyncSession,
    workspace: ProjectCellWorkspace,
    run: GenerationRun,
    revision: str,
    *,
    evidence_seed: str | None = None,
) -> ProjectCellCandidate:
    run.status = "running"
    workspace.generation_run_id = run.id
    workspace.fencing_epoch = 7
    await session.flush()
    seed = evidence_seed or revision
    return await prepare_candidate(
        session,
        workspace_id=workspace.id,
        generation_run_id=run.id,
        fencing_epoch=7,
        source_revision=revision,
        migration_digest="b" * 64,
        database_backup_ref=_content_addressed_ref("database-backup", f"{seed}:backup"),
        build_ref=_content_addressed_ref("build", f"{seed}:build"),
        verification_ref=_content_addressed_ref("verification", f"{seed}:verify"),
    )


def _content_addressed_ref(kind: str, seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"{kind}/sha256/{digest}"


async def _seed_candidate_fixture(
    factory: async_sessionmaker[AsyncSession],
    *,
    revision: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with factory() as session:
        owner = await _new_user(session, f"candidate-owner-{revision[:6]}")
        project = await _new_project(session, owner, f"candidate-project-{revision[:6]}")
        run = await _new_run(
            session,
            project,
            owner,
            f"candidate-run-{revision[:6]}",
            status="running",
        )
        workspace = await _new_workspace(session, project, owner, run=run)
        workspace.fencing_epoch = 7
        candidate = await _prepare_release_candidate(session, workspace, run, revision)
        await session.commit()
        return run.id, workspace.id, candidate.id


@pytest_asyncio.fixture
async def owner(db_session: AsyncSession) -> User:
    return await _new_user(db_session, "owner")


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, owner: User) -> Project:
    return await _new_project(db_session, owner)


@pytest_asyncio.fixture
async def run(
    db_session: AsyncSession,
    project: Project,
    owner: User,
) -> GenerationRun:
    return await _new_run(db_session, project, owner)


@pytest_asyncio.fixture
async def workspace(
    db_session: AsyncSession,
    project: Project,
    owner: User,
    run: GenerationRun,
) -> ProjectCellWorkspace:
    return await _new_workspace(db_session, project, owner, run=run)


async def _wait_for_advisory_lock(
    observer: AsyncSession,
    backend_pid: int,
) -> None:
    for _ in range(200):
        wait_event = await observer.scalar(
            text(
                "SELECT wait_event FROM pg_stat_activity "
                "WHERE pid = :backend_pid AND wait_event_type = 'Lock'"
            ),
            {"backend_pid": backend_pid},
        )
        if wait_event == "advisory":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("second transaction never waited on the advisory lock")


async def _wait_for_row_lock(
    observer: AsyncSession,
    backend_pid: int,
) -> str:
    for _ in range(200):
        wait_event = await observer.scalar(
            text(
                "SELECT wait_event FROM pg_stat_activity "
                "WHERE pid = :backend_pid AND wait_event_type = 'Lock'"
            ),
            {"backend_pid": backend_pid},
        )
        if wait_event is not None:
            return str(wait_event)
        await asyncio.sleep(0.01)
    raise AssertionError("second transaction never waited on the operation row lock")


def _payload_with_serialized_size(
    size: int,
    *,
    multibyte: bool,
) -> dict[str, object]:
    framing = len(b'{"text":""}')
    prefix = "я" if multibyte else ""
    filler_size = size - framing - len(prefix.encode("utf-8"))
    assert filler_size >= 0
    payload: dict[str, object] = {"text": prefix + ("x" * filler_size)}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert len(canonical.encode("utf-8")) == size
    return payload


def _enveloped_payload_with_serialized_size(
    size: int,
    *,
    workspace_id: uuid.UUID,
    generation_run_id: uuid.UUID | None,
    kind: str,
    multibyte: bool,
) -> dict[str, object]:
    empty_envelope, _ = _canonical_operation_envelope(
        workspace_id,
        generation_run_id,
        kind,
        {"text": ""},
    )
    empty_size = len(
        json.dumps(
            empty_envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    prefix = "я" if multibyte else ""
    filler_size = size - empty_size - len(prefix.encode("utf-8"))
    assert filler_size >= 0
    payload: dict[str, object] = {"text": prefix + ("x" * filler_size)}
    envelope: dict[str, object] = {
        "workspace_id": str(workspace_id),
        "generation_run_id": (
            str(generation_run_id) if generation_run_id is not None else None
        ),
        "kind": kind,
        "request": payload,
    }
    assert (
        len(
            json.dumps(
                envelope,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        == size
    )
    return payload


async def test_same_operation_key_replays_same_canonical_request(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    request = {"profile_version": "v1", "details": {"label": "Тест", "count": 2}}
    reordered = {"details": {"count": 2, "label": "Тест"}, "profile_version": "v1"}

    first, replayed = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:one",
        request=request,
    )
    second, replayed_again = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:one",
        request=reordered,
    )

    envelope, digest = _canonical_operation_envelope(
        workspace.id,
        None,
        "ensure",
        request,
    )
    assert replayed is False
    assert replayed_again is True
    assert second.id == first.id
    assert first.request_digest == digest
    assert first.request_payload == envelope
    assert first.request_payload is not request
    assert first.fencing_epoch is None


async def test_same_key_with_different_request_precedes_busy_check(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    first, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:conflict",
        request={"profile_version": "v1"},
    )

    with pytest.raises(ProjectCellIdempotencyConflict):
        await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:conflict",
            request={"profile_version": "v2"},
        )

    assert first.status == "pending"


async def test_same_key_with_different_kind_is_idempotency_conflict(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:kind-conflict",
        request={"profile_version": "v1"},
    )

    with pytest.raises(ProjectCellIdempotencyConflict):
        await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="reconcile",
            idempotency_key="ensure:kind-conflict",
            request={"indeterminate_operation_id": str(uuid.uuid4())},
        )


@pytest.mark.parametrize(
    "operation_request",
    [
        {},
        {"indeterminate_operation_id": "not-a-uuid"},
        {"indeterminate_operation_id": str(uuid.uuid4()), "extra": True},
    ],
)
async def test_reconcile_reservation_requires_exact_target(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
    operation_request: dict[str, object],
) -> None:
    with pytest.raises(ProjectCellValidationError):
        await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="reconcile",
            idempotency_key=f"reconcile:invalid:{uuid.uuid4().hex}",
            request=operation_request,
        )


async def test_same_key_with_different_generation_run_is_idempotency_conflict(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
    project: Project,
    owner: User,
    run: GenerationRun,
) -> None:
    later_run = await _new_run(
        db_session,
        project,
        owner,
        "same-key-later",
        status="completed",
    )
    await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=run.id,
        kind="ensure",
        idempotency_key="ensure:run-conflict",
        request={"profile_version": "v1"},
    )

    with pytest.raises(ProjectCellIdempotencyConflict):
        await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=later_run.id,
            kind="ensure",
            idempotency_key="ensure:run-conflict",
            request={"profile_version": "v1"},
        )


async def test_different_key_is_busy_while_operation_active(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:active-one",
        request={},
    )

    with pytest.raises(ProjectCellBusy):
        await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="status",
            idempotency_key="status:active-two",
            request={},
        )


async def test_completed_operation_replays_and_cannot_be_claimed(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    operation, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:completed",
        request={"profile_version": "v1"},
    )
    await claim_cell_operation(db_session, operation.id)
    assert await complete_cell_operation(db_session, operation.id, {"state": "ready"}) is None

    replay, replayed = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:completed",
        request={"profile_version": "v1"},
    )

    assert replayed is True
    assert replay.id == operation.id
    assert replay.status == "completed"
    assert replay.result_payload == {"state": "ready"}
    with pytest.raises(ProjectCellStateConflict):
        await claim_cell_operation(db_session, replay.id)


async def test_new_key_is_allowed_after_failed_operation(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    failed, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:failed",
        request={},
    )
    await claim_cell_operation(db_session, failed.id)
    await fail_cell_operation(db_session, failed.id, "dummy provider failure")

    replacement, replayed = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="wake",
        idempotency_key="wake:replacement",
        request={},
    )

    assert replayed is False
    assert replacement.id != failed.id
    assert replacement.status == "pending"


async def test_failed_operation_stores_only_bounded_error_digest(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    unicode_error = "Ошибка провайдера: dummy-value"
    errors = (
        "authorization=dummy-credential",
        "RAW_ENVIRONMENT\nDUMMY_NAME=dummy-value",
        "COMMAND_STREAM\ndummy-command --dummy-argument",
        "x" * 200_000,
        unicode_error,
        unicode_error,
    )
    stored_errors: list[str] = []

    for position, raw_error in enumerate(errors):
        operation, _ = await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key=f"ensure:error-digest-{position}",
            request={},
        )
        await claim_cell_operation(db_session, operation.id)
        await fail_cell_operation(db_session, operation.id, raw_error)
        await db_session.refresh(operation)

        expected = f"provider_error:{hashlib.sha256(raw_error.encode('utf-8')).hexdigest()}"
        assert operation.error == expected
        assert len(operation.error) == 79
        assert raw_error not in operation.error
        assert "dummy-credential" not in operation.error
        assert "RAW_ENVIRONMENT" not in operation.error
        assert "COMMAND_STREAM" not in operation.error
        assert "\n" not in operation.error
        stored_errors.append(operation.error)

    assert stored_errors[-1] == stored_errors[-2]


async def test_new_key_is_allowed_after_cancelled_operation(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    cancelled, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:cancelled",
        request={},
    )
    cancelled.status = "cancelled"
    await db_session.flush()

    replacement, replayed = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="status",
        idempotency_key="status:after-cancel",
        request={},
    )

    assert replayed is False
    assert replacement.id != cancelled.id


async def test_key_boundaries_and_all_operation_kinds_are_accepted(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    keys = ["k" * 8, "k" * 128]
    for position, key in enumerate(keys):
        operation, replayed = await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="status",
            idempotency_key=key,
            request={"position": position},
        )
        assert replayed is False
        await claim_cell_operation(db_session, operation.id)
        await complete_cell_operation(db_session, operation.id, {})

    for position, kind in enumerate(ALLOWED_KINDS):
        request = (
            {"indeterminate_operation_id": str(uuid.uuid4())}
            if kind == "reconcile"
            else {"position": position}
        )
        operation, replayed = await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind=kind,
            idempotency_key=f"allowed:{kind}",
            request=request,
        )
        assert replayed is False
        await claim_cell_operation(db_session, operation.id)
        await complete_cell_operation(db_session, operation.id, {})


async def test_invalid_keys_and_kind_raise_domain_validation(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    for key in ("k" * 7, "k" * 129):
        with pytest.raises(ProjectCellValidationError):
            await reserve_cell_operation(
                db_session,
                workspace_id=workspace.id,
                generation_run_id=None,
                kind="ensure",
                idempotency_key=key,
                request={},
            )

    with pytest.raises(ProjectCellValidationError):
        await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="execute",
            idempotency_key="execute:invalid",
            request={},
        )


async def test_request_payload_must_be_json_and_fit_utf8_limit(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    for request in ({"value": object()}, {"text": "я" * 40_000}):
        with pytest.raises(ProjectCellValidationError):
            await reserve_cell_operation(
                db_session,
                workspace_id=workspace.id,
                generation_run_id=None,
                kind="ensure",
                idempotency_key="ensure:bad-payload",
                request=request,
            )


async def test_request_and_result_reject_non_json_native_tuples(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    with pytest.raises(ProjectCellValidationError):
        await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:tuple-request",
            request={"nested": ["safe", ("not", "json")]},
        )

    operation, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:tuple-result",
        request={},
    )
    await claim_cell_operation(db_session, operation.id)
    with pytest.raises(ProjectCellValidationError):
        await complete_cell_operation(
            db_session,
            operation.id,
            {"nested": ["safe", ("not", "json")]},
        )


async def test_payload_serialized_utf8_boundaries_are_exact(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    request_at_limit = _enveloped_payload_with_serialized_size(
        65_536,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        multibyte=True,
    )
    request_over_limit = _enveloped_payload_with_serialized_size(
        65_537,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        multibyte=False,
    )
    accepted, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:request-65536",
        request=request_at_limit,
    )
    assert len(
        json.dumps(
            accepted.request_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ) == 65_536
    await claim_cell_operation(db_session, accepted.id)
    await complete_cell_operation(db_session, accepted.id, {})

    with pytest.raises(ProjectCellValidationError):
        await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:request-65537",
            request=request_over_limit,
        )

    result_at_limit = _payload_with_serialized_size(65_536, multibyte=False)
    result_over_limit = _payload_with_serialized_size(65_537, multibyte=True)
    result_operation, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:result-boundary",
        request={},
    )
    await claim_cell_operation(db_session, result_operation.id)
    await complete_cell_operation(db_session, result_operation.id, result_at_limit)
    assert result_operation.result_payload is not None
    assert len(
        json.dumps(
            result_operation.result_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ) == 65_536

    oversized_result, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:result-65537",
        request={},
    )
    await claim_cell_operation(db_session, oversized_result.id)
    with pytest.raises(ProjectCellValidationError):
        await complete_cell_operation(db_session, oversized_result.id, result_over_limit)


async def test_unsafe_request_keys_are_rejected_recursively(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    unsafe_requests = (
        {"password": "dummy-password"},
        {"nested": [{"apiKey": "dummy-api-key"}]},
        {"rawEnvironment": {"SAFE_NAME": "dummy-value"}},
        {"commandStream": ["dummy-command"]},
    )
    for position, request in enumerate(unsafe_requests):
        with pytest.raises(ProjectCellValidationError):
            await reserve_cell_operation(
                db_session,
                workspace_id=workspace.id,
                generation_run_id=None,
                kind="ensure",
                idempotency_key=f"unsafe:{position}",
                request=request,
            )


async def test_compact_and_mixed_case_unsafe_keys_fail_closed(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    unsafe_keys = (
        "database_url",
        "DatabaseURL",
        "connection_string",
        "ConnectionString",
        "token",
        "apikey",
        "PrivateKey",
        "AccessToken",
        "RefreshToken",
        "RAWENVIRONMENT",
        "COMMANDSTREAM",
        "commandOutput",
    )
    for position, unsafe_key in enumerate(unsafe_keys):
        request = {"safe": [{"nested": {unsafe_key: f"dummy-value-{position}"}}]}
        with pytest.raises(ProjectCellValidationError):
            await reserve_cell_operation(
                db_session,
                workspace_id=workspace.id,
                generation_run_id=None,
                kind="ensure",
                idempotency_key=f"unsafe:compact-{position}",
                request=request,
            )


async def test_compact_and_mixed_case_result_keys_are_redacted(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    unsafe_keys = (
        "database_url",
        "ConnectionString",
        "token",
        "apikey",
        "PrivateKey",
        "AccessToken",
        "RefreshToken",
        "RAWENVIRONMENT",
        "COMMANDSTREAM",
        "commandOutput",
    )
    raw_values = {key: f"dummy-result-{position}" for position, key in enumerate(unsafe_keys)}
    operation, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:compact-redaction",
        request={},
    )
    await claim_cell_operation(db_session, operation.id)
    await complete_cell_operation(db_session, operation.id, {"nested": [raw_values]})
    await db_session.refresh(operation)

    assert operation.result_payload == {
        "nested": [{key: "[REDACTED]" for key in unsafe_keys}]
    }
    stored = json.dumps(operation.result_payload, ensure_ascii=False)
    assert "dummy-result-" not in stored


async def test_legacy_environment_and_stream_request_aliases_are_rejected(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    unsafe_keys = (
        "env",
        "env_var",
        "ENV_VARS",
        "environment",
        "Process_ENV",
        "raw_env",
        "RAWENVIRONMENT",
        "stdout",
        "Std_Err",
        "command_output",
        "CommandStream",
    )
    for position, unsafe_key in enumerate(unsafe_keys):
        request = {
            "safe": {
                "items": [
                    {"public_label": "visible"},
                    {unsafe_key: f"dummy-unsafe-{position}"},
                ]
            }
        }
        with pytest.raises(ProjectCellValidationError):
            await reserve_cell_operation(
                db_session,
                workspace_id=workspace.id,
                generation_run_id=None,
                kind="ensure",
                idempotency_key=f"unsafe:legacy-{position}",
                request=request,
            )


async def test_legacy_environment_and_stream_result_aliases_are_redacted(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    unsafe_keys = (
        "env",
        "env_var",
        "ENV_VARS",
        "environment",
        "Process_ENV",
        "raw_env",
        "RAWENVIRONMENT",
        "stdout",
        "Std_Err",
        "command_output",
        "CommandStream",
    )
    raw_values = {key: f"dummy-unsafe-{position}" for position, key in enumerate(unsafe_keys)}
    operation, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:legacy-alias-redaction",
        request={},
    )
    await claim_cell_operation(db_session, operation.id)
    await complete_cell_operation(
        db_session,
        operation.id,
        {
            "safe": {
                "public_label": "visible",
                "output_summary": "also-visible",
                "items": [raw_values],
            }
        },
    )
    await db_session.refresh(operation)

    assert operation.result_payload == {
        "safe": {
            "public_label": "visible",
            "output_summary": "also-visible",
            "items": [{key: "[REDACTED]" for key in unsafe_keys}],
        }
    }
    stored = json.dumps(operation.result_payload, ensure_ascii=False)
    assert "dummy-unsafe-" not in stored


async def test_result_payload_is_recursively_redacted_before_storage(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    operation, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:redaction",
        request={},
    )
    await claim_cell_operation(db_session, operation.id)
    result = {
        "state": "ready",
        "nested": {
            "clientSecret": "dummy-secret",
            "items": [{"access_token": "dummy-token"}, {"safe": "visible"}],
        },
        "raw_environment": {"SAFE_NAME": "dummy-env"},
        "command_stream": ["dummy-command"],
    }

    await complete_cell_operation(db_session, operation.id, result)
    await db_session.refresh(operation)

    assert operation.result_payload == {
        "state": "ready",
        "nested": {
            "clientSecret": "[REDACTED]",
            "items": [{"access_token": "[REDACTED]"}, {"safe": "visible"}],
        },
        "raw_environment": "[REDACTED]",
        "command_stream": "[REDACTED]",
    }
    stored = json.dumps(operation.result_payload, ensure_ascii=False)
    assert "dummy-secret" not in stored
    assert "dummy-token" not in stored
    assert "dummy-env" not in stored
    assert "dummy-command" not in stored


async def test_result_payload_must_be_json_and_fit_stored_limit(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    for position, result in enumerate(({"value": object()}, {"output": "x" * 65_536})):
        operation, _ = await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key=f"ensure:bad-result-{position}",
            request={},
        )
        await claim_cell_operation(db_session, operation.id)
        with pytest.raises(ProjectCellValidationError):
            await complete_cell_operation(db_session, operation.id, result)
        await fail_cell_operation(db_session, operation.id, "dummy invalid result")


async def test_workspace_create_replays_without_retargeting_owner_or_run(
    db_session: AsyncSession,
    owner: User,
    project: Project,
    run: GenerationRun,
) -> None:
    first, created = await get_or_create_workspace(
        db_session,
        project=project,
        user=owner,
        run=run,
    )
    run.status = "completed"
    await db_session.flush()
    later_run = await _new_run(db_session, project, owner, "later")

    replay, created_again = await get_or_create_workspace(
        db_session,
        project=project,
        user=owner,
        run=later_run,
    )

    assert created is True
    assert created_again is False
    assert replay.id == first.id
    assert replay.owner_id == owner.id
    assert replay.generation_run_id == run.id
    assert replay.provider == "docker_owner_canary"
    assert replay.state == "provisioning"


async def test_workspace_validates_authenticated_owner_and_run_ownership(
    db_session: AsyncSession,
    owner: User,
    project: Project,
    run: GenerationRun,
) -> None:
    outsider = await _new_user(db_session, "outsider")
    with pytest.raises(ProjectCellOwnershipError):
        await get_or_create_workspace(
            db_session,
            project=project,
            user=outsider,
            run=run,
        )

    other_project = await _new_project(db_session, outsider, "outsider")
    other_run = await _new_run(db_session, other_project, outsider, "outsider")
    with pytest.raises(ProjectCellOwnershipError):
        await get_or_create_workspace(
            db_session,
            project=project,
            user=owner,
            run=other_run,
        )


async def test_existing_workspace_with_wrong_owner_is_not_silently_retargeted(
    db_session: AsyncSession,
    owner: User,
    project: Project,
    run: GenerationRun,
) -> None:
    other = await _new_user(db_session, "other-cell-owner")
    existing = await _new_workspace(db_session, project, other, run=run)

    with pytest.raises(ProjectCellOwnershipError):
        await get_or_create_workspace(
            db_session,
            project=project,
            user=owner,
            run=run,
        )

    await db_session.refresh(existing)
    assert existing.owner_id == other.id


async def test_reservation_validates_workspace_and_generation_run_ownership(
    db_session: AsyncSession,
    owner: User,
    project: Project,
    workspace: ProjectCellWorkspace,
) -> None:
    with pytest.raises(ProjectCellNotFound):
        await reserve_cell_operation(
            db_session,
            workspace_id=uuid.uuid4(),
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:missing-workspace",
            request={},
        )

    outsider = await _new_user(db_session, "run-outsider")
    other_project = await _new_project(db_session, outsider, "run-outsider")
    other_run = await _new_run(db_session, other_project, outsider, "run-outsider")
    with pytest.raises(ProjectCellOwnershipError):
        await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=other_run.id,
            kind="ensure",
            idempotency_key="ensure:wrong-run",
            request={},
        )

    missing_run_id = uuid.uuid4()
    with pytest.raises(ProjectCellNotFound):
        await reserve_cell_operation(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=missing_run_id,
            kind="ensure",
            idempotency_key="ensure:missing-run",
            request={},
        )


async def test_claim_complete_and_fail_enforce_row_locked_transitions(
    db_session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> None:
    completed, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="ensure",
        idempotency_key="ensure:transition-complete",
        request={},
    )
    with pytest.raises(ProjectCellStateConflict):
        await complete_cell_operation(db_session, completed.id, {})
    with pytest.raises(ProjectCellStateConflict):
        await fail_cell_operation(db_session, completed.id, "dummy premature failure")
    with pytest.raises(ProjectCellStateConflict):
        await mark_cell_operation_indeterminate(db_session, completed.id, "dummy premature unknown")

    claimed = await claim_cell_operation(db_session, completed.id)
    assert claimed.status == "running"
    assert claimed.started_at is not None
    with pytest.raises(ProjectCellStateConflict):
        await claim_cell_operation(db_session, completed.id)
    await complete_cell_operation(db_session, completed.id, {"state": "ready"})
    assert completed.status == "completed"
    assert completed.finished_at is not None

    failed, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="wake",
        idempotency_key="wake:transition-fail",
        request={},
    )
    await claim_cell_operation(db_session, failed.id)
    await fail_cell_operation(db_session, failed.id, "dummy provider failure")
    assert failed.status == "failed"
    assert failed.error == (
        "provider_error:"
        + hashlib.sha256(b"dummy provider failure").hexdigest()
    )
    assert failed.finished_at is not None

    indeterminate, _ = await reserve_cell_operation(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=None,
        kind="restore",
        idempotency_key="restore:transition-indeterminate",
        request={"checkpoint_ref": "accepted-1"},
    )
    await claim_cell_operation(db_session, indeterminate.id)
    await mark_cell_operation_indeterminate(
        db_session,
        indeterminate.id,
        "dummy provider unknown",
    )
    assert indeterminate.status == "indeterminate"
    assert indeterminate.error == (
        "provider_error:" + hashlib.sha256(b"dummy provider unknown").hexdigest()
    )
    assert indeterminate.finished_at is not None


async def test_transition_functions_raise_not_found_for_missing_ids(
    db_session: AsyncSession,
) -> None:
    missing = uuid.uuid4()
    with pytest.raises(ProjectCellNotFound):
        await claim_cell_operation(db_session, missing)
    with pytest.raises(ProjectCellNotFound):
        await complete_cell_operation(db_session, missing, {})
    with pytest.raises(ProjectCellNotFound):
        await fail_cell_operation(db_session, missing, "dummy missing operation")
    with pytest.raises(ProjectCellNotFound):
        await mark_cell_operation_indeterminate(db_session, missing, "dummy missing operation")


async def test_claim_cell_operation_committed_persists_workspace_and_operation_fence(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_user(session, "claimed")
        project = await _new_project(session, owner, "claimed")
        workspace = await _new_workspace(session, project, owner)
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:claimed",
            request={"profile_version": "docker-owner-cell-resources-v1"},
        )
        await session.commit()

    claimed = await claim_cell_operation_committed(factory, operation.id)

    assert claimed.workspace_id == workspace.id
    assert claimed.project_id == project.id
    assert claimed.owner_id == owner.id
    assert claimed.request == {"profile_version": "docker-owner-cell-resources-v1"}
    assert claimed.fencing_epoch == 1
    async with factory() as verify:
        stored_workspace = await verify.get(ProjectCellWorkspace, workspace.id)
        stored_operation = await verify.get(ProjectCellOperation, operation.id)
        assert stored_workspace is not None
        assert stored_operation is not None
        assert stored_workspace.fencing_epoch == 1
        assert stored_workspace.version == 2
        assert stored_operation.fencing_epoch == 1
        assert stored_operation.status == "running"


async def test_claim_cell_operation_committed_rejects_tampered_envelope(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_user(session, "tampered")
        project = await _new_project(session, owner, "tampered")
        workspace = await _new_workspace(session, project, owner)
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:tampered-envelope",
            request={"profile_version": "v1"},
        )
        operation.request_payload = {
            "workspace_id": str(workspace.id),
            "generation_run_id": None,
            "kind": "ensure",
            "request": {"profile_version": "v2"},
        }
        await session.commit()

    with pytest.raises(ProjectCellValidationError):
        await claim_cell_operation_committed(factory, operation.id)


async def test_committed_claims_monotonically_increase_workspace_fence(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_user(session, "fence")
        project = await _new_project(session, owner, "fence")
        workspace = await _new_workspace(session, project, owner)
        first, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:first",
            request={"profile_version": "v1"},
        )
        await session.commit()

    claimed_first = await claim_cell_operation_committed(factory, first.id)
    async with factory() as session:
        await fail_cell_operation(session, first.id, "first ended")
        await session.commit()
        second, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="reconcile",
            idempotency_key="reconcile:second",
            request={"indeterminate_operation_id": str(first.id)},
        )
        await session.commit()

    claimed_second = await claim_cell_operation_committed(factory, second.id)
    assert claimed_second.fencing_epoch > claimed_first.fencing_epoch


async def test_recovery_returns_count_and_only_requeues_running_operations(
    db_session: AsyncSession,
    owner: User,
) -> None:
    running: list[ProjectCellOperation] = []
    for position in range(2):
        project = await _new_project(db_session, owner, f"recover-{position}")
        workspace = await _new_workspace(db_session, project, owner)
        operation = ProjectCellOperation(
            workspace_id=workspace.id,
            kind="ensure",
            status="running",
            idempotency_key=f"recover:running-{position}",
            request_digest=str(position) * 64,
            started_at=datetime.now(UTC),
        )
        db_session.add(operation)
        running.append(operation)

    terminal_project = await _new_project(db_session, owner, "recover-terminal")
    terminal_workspace = await _new_workspace(db_session, terminal_project, owner)
    terminal = ProjectCellOperation(
        workspace_id=terminal_workspace.id,
        kind="ensure",
        status="completed",
        idempotency_key="recover:completed",
        request_digest="c" * 64,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        result_payload={"state": "ready"},
    )
    db_session.add(terminal)
    await db_session.flush()
    original_terminal_started_at = terminal.started_at

    assert await recover_interrupted_cell_operations(db_session) == 2

    for operation in running:
        assert operation.status == "indeterminate"
        assert operation.started_at is not None
        assert operation.idempotency_key.startswith("recover:running-")
        assert operation.finished_at is not None
        assert operation.error is not None
    assert terminal.status == "completed"
    assert terminal.started_at == original_terminal_started_at
    assert terminal.finished_at is not None
    assert terminal.result_payload == {"state": "ready"}


async def test_workspace_creation_serializes_with_real_advisory_lock(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as setup:
        owner = await _new_user(setup, "workspace-lock")
        project = await _new_project(setup, owner, "workspace-lock")
        run = await _new_run(setup, project, owner, "workspace-lock")
        await setup.commit()

    async with factory() as first_session, factory() as second_session, factory() as observer:
        second_pid = int(await second_session.scalar(text("SELECT pg_backend_pid()")))
        first_result = await get_or_create_workspace(
            first_session,
            project=project,
            user=owner,
            run=run,
        )
        second_task = asyncio.create_task(
            get_or_create_workspace(
                second_session,
                project=project,
                user=owner,
                run=run,
            )
        )
        await _wait_for_advisory_lock(observer, second_pid)
        await first_session.commit()
        second_result = await second_task
        await second_session.commit()

    first_workspace, first_created = first_result
    second_workspace, second_created = second_result
    assert first_created is True
    assert second_created is False
    assert second_workspace.id == first_workspace.id


async def test_concurrent_same_key_same_request_replays_after_advisory_wait(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as setup:
        owner = await _new_user(setup, "same-key-replay")
        project = await _new_project(setup, owner, "same-key-replay")
        workspace = await _new_workspace(setup, project, owner)
        await setup.commit()

    async with factory() as first_session, factory() as second_session, factory() as observer:
        second_pid = int(await second_session.scalar(text("SELECT pg_backend_pid()")))
        first, first_replayed = await reserve_cell_operation(
            first_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:concurrent-replay",
            request={"profile_version": "v1"},
        )
        second_task = asyncio.create_task(
            reserve_cell_operation(
                second_session,
                workspace_id=workspace.id,
                generation_run_id=None,
                kind="ensure",
                idempotency_key="ensure:concurrent-replay",
                request={"profile_version": "v1"},
            )
        )
        await _wait_for_advisory_lock(observer, second_pid)
        await first_session.commit()
        replay, replayed = await asyncio.wait_for(second_task, timeout=5)
        await second_session.commit()

    assert first_replayed is False
    assert replayed is True
    assert replay.id == first.id


async def test_concurrent_same_key_different_request_keeps_conflict_precedence(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as setup:
        owner = await _new_user(setup, "same-key-conflict")
        project = await _new_project(setup, owner, "same-key-conflict")
        workspace = await _new_workspace(setup, project, owner)
        await setup.commit()

    async with factory() as first_session, factory() as second_session, factory() as observer:
        second_pid = int(await second_session.scalar(text("SELECT pg_backend_pid()")))
        await reserve_cell_operation(
            first_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:concurrent-conflict",
            request={"profile_version": "v1"},
        )
        second_task = asyncio.create_task(
            reserve_cell_operation(
                second_session,
                workspace_id=workspace.id,
                generation_run_id=None,
                kind="ensure",
                idempotency_key="ensure:concurrent-conflict",
                request={"profile_version": "v2"},
            )
        )
        await _wait_for_advisory_lock(observer, second_pid)
        await first_session.commit()
        with pytest.raises(ProjectCellIdempotencyConflict):
            await asyncio.wait_for(second_task, timeout=5)
        await second_session.rollback()


async def test_concurrent_claim_uses_row_lock_and_only_one_claims(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as setup:
        owner = await _new_user(setup, "claim-lock")
        project = await _new_project(setup, owner, "claim-lock")
        workspace = await _new_workspace(setup, project, owner)
        operation, _ = await reserve_cell_operation(
            setup,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:claim-lock",
            request={},
        )
        await setup.commit()

    async with factory() as first_session, factory() as second_session, factory() as observer:
        second_pid = int(await second_session.scalar(text("SELECT pg_backend_pid()")))
        first_claim = await claim_cell_operation(first_session, operation.id)
        second_task = asyncio.create_task(claim_cell_operation(second_session, operation.id))
        wait_event = await _wait_for_row_lock(observer, second_pid)
        assert wait_event in {"transactionid", "tuple"}
        await first_session.commit()
        with pytest.raises(ProjectCellStateConflict):
            await asyncio.wait_for(second_task, timeout=5)
        await second_session.rollback()

    assert first_claim.status == "running"
    async with factory() as verification_session:
        stored = await verification_session.get(ProjectCellOperation, operation.id)
        assert stored is not None
        assert stored.status == "running"


async def test_competing_complete_and_fail_use_row_lock_terminal_winner(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as setup:
        owner = await _new_user(setup, "terminal-lock")
        project = await _new_project(setup, owner, "terminal-lock")
        workspace = await _new_workspace(setup, project, owner)
        operation, _ = await reserve_cell_operation(
            setup,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:terminal-lock",
            request={},
        )
        await claim_cell_operation(setup, operation.id)
        await setup.commit()

    async with factory() as first_session, factory() as second_session, factory() as observer:
        second_pid = int(await second_session.scalar(text("SELECT pg_backend_pid()")))
        await complete_cell_operation(first_session, operation.id, {"state": "ready"})
        second_task = asyncio.create_task(
            fail_cell_operation(second_session, operation.id, "dummy competing failure")
        )
        wait_event = await _wait_for_row_lock(observer, second_pid)
        assert wait_event in {"transactionid", "tuple"}
        await first_session.commit()
        with pytest.raises(ProjectCellStateConflict):
            await asyncio.wait_for(second_task, timeout=5)
        await second_session.rollback()

    async with factory() as verification_session:
        stored = await verification_session.get(ProjectCellOperation, operation.id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.result_payload == {"state": "ready"}
        assert stored.error is None


async def test_operation_reservation_serializes_with_real_advisory_lock(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as setup:
        owner = await _new_user(setup, "operation-lock")
        project = await _new_project(setup, owner, "operation-lock")
        workspace = await _new_workspace(setup, project, owner)
        await setup.commit()

    async with factory() as first_session, factory() as second_session, factory() as observer:
        second_pid = int(await second_session.scalar(text("SELECT pg_backend_pid()")))
        first, replayed = await reserve_cell_operation(
            first_session,
            workspace_id=workspace.id,
            generation_run_id=None,
            kind="ensure",
            idempotency_key="ensure:lock-first",
            request={},
        )
        second_task = asyncio.create_task(
            reserve_cell_operation(
                second_session,
                workspace_id=workspace.id,
                generation_run_id=None,
                kind="status",
                idempotency_key="status:lock-second",
                request={},
            )
        )
        await _wait_for_advisory_lock(observer, second_pid)
        await first_session.commit()
        with pytest.raises(ProjectCellBusy):
            await second_task
        await second_session.rollback()

    assert replayed is False
    assert first.status == "pending"
    async with factory() as verification_session:
        operations = list(
            (
                await verification_session.execute(
                    select(ProjectCellOperation).where(
                        ProjectCellOperation.workspace_id == workspace.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert [operation.id for operation in operations] == [first.id]

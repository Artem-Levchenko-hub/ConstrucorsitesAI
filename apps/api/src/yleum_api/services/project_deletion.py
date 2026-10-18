"""Durable owner deletion: one request, independently recoverable server execution."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import structlog
from fastapi import status
from sqlalchemy import delete, event, or_, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from yleum_api.core.crypto import decrypt_strong
from yleum_api.core.errors import ApiError
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.max_integration import MaxIntegration
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellCandidate,
    ProjectCellOperation,
    ProjectCellProof,
    ProjectCellWorkspace,
)
from yleum_api.models.usage import Usage
from yleum_api.models.wallet_charge import WalletCharge
from yleum_api.schemas.project import is_fullstack
from yleum_api.services import max_client, orchestrator_client
from yleum_api.services import repo as repo_svc
from yleum_api.services.project_cell_deletion import (
    request_project_cell_deletion,
    teardown_project_cell,
)
from yleum_api.services.project_cells import mark_cell_operation_indeterminate

log = structlog.get_logger("project_deletion")
DeletionResult = Literal["completed", "pending", "busy", "missing"]
_DELETE_LOCK_NAMESPACE = "yleum-project-deletion-v1"
_POLL_SECONDS = 5
_WORKER_CONCURRENCY = 2


class DeletionOwnershipLost(RuntimeError):
    """The pinned PostgreSQL session no longer owns this deletion."""


@dataclass(frozen=True, slots=True)
class DeletionClaim:
    connection: AsyncConnection
    backend_pid: int

    async def verify(self, session: AsyncSession, project_id: UUID) -> None:
        if self.connection.invalidated or self.connection.closed:
            raise DeletionOwnershipLost("deletion ownership connection was lost")
        pid, owned = (await session.execute(text("""
            SELECT pg_backend_pid(), EXISTS (
                SELECT 1 FROM pg_locks
                WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted
                AND classid = (hashtext(:namespace)::bigint & 4294967295)
                AND objid = (hashtext(:project_id)::bigint & 4294967295)
                AND objsubid = 2
            )
        """), {"namespace": _DELETE_LOCK_NAMESPACE, "project_id": str(project_id)})).one()
        if pid != self.backend_pid or not owned:
            raise DeletionOwnershipLost("deletion ownership session changed")


@asynccontextmanager
async def _guard_claim_sql(claim: DeletionClaim, project_id: UUID) -> AsyncIterator[None]:
    """Fence *all* SQL, including nested lifecycle persistence/recovery sessions.

    A bound SQLAlchemy connection can reconnect after rollback/invalidation.
    Session factories must never turn that into unowned persistence or a new
    controller fence. Check the original driver identity before execute and its
    actual session lock immediately before each payload statement. The raw
    cursor check bypasses SQLAlchemy events, so it cannot recurse.
    """
    connection = claim.connection
    sync = connection.sync_connection
    if sync is None or connection.closed or connection.invalidated:
        raise DeletionOwnershipLost("deletion ownership connection is unavailable")
    original = sync.connection.driver_connection
    if original is None:
        raise DeletionOwnershipLost("deletion ownership driver is unavailable")
    lost = False

    def require_original(current: Connection, *_args: object) -> None:
        nonlocal lost
        try:
            if lost or current.closed or current.invalidated:
                raise DeletionOwnershipLost("deletion ownership connection was lost")
            driver = current.connection.driver_connection
            if (
                driver is None or driver is not original or driver.is_closed()
                or driver.get_server_pid() != claim.backend_pid
            ):
                raise DeletionOwnershipLost("deletion ownership physical session changed")
        except Exception as exc:
            lost = True
            raise DeletionOwnershipLost("deletion SQL has no original ownership session") from exc

    def before_statement(current: Connection, *_args: object) -> None:
        nonlocal lost
        require_original(current)
        cursor = current.connection.cursor()
        try:
            cursor.execute("""
                SELECT pg_backend_pid(), EXISTS (
                    SELECT 1 FROM pg_locks
                    WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted
                    AND classid = (hashtext($1::text)::bigint & 4294967295)
                    AND objid = (hashtext($2::text)::bigint & 4294967295)
                    AND objsubid = 2
                )
            """, (_DELETE_LOCK_NAMESPACE, str(project_id)))
            observed = cursor.fetchone()
            if not observed or observed[0] != claim.backend_pid or not observed[1]:
                raise DeletionOwnershipLost("deletion session lock is absent")
        except Exception as exc:
            lost = True
            raise DeletionOwnershipLost("deletion SQL ownership cannot be confirmed") from exc
        finally:
            cursor.close()

    event.listen(sync, "before_execute", require_original)
    event.listen(sync, "before_cursor_execute", before_statement)
    try:
        yield
    finally:
        event.remove(sync, "before_cursor_execute", before_statement)
        event.remove(sync, "before_execute", require_original)
        if lost:
            # Do not pool an uncertain physical session, even when a fallback
            # swallowed the initial transport failure and attempted to reconnect.
            await connection.invalidate()


async def _owned_project(session: AsyncSession, project_id: UUID, owner_id: UUID) -> Project:
    project = await session.get(Project, project_id, populate_existing=True)
    if project is None:
        raise ApiError("not_found", "project not found", 404)
    if project.owner_id != owner_id:
        raise ApiError("forbidden", "not your project", 403)
    return project


@asynccontextmanager
async def project_deletion_claim(
    engine: AsyncEngine, project_id: UUID,
) -> AsyncIterator[DeletionClaim | None]:
    """Pin a dedicated PostgreSQL connection until the entire attempt finishes.

    The lock is session-scoped, not transaction-scoped: lifecycle commits cannot
    release it. A process/connection loss releases ownership; an exact durable
    controller envelope is still required to reconcile an interrupted effect.
    Never return a possibly locked connection to the pool.
    """
    async with engine.connect() as connection:
        claimed = False
        lock_outcome_known = False
        try:
            claimed = bool(await connection.scalar(text(
                "SELECT pg_try_advisory_lock(hashtext(:namespace), hashtext(:project_id))"
            ), {"namespace": _DELETE_LOCK_NAMESPACE, "project_id": str(project_id)}))
            lock_outcome_known = True
            pid = await connection.scalar(text("SELECT pg_backend_pid()"))
            await connection.commit()
            if claimed:
                claim = DeletionClaim(connection, pid)
                async with _guard_claim_sql(claim, project_id):
                    yield claim
            else:
                yield None
        finally:
            if not lock_outcome_known:
                # A lost ACK may still have acquired a session lock. Rolling
                # back its transaction would return that lock to the pool.
                await connection.invalidate()
            elif claimed:
                unlocked = False
                try:
                    if not connection.invalidated and not connection.closed:
                        unlocked = bool(await connection.scalar(text(
                            "SELECT pg_advisory_unlock(hashtext(:namespace), hashtext(:project_id))"
                        ), {"namespace": _DELETE_LOCK_NAMESPACE, "project_id": str(project_id)}))
                        await connection.commit()
                except Exception as exc:
                    log.warning(
                        "project_delete.claim_release_failed", error_type=type(exc).__name__,
                    )
                finally:
                    if not unlocked:
                        await connection.invalidate()


async def request_project_deletion(
    session: AsyncSession, project_id: UUID, owner_id: UUID,
) -> bool:
    """Return True for a durable Cell deletion, False only after full sync cleanup."""
    project = await _owned_project(session, project_id, owner_id)
    if await request_project_cell_deletion(session, project):
        return True
    # Legacy/static projects have no durable Cell tombstone. Preserve their
    # synchronous contract while sharing ownership and the complete finalizer.
    bind = session.bind
    assert isinstance(bind, (AsyncEngine, AsyncConnection))
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    async with project_deletion_claim(engine, project_id) as claim:
        if claim is None:
            raise ApiError("conflict", "Удаление проекта уже выполняется", 409)
        async with AsyncSession(bind=claim.connection, expire_on_commit=False) as cleanup_session:
            project = await _owned_project(cleanup_session, project_id, owner_id)
            # Recheck admission after the first no-Cell transaction committed:
            # generation/restoration/ownership or a Cell may have appeared.
            # If still legacy, retain the project xact lock through final commit.
            if await request_project_cell_deletion(
                cleanup_session, project, commit_without_cell=False,
            ):
                return True
            await _delete_project_runtime_and_records(
                cleanup_session, project,
                before_effect=lambda: claim.verify(cleanup_session, project_id),
            )
    session.expunge_all()
    return False


async def _recover_interrupted_delete_steps(session: AsyncSession, workspace_id: UUID) -> None:
    """Only the exclusive executor may recover its prior running SQL dispatch.

    This does not assign an exit status or alter the controller command journal.
    Unknown effects remain indeterminate and must replay/observe their fence.
    Other kinds of owner work and generation-owned operations are untouched.
    """
    operations = (await session.scalars(select(ProjectCellOperation).where(
        ProjectCellOperation.workspace_id == workspace_id,
        ProjectCellOperation.generation_run_id.is_(None),
        ProjectCellOperation.execution_run_id.is_(None),
        ProjectCellOperation.kind.in_(("destroy", "reconcile")),
        ProjectCellOperation.idempotency_key.startswith(f"project-delete:{workspace_id}"),
        ProjectCellOperation.status == "running",
    ).with_for_update())).all()
    for operation in operations:
        await mark_cell_operation_indeterminate(
            session, operation.id, "delete_executor_interrupted",
        )
    await session.commit()


async def finish_pending_project_deletion(
    engine: AsyncEngine, project_id: UUID,
) -> DeletionResult:
    """Claim one already-admitted intent; never infer intent from an API retry."""
    async with project_deletion_claim(engine, project_id) as claim:
        if claim is None:
            return "busy"
        # Every SQL mutation uses the same connection as the claim. Reconnecting
        # cannot silently finish a financial/data deletion without ownership.
        factory = async_sessionmaker(claim.connection, expire_on_commit=False)
        async with factory() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return "missing"
            workspace = await session.scalar(select(ProjectCellWorkspace).where(
                ProjectCellWorkspace.project_id == project_id,
                ProjectCellWorkspace.state == "deleting",
                ProjectCellWorkspace.deleted_at.is_not(None),
            ))
            if workspace is None:
                return "pending"
            if (
                workspace.owner_id != project.owner_id
                or workspace.provider != "docker_owner_canary"
            ):
                log.error("project_delete.identity_mismatch", project_id=str(project_id))
                return "pending"
            await _recover_interrupted_delete_steps(session, workspace.id)
            try:
                await claim.verify(session, project_id)
                await teardown_project_cell(session, project)
                await _delete_project_runtime_and_records(
                    session, project, before_effect=lambda: claim.verify(session, project_id),
                )
            except Exception as exc:
                # No error is a successful deletion. Preserve the tombstone and
                # ledger; subsequent worker cycles retry the same durable chain.
                try:
                    await session.rollback()
                except Exception:
                    await claim.connection.invalidate()
                log.warning(
                    "project_delete.pending", project_id=str(project_id),
                    error_type=type(exc).__name__,
                )
                return "pending"
            log.info("project_delete.completed", project_id=str(project_id))
            return "completed"


async def pending_project_deletions(
    engine: AsyncEngine, *, limit: int = 20, exclude: tuple[UUID, ...] = (),
) -> list[UUID]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        return list((await session.scalars(select(ProjectCellWorkspace.project_id).where(
            ProjectCellWorkspace.state == "deleting",
            ProjectCellWorkspace.deleted_at.is_not(None),
            ProjectCellWorkspace.project_id.not_in(exclude),
        ).order_by(
            ProjectCellWorkspace.deleted_at, ProjectCellWorkspace.project_id,
        ).limit(limit))).all())


async def advance_project_deletions(engine: AsyncEngine, *, limit: int = 20) -> int:
    """One bounded worker cycle, also usable by isolated acceptance checks."""
    gate = asyncio.Semaphore(_WORKER_CONCURRENCY)

    async def finish(project_id: UUID) -> DeletionResult:
        async with gate:
            return await finish_pending_project_deletion(engine, project_id)

    results = await asyncio.gather(*(finish(project_id) for project_id in (
        await pending_project_deletions(engine, limit=limit)
    )))
    return results.count("completed")


async def run_project_deletion_forever(
    *, before_tick: Callable[[], Awaitable[None]] | None = None,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from yleum_api.core.config import get_settings

    # Each worker thread owns its engine/event loop; never reuse API pools here.
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    active: dict[UUID, asyncio.Task[DeletionResult]] = {}
    attempted: set[UUID] = set()
    log.info("project_delete.worker_started", poll_seconds=_POLL_SECONDS)
    try:
        while True:
            try:
                for project_id, task in list(active.items()):
                    if task.done():
                        del active[project_id]
                        try:
                            task.result()
                        except Exception:
                            log.exception(
                                "project_delete.attempt_failed", project_id=str(project_id),
                            )
                if before_tick is not None:
                    await before_tick()
                # Rotate through the whole durable backlog: two unreachable old
                # Cells must not starve a healthy deletion on another host.
                excluded = tuple(attempted | active.keys())
                pending = await pending_project_deletions(engine, exclude=excluded)
                if not pending:
                    attempted.intersection_update(active)
                    pending = await pending_project_deletions(engine, exclude=tuple(active))
                for project_id in pending:
                    if len(active) >= _WORKER_CONCURRENCY:
                        break
                    attempted.add(project_id)
                    active[project_id] = asyncio.create_task(
                        finish_pending_project_deletion(engine, project_id),
                        name=f"project-delete:{project_id}",
                    )
            except Exception:
                log.exception("project_delete.scan_failed")
            await asyncio.sleep(_POLL_SECONDS)
    finally:
        for task in active.values():
            task.cancel()
        await asyncio.gather(*active.values(), return_exceptions=True)
        await engine.dispose()


async def _delete_project_runtime_and_records(
    session: AsyncSession, project: Project,
    *, before_effect: Callable[[], Awaitable[None]],
) -> None:
    """Finish every teardown stage before deleting SQL; retain immutable billing."""
    await before_effect()
    max_integration = (
        await session.execute(
            select(MaxIntegration).where(MaxIntegration.project_id == project.id)
        )
    ).scalar_one_or_none()
    if max_integration is not None and max_integration.webhook_url:
        try:
            await max_client.unsubscribe(
                decrypt_strong(max_integration.bot_token_enc),
                max_integration.webhook_url,
            )
        except max_client.MaxClientError as exc:
            raise ApiError(
                "max_webhook_failed",
                f"Не удалось отключить webhook MAX: {exc}",
                status.HTTP_502_BAD_GATEWAY,
            ) from exc

    if is_fullstack(project.template):
        # Containers/schema/nginx — idempotent teardown. Errors propagate
        # (503/4xx) so the project row survives for a retry, no orphans.
        await before_effect()
        await orchestrator_client.destroy(project.id, project.slug)

    # Bare-repo tarball in MinIO (idempotent). Snapshots + messages cascade at
    # the ORM layer when the project row goes.
    await before_effect()
    await asyncio.to_thread(repo_svc.delete_repo, project.id)
    await before_effect()

    # Keep the immutable billing ledger while breaking every project-owned FK
    # path before PostgreSQL cascades messages and generation runs. Production
    # migrations can order the SET NULL/CASCADE triggers differently from a
    # fresh metadata schema; without this explicit update, deleting a project
    # may try to null one usage reference after another target row is gone.
    project_run_ids = select(GenerationRun.id).where(
        GenerationRun.project_id == project.id
    )
    project_message_ids = select(Message.id).where(Message.project_id == project.id)
    # Cell release evidence (proofs, their results, candidates) references the
    # project's generation runs with ON DELETE RESTRICT on purpose: evidence must
    # never vanish because a run was dropped. Deleting the whole project is the
    # one legitimate case where the evidence goes too, so remove it explicitly
    # here — otherwise PostgreSQL may cascade projects → generation_runs before
    # the workspace cascade has reached the proofs and refuse the delete
    # (seen live 2026-09-22: DELETE /api/projects/{id} → 500 IntegrityError).
    project_workspace_ids = select(ProjectCellWorkspace.id).where(
        ProjectCellWorkspace.project_id == project.id
    )
    evidence_scope = or_(
        ProjectCellCandidate.generation_run_id.in_(project_run_ids),
        ProjectCellCandidate.workspace_id.in_(project_workspace_ids),
    )
    # Candidates may point at each other (expected_accepted_candidate_id, also
    # RESTRICT); break those links before removing the rows.
    await session.execute(
        update(ProjectCellCandidate)
        .where(evidence_scope)
        .values(expected_accepted_candidate_id=None)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(ProjectCellCandidate)
        .where(evidence_scope)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(ProjectCellProof)
        .where(
            or_(
                ProjectCellProof.generation_run_id.in_(project_run_ids),
                ProjectCellProof.workspace_id.in_(project_workspace_ids),
            )
        )
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(WalletCharge)
        .where(WalletCharge.message_id.in_(project_message_ids))
        .values(message_id=None)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(Usage)
        .where(
            or_(
                Usage.project_id == project.id,
                Usage.run_id.in_(project_run_ids),
                Usage.message_id.in_(project_message_ids),
            )
        )
        .values(project_id=None, run_id=None, message_id=None)
        .execution_options(synchronize_session=False)
    )

    await session.delete(project)
    await session.commit()

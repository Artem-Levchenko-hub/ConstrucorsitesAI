"""Run-bound owner preview slots. No publication, generation billing or live DB writes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4, uuid5

import structlog
from pydantic import BaseModel, ConfigDict, Field

from yleum_orchestrator.core.cell_resources import (
    CellCapacityUnavailable,
    CellIdentityConflict,
    WorkspaceLockTimeout,
)
from yleum_orchestrator.core.config import get_settings
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.routers import workspace as ws
from yleum_orchestrator.schemas.workspace import (
    WorkspaceCheckpointPreviewRequest,
    WorkspaceDraftPreviewSessionResponse,
)
from yleum_orchestrator.services import checkpoint_preview_runtime as driver
from yleum_orchestrator.services import generation_reader_inventory as readers
from yleum_orchestrator.services.cell_deletion import is_workspace_deleted
from yleum_orchestrator.services.cell_state import _read_plain_json_file
from yleum_orchestrator.services.docker_cell_resources import DockerCellResourceManager
from yleum_orchestrator.services.project_machine import write_controller_json

log = structlog.get_logger(__name__)
_IDLE_SECONDS = 6 * 60 * 60
_jobs: dict[UUID, asyncio.Task[None]] = {}


class Slot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical: UUID
    project: UUID
    owner: UUID
    run: UUID
    checkpoint: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    lease: UUID
    phase: Literal["preparing", "ready", "failed", "capacity", "retiring"]
    reason: str = ""

    @property
    def candidate(self) -> UUID:
        return uuid5(self.canonical, f"owner-draft-preview:{self.run}:{self.checkpoint}")

    def matches(self, req: WorkspaceCheckpointPreviewRequest) -> bool:
        return (self.project, self.owner, self.run, self.checkpoint) == (
            req.project_id,
            req.owner_id,
            req.run_id,
            req.checkpoint_digest,
        )


def _slot_path(canonical: UUID) -> Path:
    return (
        Path(get_settings().cell_state_path).parent
        / "owner-checkpoint-previews"
        / f"{canonical}.json"
    )


def _read(canonical: UUID) -> Slot | None:
    path = _slot_path(canonical)
    if not path.exists() and not path.is_symlink():
        return None
    slot = Slot.model_validate(_read_plain_json_file(path))
    if slot.canonical != canonical or slot.candidate in {slot.canonical, slot.project}:
        raise CellIdentityConflict("checkpoint slot identity changed")
    return slot


def _write(slot: Slot) -> None:
    write_controller_json(_slot_path(slot.canonical), slot.model_dump(mode="json"), durable=True)


def _manager(identity: UUID) -> DockerCellResourceManager:
    return ws._require_docker_resource_manager(ws._workspace_provider(identity))


def _canonical_guard(
    manager: DockerCellResourceManager, canonical: UUID, project: UUID, owner: UUID
) -> None:
    if is_workspace_deleted(manager.state_store.root, canonical):
        raise OrchestratorError("not_found", "preview workspace no longer exists", 404)
    state = manager.state_store.load(canonical)
    if state is None or (state.workspace_id, state.project_id, state.owner_id) != (
        canonical,
        project,
        owner,
    ):
        raise OrchestratorError("not_found", "preview workspace not found", 404)
    if state.active_generation_run_id is not None or state.phase != "completed":
        raise _conflict("draft_preview_workspace_busy")


def _conflict(reason: str) -> OrchestratorError:
    return OrchestratorError(
        "conflict", "Working preview is not ready", 409, details={"reason": reason}
    )


async def _clean(slot: Slot) -> None:
    await driver.retire_candidate(
        _manager(slot.candidate),
        slot.candidate,
        slot.project,
        slot.owner,
        slot.lease,
        slot.revision,
    )


async def _recover_failure(slot: Slot, exc: Exception) -> None:
    slot.phase = "retiring"
    slot.reason = type(exc).__name__
    log.warning(
        "checkpoint_preview_prepare_failed",
        canonical=str(slot.canonical),
        error_type=slot.reason,
        diagnostic_id=hashlib.sha256(str(exc).encode()).hexdigest()[:12],
    )
    _write(slot)
    await _clean(slot)
    slot.phase = (
        "capacity"
        if isinstance(exc, (CellCapacityUnavailable, readers.ReaderInventoryUnavailable))
        else "failed"
    )
    _write(slot)


async def _run(
    canonical: UUID, request: WorkspaceCheckpointPreviewRequest, *, restart: bool
) -> None:
    manager = _manager(canonical)
    async with manager.operation_lock.hold_named(f"checkpoint-preview-{canonical}"):
        # The release observer checks this lock. A job delayed until inhibit
        # must fail before starting any Docker work or changing admission.
        await asyncio.to_thread(readers.require_if_configured)
        _canonical_guard(manager, canonical, request.project_id, request.owner_id)
        slot = _read(canonical)
        if slot and not slot.matches(request):
            if request.files is None:
                return
            slot.phase = "retiring"
            _write(slot)
            await _clean(slot)
            _slot_path(canonical).unlink()
            slot = None
        if slot and slot.phase == "ready":
            if restart:
                try:
                    await driver.resume_candidate(
                        _manager(slot.candidate),
                        slot.candidate,
                        slot.project,
                        slot.owner,
                        repair=True,
                    )
                except Exception as exc:
                    await _recover_failure(slot, exc)
            return
        if slot and slot.phase in {"preparing", "retiring"}:
            # An interrupted effect is reconciled by the sweeper under this lock.
            return
        if request.files is None or request.source_revision is None:
            return
        slot = Slot(
            canonical=canonical,
            project=request.project_id,
            owner=request.owner_id,
            run=request.run_id,
            checkpoint=request.checkpoint_digest,
            revision=request.source_revision,
            lease=uuid4(),
            phase="preparing",
        )
        _write(slot)
        try:
            await driver.materialize(
                _manager(slot.candidate),
                slot.candidate,
                slot.project,
                slot.owner,
                slot.lease,
                slot.revision,
                request.files,
            )
            _canonical_guard(manager, canonical, slot.project, slot.owner)
            slot.phase = "ready"
            _write(slot)
        except Exception as exc:
            await _recover_failure(slot, exc)


def _schedule(
    canonical: UUID, request: WorkspaceCheckpointPreviewRequest, *, restart: bool = False
) -> None:
    if canonical in _jobs:
        return
    task = asyncio.create_task(
        _run(canonical, request, restart=restart), name=f"checkpoint-preview-{canonical}"
    )
    _jobs[canonical] = task

    def finished(work: asyncio.Task[None]) -> None:
        _jobs.pop(canonical, None)
        if not work.cancelled() and (failure := work.exception()) is not None:
            if not isinstance(failure, WorkspaceLockTimeout):
                log.warning(
                    "checkpoint_preview_background_failed",
                    canonical=str(canonical),
                    error_type=type(failure).__name__,
                )

    task.add_done_callback(finished)


async def checkpoint_session(
    canonical: UUID, request: WorkspaceCheckpointPreviewRequest
) -> WorkspaceDraftPreviewSessionResponse:
    manager = _manager(canonical)
    _canonical_guard(manager, canonical, request.project_id, request.owner_id)
    slot = _read(canonical)
    if slot and slot.matches(request):
        if request.source_revision is not None and request.source_revision != slot.revision:
            raise _conflict("draft_preview_source_changed")
        if slot.phase == "ready":
            try:
                session = await driver.signed_session(
                    _manager(slot.candidate), slot.candidate, slot.project, slot.owner
                )
            except driver.PreviewProcessStopped:
                _schedule(canonical, request, restart=True)
                raise _conflict("draft_preview_preparing") from None
            # session only signs a short-lived private capability for a running
            # candidate. Protect the active tab from the subsequent idle sweep.
            path = _slot_path(canonical)
            if time.time() - path.stat().st_mtime > 60:
                async with manager.operation_lock.hold_named(f"checkpoint-preview-{canonical}"):
                    if _read(canonical) != slot:
                        raise _conflict("draft_preview_preparing")
                    if os.utime in os.supports_follow_symlinks:
                        os.utime(path, None, follow_symlinks=False)
                    else:
                        _write(slot)
            return session
        if slot.phase in {"preparing", "retiring"}:
            raise _conflict("draft_preview_preparing")
        if time.time() - _slot_path(canonical).stat().st_mtime < 30:
            if slot.phase == "failed":
                raise _conflict("draft_preview_build_failed")
            raise OrchestratorError(
                "capacity_wait",
                "Preview capacity is temporarily busy",
                429,
                details={"reason": "draft_preview_capacity"},
            )
    if canonical in _jobs:
        raise _conflict("draft_preview_preparing")
    if request.files is None:
        raise _conflict("draft_preview_source_required")
    # Recheck on the worker after taking the release-visible lock too.
    await asyncio.to_thread(readers.require_if_configured)
    _schedule(canonical, request)
    raise _conflict("draft_preview_preparing")


async def sweep_previews() -> None:
    directory = Path(get_settings().cell_state_path).parent / "owner-checkpoint-previews"
    if not directory.is_dir():
        return
    for path in directory.glob("*.json"):
        try:
            key = UUID(path.stem)
            if key in _jobs:
                continue
            manager = _manager(key)
            slot = _read(key)
            if slot is None or not _due(slot, path, manager):
                continue
            async with manager.operation_lock.hold_named(f"checkpoint-preview-{key}"):
                await asyncio.to_thread(readers.require_if_configured)
                slot = _read(key)
                if slot is None or not _due(slot, path, manager):
                    continue
                slot.phase = "retiring"
                _write(slot)
                await _clean(slot)
                path.unlink()
        except WorkspaceLockTimeout:
            continue
        except Exception as exc:
            log.warning(
                "checkpoint_preview_cleanup_pending", slot=path.stem, error_type=type(exc).__name__
            )


def _due(slot: Slot, path: Path, manager: DockerCellResourceManager) -> bool:
    # A ready but idle cell can always be reconstructed from the saved draft
    # with no model call. Its temporary DB is disposable, including on expiry.
    return (
        slot.phase in {"retiring", "preparing"}
        or is_workspace_deleted(manager.state_store.root, slot.canonical)
        or time.time() - path.stat().st_mtime >= _IDLE_SECONDS
    )


async def stop_preview_tasks() -> None:
    pending = list(_jobs.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

"""A private still of the working environment, never a published version or a build verdict."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import TYPE_CHECKING, Literal
from urllib.parse import urljoin
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.minio import get_minio_client
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.services.snapshot_preview_capture import _dimensions, _main_route, _upload
from yleum_api.workers.preview import capture_private_draft_png

if TYPE_CHECKING:
    from yleum_api.services.generation.contracts import GenerationIds
    from yleum_api.services.project_cell_executor import ProjectCellExecutorHandle

log = logging.getLogger(__name__)
KEY = "draft_preview_image_v1"
_WIDTH, _HEIGHT = 390, 844
_LIMIT = 4 * 1024 * 1024
_SHA256 = re.compile(r"[a-f0-9]{64}")
ImageOrigin = Literal["working_environment", "reconstructed_checkpoint"]


def _key(project_id: UUID, run_id: UUID, digest: str) -> str:
    return f"generation-checkpoints/{project_id}/{run_id}/previews/{digest}.png"


def preview_digest(run: GenerationRun) -> str | None:
    state = run.agent_state
    if not isinstance(state, dict):
        return None
    ref, checkpoint = state.get(KEY), state.get("source_checkpoint")
    if not isinstance(ref, dict) or not isinstance(checkpoint, dict):
        return None
    source, digest = ref.get("checkpoint_sha256"), ref.get("png_sha256")
    if (not isinstance(source, str) or not _SHA256.fullmatch(source)
            or source != checkpoint.get("digest")
            or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            or ref.get("origin") not in ("working_environment", "reconstructed_checkpoint")):
        return None
    return digest


async def _owned_image_run(
    session: AsyncSession, ids: GenerationIds, checkpoint_digest: str, *, lock: bool,
) -> GenerationRun | None:
    query = select(GenerationRun).join(Project, Project.id == GenerationRun.project_id).where(
        Project.id == ids.project_id, Project.owner_id == ids.user_id,
        Project.template == "max_miniapp",
        GenerationRun.id == ids.run_id, GenerationRun.user_id == ids.user_id,
        GenerationRun.status.in_(("running", "failed", "cancelled", "cancel_requested")),
    )
    if lock:
        query = query.with_for_update(of=GenerationRun)
    run = await session.scalar(query)
    checkpoint = (run.agent_state or {}).get("source_checkpoint") if run else None
    if not isinstance(checkpoint, dict) or checkpoint.get("digest") != checkpoint_digest:
        return None
    return run


async def save_draft_image(
    factory: async_sessionmaker[AsyncSession], ids: GenerationIds, checkpoint_digest: str,
    png: bytes, *, origin: ImageOrigin, limited: bool = False,
) -> bool:
    if (not _SHA256.fullmatch(checkpoint_digest) or len(png) > _LIMIT
            or _dimensions(png, _WIDTH) != (_WIDTH, _HEIGHT)
            or origin not in ("working_environment", "reconstructed_checkpoint")):
        return False
    digest = hashlib.sha256(png).hexdigest()
    async with factory() as session:
        run = await _owned_image_run(session, ids, checkpoint_digest, lock=False)
        if run is None:
            return False
        if preview_digest(run):
            return True
    # An object-store outage must not hold the generation row lock. Uploaded
    # bytes cannot be read before the exact checkpoint is rechecked below.
    await asyncio.to_thread(_upload, _key(ids.project_id, ids.run_id, digest), png)
    async with factory() as session:
        run = await _owned_image_run(session, ids, checkpoint_digest, lock=True)
        if run is None:
            return False
        if preview_digest(run):
            return True
        run.agent_state = {
            **run.agent_state, KEY: {
                "checkpoint_sha256": checkpoint_digest, "png_sha256": digest, "origin": origin,
                "limited": limited and origin == "working_environment",
            },
        }
        await session.commit()
    return True


async def capture_before_rollback(
    factory: async_sessionmaker[AsyncSession], ids: GenerationIds, checkpoint_digest: str,
    files: dict[str, str], handle: ProjectCellExecutorHandle,
) -> None:
    """View the already running lease; never start a server, repair or delay rollback on errors."""
    try:
        async with asyncio.timeout(15):
            open_session = handle.existing_preview_session
            if open_session is None:
                return
            async with factory() as session:
                run = await _owned_image_run(session, ids, checkpoint_digest, lock=False)
                if run is None or preview_digest(run):
                    return
            try:
                route = _main_route(files)
            except ValueError:
                route = "/"
            preview = await open_session()
            if preview.workspace_id != handle.workspace_id:
                return
            capture = await capture_private_draft_png(
                preview.bootstrap_url, urljoin(preview.preview_url, route), _WIDTH, _HEIGHT,
            )
            # Revalidate the same lease once after navigation; a replacement
            # generation may have acquired the workspace while Chromium painted.
            after = await open_session()
            if (after.workspace_id != preview.workspace_id
                    or after.preview_url != preview.preview_url):
                return
            await save_draft_image(
                factory, ids, checkpoint_digest, capture.png,
                origin="working_environment", limited=capture.server_action_blocked,
            )
    except Exception as exc:
        # Signed URLs and guest exception text must not reach logs.
        log.info("Draft view unavailable run=%s kind=%s", ids.run_id, type(exc).__name__)


def read_draft_png(project_id: UUID, run_id: UUID, digest: str) -> bytes | None:
    if not _SHA256.fullmatch(digest):
        return None
    stream = get_minio_client().get_object(
        get_settings().minio_bucket_projects, _key(project_id, run_id, digest),
    )
    try:
        data = stream.read(_LIMIT + 1)
    finally:
        stream.close()
        stream.release_conn()
    if len(data) > _LIMIT or hashlib.sha256(data).hexdigest() != digest:
        return None
    return data

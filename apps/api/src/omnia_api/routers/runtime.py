"""V2 runtime + deploy proxy routes.

This file is the public seam between apps/web (Auth.js JWT in httpOnly
cookie) and apps/orchestrator (internal `X-Internal-Token` API). All
routes here:

  1. Verify the JWT (`CurrentUserDep`).
  2. Verify the project belongs to the current user (same `_project_owned_by`
     pattern used in snapshots / rollback / messages).
  3. Forward to orchestrator via `orchestrator_client`.
  4. Translate the orchestrator response into a stable `RuntimeStatus` /
     `DeployStatus` payload the frontend can rely on.

Routes follow `docs/01-api-contract.md` § "V2: Runtime + Deploy".
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, status

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.project import orchestrator_template
from omnia_api.schemas.runtime import (
    DeployRequest,
    DeployStatus,
    RuntimeLogs,
    RuntimeStatus,
    RuntimeStopRequest,
)
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/projects", tags=["runtime"])

# Container-backed templates whose dev container holds AI-generated files in its
# writable layer (no bind mount). Canonical list lives in routers/messages.py
# CONTAINER_NEXT; kept in sync. A recreated container (destroy+reprovision, host
# reboot losing the layer, manual cleanup) comes up running the *baked template*
# — the "Новый проект на Omnia.AI" starter — instead of the user's app, unless
# we re-push the latest snapshot. start_runtime does exactly that. `spa` (Vite +
# React, Phase 7.2) holds its AI files in the writable layer too.
_CONTAINER_NEXT = ("fullstack", "nextjs_entities", "spa", "realtime")


async def _project_owned_by(
    session: Any, project_id: UUID, user_id: UUID
) -> Project:
    """Same gate snapshots.py uses — raises 404 if not owned (no leak)."""
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


def _to_runtime_status(payload: dict[str, Any]) -> RuntimeStatus:
    """Project a (possibly larger) orchestrator response into the public shape."""
    return RuntimeStatus(
        state=payload.get("state", "stopped"),
        container_name=payload.get("container_name"),
        port=payload.get("port"),
        dev_url=payload.get("dev_url"),
        last_active_at=payload.get("last_active_at"),
        hibernate_after_seconds=payload.get("hibernate_after_seconds"),
    )


def _to_deploy_status(payload: dict[str, Any]) -> DeployStatus:
    return DeployStatus(
        phase=payload.get("phase", "queued"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        prod_url=payload.get("prod_url"),
        image_tag=payload.get("image_tag"),
        error=payload.get("error"),
    )


# --- Runtime ----------------------------------------------------------


@router.get("/{project_id}/runtime", response_model=RuntimeStatus)
async def get_runtime(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> RuntimeStatus:
    await _project_owned_by(session, project_id, current_user.id)
    payload = await orchestrator_client.get_status(project_id)
    return _to_runtime_status(payload)


@router.post("/{project_id}/runtime/start", response_model=RuntimeStatus)
async def start_runtime(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> RuntimeStatus:
    """Start (or provision-and-start) the project's dev container.

    Goes through orchestrator `provision`, which is idempotent — calling it for an
    existing project returns the live container info without rebuilding, and
    provisions on first call. (Wake-on-request is wired separately at the ingress
    layer, so a sleeping preview self-revives on the first visitor hit.)
    """
    project = await _project_owned_by(session, project_id, current_user.id)
    # Map api-side `template` to the orchestrator's actual template dir.
    # Static V1 templates (blank/landing/portfolio/blog) have no orchestrator
    # image — they ship as plain HTML via /p/<slug>. We default those to
    # `nextjs-postgres-drizzle` so a V1 user who hits "Start" can still
    # opt into a full backend (lazy upgrade) without re-creating the project.
    orch_template = (
        orchestrator_template(project.template) or "nextjs-postgres-drizzle"
    )
    payload = await orchestrator_client.provision(
        project_id=project_id,
        slug=project.slug,
        template=orch_template,
        tier="free",
    )

    # E3 — "always works, never the silent starter". provision is idempotent and
    # leaves an *existing* container's files untouched, but a recreated one boots
    # from the baked template (the "Новый проект на Omnia.AI" starter). If this
    # project has a generated snapshot, re-push its files so the user always sees
    # their app, not the starter. Fail-soft: a resync hiccup must not turn a
    # successful start into an error — git/MinIO stay canonical and the user can
    # hit "Запустить" again.
    if project.template in _CONTAINER_NEXT and project.current_snapshot_id:
        await _resync_latest_snapshot(session, project)

    return _to_runtime_status(payload)


async def _resync_latest_snapshot(session: SessionDep, project: Project) -> None:
    """Re-push the latest snapshot's files into the (possibly freshly recreated)
    dev container via orchestrator hot-reload, so an opened project shows its own
    code rather than the baked template starter. Best-effort; never raises."""
    try:
        snap = await session.get(Snapshot, project.current_snapshot_id)
        if snap is None:
            return
        files = await asyncio.to_thread(
            repo_svc.read_files, project.id, snap.commit_sha
        )
        if not files:
            return
        result = await orchestrator_client.hot_reload(
            project_id=project.id,
            slug=project.slug,
            files=files,
        )
        log.info(
            "runtime.start_resync",
            project_id=str(project.id),
            files=len(files),
            written=result.get("written"),
        )
    except Exception as exc:  # noqa: BLE001 — resync is best-effort
        log.warning(
            "runtime.start_resync_failed",
            project_id=str(project.id),
            err=str(exc),
        )


@router.get("/{project_id}/runtime/logs", response_model=RuntimeLogs)
async def get_runtime_logs(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    tail: int = 200,
    kind: str = "dev",
) -> RuntimeLogs:
    """Tail recent container stdout/stderr (capped at 5000 lines).

    Proxies to orchestrator's `/internal/projects/<id>/logs`. UI polls this
    every 3 s for a live feed; the orchestrator currently returns a flat
    snapshot rather than a stream because docker_client's API is sync and
    spinning up a follow-mode WebSocket here was deemed YAGNI for MVP.
    Missing container → empty `logs` with 200 (UI shows "No logs yet").
    """
    await _project_owned_by(session, project_id, current_user.id)
    if tail < 1:
        tail = 1
    elif tail > 5000:
        tail = 5000
    payload = await orchestrator_client.get_logs(project_id, tail=tail, kind=kind)
    return RuntimeLogs(
        container_name=payload.get("container_name"),
        tail=int(payload.get("tail", tail)),
        logs=str(payload.get("logs", "")),
    )


@router.post("/{project_id}/runtime/stop", response_model=RuntimeStatus)
async def stop_runtime(
    project_id: UUID,
    body: RuntimeStopRequest | None,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> RuntimeStatus:
    await _project_owned_by(session, project_id, current_user.id)
    pause = body.pause if body is not None else True
    payload = await orchestrator_client.stop(project_id, pause=pause)
    return _to_runtime_status(payload)


# --- Deploy -----------------------------------------------------------


@router.post("/{project_id}/deploy", response_model=DeployStatus)
async def trigger_deploy(
    project_id: UUID,
    body: DeployRequest | None,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> DeployStatus:
    await _project_owned_by(session, project_id, current_user.id)
    sha = body.commit_sha if body is not None else None
    payload = await orchestrator_client.deploy(project_id, commit_sha=sha)
    return _to_deploy_status(payload)


@router.get("/{project_id}/deploy", response_model=DeployStatus)
async def get_last_deploy(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> DeployStatus:
    """Last-deploy info, proxied from the orchestrator's persisted record.

    The orchestrator's `DeployResponse` shape mirrors `DeployStatus` 1-1, so
    `_to_deploy_status` projects it without massaging. If the orchestrator is
    unreachable OR has never recorded a deploy for this project, we fall back
    to `phase=queued` so the frontend's ON/OFF render path stays alive — same
    contract the placeholder used to enforce, without the lie that we
    "haven't implemented this yet".
    """
    await _project_owned_by(session, project_id, current_user.id)
    try:
        payload = await orchestrator_client.get_deploy(project_id)
    except Exception:  # noqa: BLE001 — fall back gracefully on any failure
        return DeployStatus(phase="queued")
    return _to_deploy_status(payload)

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

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.schemas.runtime import (
    DeployRequest,
    DeployStatus,
    RuntimeStatus,
    RuntimeStopRequest,
)
from omnia_api.services import orchestrator_client

router = APIRouter(prefix="/api/projects", tags=["runtime"])


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

    Phase A note: orchestrator's `wake` endpoint is still scaffold (returns 501
    "not yet implemented"). `provision` is the only fully wired path, and it's
    already idempotent — calling it for an existing project returns the live
    container info without rebuilding. So Phase A always goes through provision.

    When orchestrator/wake lands properly (sprint A1+), this body switches to
    the wake-first / provision-fallback flow — the public API contract here
    does not change.
    """
    project = await _project_owned_by(session, project_id, current_user.id)
    # Today there's only one orchestrator template (nextjs-postgres-drizzle),
    # so every project — fullstack or static — provisions into it when the
    # user hits Start. That lets a V1 static user opt into "give me a real
    # backend" without re-creating the project. When a second template lands
    # this becomes a real switch on `project.template`.
    payload = await orchestrator_client.provision(
        project_id=project_id,
        slug=project.slug,
        template="nextjs-postgres-drizzle",
        tier="free",
    )
    return _to_runtime_status(payload)


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

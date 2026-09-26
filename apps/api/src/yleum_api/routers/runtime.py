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

from typing import Any, NoReturn
from uuid import UUID

import structlog
from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.billing import BillingAccount, BillingPlan, Subscription
from yleum_api.models.project import Project
from yleum_api.schemas.runtime import (
    DeployRequest,
    DeployStatus,
    RuntimeStatus,
)
from yleum_api.services import orchestrator_client, project_cell_runtime
from yleum_api.services.billing_accounts import resolve_billing_account

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/projects", tags=["runtime"])

# Container-backed templates whose dev container holds AI-generated files in its
# writable layer (no bind mount). The browser-container family comes from
# schemas.project. A recreated container (destroy+reprovision, host
# reboot losing the layer, manual cleanup) comes up running the *baked template*
# — the "Новый проект на Yleum" starter — instead of the user's app, unless
# we re-push the latest snapshot. start_runtime does exactly that. `spa` (Vite +
# React, Phase 7.2) holds its AI files in the writable layer too.


async def _project_owned_by(session: AsyncSession, project_id: UUID, user_id: UUID) -> Project:
    """Same gate snapshots.py uses — raises 404 if not owned (no leak)."""
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


async def _billing_plan_for_user(
    session: AsyncSession,
    user_id: UUID,
    *,
    for_update_account: bool = False,
) -> tuple[BillingAccount, BillingPlan]:
    account = await resolve_billing_account(
        session,
        user_id,
        for_update=for_update_account,
    )
    plan = (
        await session.execute(
            select(BillingPlan)
            .join(Subscription, Subscription.plan_id == BillingPlan.id)
            .where(
                Subscription.billing_account_id == account.id,
                Subscription.status.in_(("trialing", "active", "past_due", "paused")),
            )
        )
    ).scalar_one()
    return account, plan


def _to_runtime_status(payload: dict[str, Any]) -> RuntimeStatus:
    """Project a (possibly larger) orchestrator response into the public shape."""
    return RuntimeStatus(
        state=payload.get("state", "stopped"),
        container_name=payload.get("container_name"),
        port=payload.get("port"),
        dev_url=payload.get("dev_url"),
        last_active_at=payload.get("last_active_at"),
        hibernate_after_seconds=payload.get("hibernate_after_seconds"),
        keep_alive=bool(payload.get("keep_alive")),
    )


def _to_deploy_status(payload: dict[str, Any]) -> DeployStatus:
    return DeployStatus(
        run_id=payload.get("run_id"),
        snapshot_id=payload.get("snapshot_id"),
        commit_sha=payload.get("commit_sha"),
        phase=payload.get("phase", "idle"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        prod_url=payload.get("prod_url"),
        image_tag=payload.get("image_tag"),
        error=payload.get("error"),
        detail=payload.get("detail"),
        target_label=payload.get("target_label"),
        target_id=payload.get("target_id"),
        can_cancel=bool(payload.get("can_cancel")),
        logs=list(payload.get("logs") or []),
        format_version=int(payload.get("format_version") or 1),
        stage=payload.get("stage"),
        stage_started_at=payload.get("stage_started_at"),
        heartbeat_at=payload.get("heartbeat_at"),
        progress=payload.get("progress"),
        stages=list(payload.get("stages") or []),
        metrics=dict(payload.get("metrics") or {}),
        error_stage=payload.get("error_stage"),
        reason_code=payload.get("reason_code"),
    )


# --- Runtime ----------------------------------------------------------


def _raise_no_cell() -> NoReturn:
    """A project without a cell has no runtime to talk to since the site builder left.

    Every project is a MAX app in its own Project Cell; the legacy dev-container
    runtime went with the builder. A row that somehow has no cell gets an honest
    refusal instead of a call into a runtime that no longer exists.
    """
    raise ApiError(
        "runtime_unavailable",
        "У проекта нет своей ячейки — среда приложения недоступна. "
        "Создайте приложение заново.",
        status.HTTP_409_CONFLICT,
    )


@router.get("/{project_id}/runtime", response_model=RuntimeStatus)
async def get_runtime(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> RuntimeStatus:
    project = await _project_owned_by(session, project_id, current_user.id)
    cell_status = await project_cell_runtime.load_project_cell_runtime_status(
        session,
        project,
        owner=current_user,
    )
    if cell_status is None:
        _raise_no_cell()
    return cell_status


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
    cell_status = await project_cell_runtime.start_project_cell_runtime(
        session,
        project,
        owner=current_user,
    )
    if cell_status is None:
        _raise_no_cell()
    return cell_status


# --- Deploy -----------------------------------------------------------


@router.post("/{project_id}/deploy", response_model=DeployStatus)
async def trigger_deploy(
    project_id: UUID,
    body: DeployRequest | None,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> DeployStatus:
    project = await _project_owned_by(session, project_id, current_user.id)
    selection = await project_cell_runtime.resolve_project_cell_public_selection(
        session,
        project,
        owner=current_user,
    )
    if selection.selected:
        from yleum_api.services.cell_publication import submit_publication

        payload = await submit_publication(
            session, project, selection.workspace,
            requested_sha=body.commit_sha if body else None,
            idempotency_key=body.idempotency_key if body else None,
        )
        return _to_deploy_status(payload)
    _raise_no_cell()


@router.get("/{project_id}/deploy", response_model=DeployStatus)
async def get_last_deploy(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> DeployStatus:
    """Last-deploy info, proxied from the orchestrator's persisted record.

    No saved deployment is idle, never a queued operation. An unreachable
    controller is an error, not a successful status. Snapshot identity is
    preserved so readiness can identify the exact published version.
    """
    await _project_owned_by(session, project_id, current_user.id)
    payload = await orchestrator_client.get_deploy(project_id)
    return _to_deploy_status(payload)


@router.get("/{project_id}/deploy/history", response_model=list[DeployStatus])
async def deploy_history(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> list[DeployStatus]:
    """Publication history of the project's cell — the dashboard's «История публикаций»."""
    await _project_owned_by(session, project_id, current_user.id)
    payloads = await orchestrator_client.get_deploy_history(project_id)
    return [_to_deploy_status(payload) for payload in payloads]

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.core.crypto import decrypt_strong
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.account import BusinessMember
from omnia_api.models.attestation import Attestation
from omnia_api.models.billing import BillingAccount, BillingPlan, Subscription
from omnia_api.models.custom_domain import CustomDomain
from omnia_api.models.deploy_target import DeployTarget
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.project import orchestrator_template
from omnia_api.schemas.runtime import (
    DeployRequest,
    DeployStatus,
    RuntimeKeepAliveRequest,
    RuntimeLogs,
    RuntimeStatus,
    RuntimeStopRequest,
)
from omnia_api.services import autoheal as autoheal_svc
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.billing_accounts import resolve_billing_account

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/projects", tags=["runtime"])

# Container-backed templates whose dev container holds AI-generated files in its
# writable layer (no bind mount). Canonical list lives in routers/messages.py
# CONTAINER_NEXT; kept in sync. A recreated container (destroy+reprovision, host
# reboot losing the layer, manual cleanup) comes up running the *baked template*
# — the "Новый проект на Omnia.AI" starter — instead of the user's app, unless
# we re-push the latest snapshot. start_runtime does exactly that. `spa` (Vite +
# React, Phase 7.2) holds its AI files in the writable layer too.
_CONTAINER_NEXT = ("fullstack", "nextjs_entities", "spa", "realtime", "max_miniapp")


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


async def _billing_account_user_ids(
    session: AsyncSession,
    account: BillingAccount,
) -> list[UUID]:
    if account.personal_user_id is not None:
        return [account.personal_user_id]
    if account.business_id is None:
        return []
    return list(
        (
            await session.execute(
                select(BusinessMember.user_id).where(
                    BusinessMember.business_id == account.business_id
                )
            )
        ).scalars()
    )


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
        phase=payload.get("phase", "queued"),
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
    )


# --- Runtime ----------------------------------------------------------


@router.get("/{project_id}/runtime", response_model=RuntimeStatus)
async def get_runtime(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> RuntimeStatus:
    project = await _project_owned_by(session, project_id, current_user.id)
    payload = await orchestrator_client.get_status(project_id)
    if not project.keep_alive_enabled and payload.get("keep_alive"):
        # Postgres is canonical. This also heals a stale orchestrator marker
        # after a downgrade or an interrupted disable request.
        try:
            await orchestrator_client.set_keep_alive(project_id, enabled=False)
        except Exception:
            log.warning("runtime.keep_alive_reconcile_failed", project_id=str(project_id))
        payload["keep_alive"] = False
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
    _, plan = await _billing_plan_for_user(session, current_user.id)
    # Map api-side `template` to the orchestrator's actual template dir.
    # Static V1 templates (blank/landing/portfolio/blog) have no orchestrator
    # image — they ship as plain HTML via /p/<slug>. We default those to
    # `nextjs-postgres-drizzle` so a V1 user who hits "Start" can still
    # opt into a full backend (lazy upgrade) without re-creating the project.
    orch_template = orchestrator_template(project.template) or "nextjs-postgres-drizzle"
    payload = await orchestrator_client.provision(
        project_id=project_id,
        slug=project.slug,
        template=orch_template,
        tier=plan.code if plan.code in {"free", "pro", "business"} else "free",
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

    # Auto-heal on open (owner 2026-07-16): if the just-opened app has a RED build,
    # repair it in the background — same fix as «Починить», no click. Fire-and-
    # forget + fail-soft + flag-gated + Redis-debounced (see services.autoheal), so
    # it never delays the start response and never fires unprompted when disabled.
    if project.template in _CONTAINER_NEXT:

        async def _autoheal_bg() -> None:
            try:
                _h = await autoheal_svc.maybe_autoheal_on_open(project_id, project.slug)
                print(f"[AUTOHEAL] {project.slug}: {_h}", flush=True)
            except Exception as _ah_exc:
                print(f"[AUTOHEAL] skipped: {_ah_exc!r}", flush=True)

        _ah_task = asyncio.create_task(_autoheal_bg())
        _ah_task.add_done_callback(lambda _t: None)

    return _to_runtime_status(payload)


async def _resync_latest_snapshot(session: SessionDep, project: Project) -> None:
    """Re-push the latest snapshot's files into the (possibly freshly recreated)
    dev container via orchestrator hot-reload, so an opened project shows its own
    code rather than the baked template starter. Best-effort; never raises."""
    try:
        snap = await session.get(Snapshot, project.current_snapshot_id)
        if snap is None:
            return
        files = await asyncio.to_thread(repo_svc.read_files, project.id, snap.commit_sha)
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
    except Exception as exc:
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
    project = await _project_owned_by(session, project_id, current_user.id)
    if tail < 1:
        tail = 1
    elif tail > 5000:
        tail = 5000
    if kind == "prod" and project.deploy_target_id is not None:
        target = await session.get(DeployTarget, project.deploy_target_id)
        if (
            target is None
            or target.verify_status != "ok"
            or not target.known_host_key
            or not target.resolved_ip
        ):
            raise ApiError(
                "deploy_target_not_verified",
                "Нельзя прочитать логи: VPS требует повторной проверки.",
                status.HTTP_409_CONFLICT,
            )
        payload = await orchestrator_client.get_remote_logs(
            project_id,
            {
                "host": target.ssh_host,
                "port": target.ssh_port,
                "user": target.ssh_user,
                "auth_type": target.ssh_auth_type,
                "secret": decrypt_strong(target.ssh_secret_enc),
                "known_host_key": target.known_host_key,
                "resolved_ip": target.resolved_ip,
            },
            tail=tail,
        )
        payload.setdefault("tail", tail)
        payload.setdefault("container_name", None)
    else:
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


@router.post("/{project_id}/runtime/keep-alive", response_model=RuntimeStatus)
async def set_runtime_keep_alive(
    project_id: UUID,
    body: RuntimeKeepAliveRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> RuntimeStatus:
    """Keep the dev runtime hot across inactivity and orchestrator restarts."""
    project = await _project_owned_by(session, project_id, current_user.id)
    if body.enabled:
        account, plan = await _billing_plan_for_user(
            session,
            current_user.id,
            for_update_account=True,
        )
        configured_slots = plan.entitlements.get("always_on_slots")
        always_on_slots = configured_slots if isinstance(configured_slots, int) else 0
        if always_on_slots < 1:
            raise ApiError(
                "subscription_entitlement_required",
                "Постоянно запущенный runtime доступен на тарифе Business",
                status.HTTP_402_PAYMENT_REQUIRED,
            )
        account_user_ids = await _billing_account_user_ids(session, account)
        active_slots = (
            await session.execute(
                select(Project.id).where(
                    Project.owner_id.in_(account_user_ids),
                    Project.keep_alive_enabled.is_(True),
                    Project.id != project_id,
                )
            )
        ).scalars().all()
        if len(active_slots) >= always_on_slots:
            raise ApiError(
                "subscription_entitlement_required",
                "Все постоянные runtime-слоты тарифа уже заняты",
                status.HTTP_409_CONFLICT,
            )
        # Provision or wake first. Only persist the promise after a successful
        # start, so the UI never says "always running" for a runtime that could
        # not be created.
        runtime = await start_runtime(project_id, session, current_user)
        await orchestrator_client.set_keep_alive(project_id, enabled=True)
        project.keep_alive_enabled = True
        await session.commit()
        return runtime.model_copy(update={"keep_alive": True, "hibernate_after_seconds": None})

    await orchestrator_client.set_keep_alive(project_id, enabled=False)
    project.keep_alive_enabled = False
    await session.commit()
    payload = await orchestrator_client.get_status(project_id)
    return _to_runtime_status(payload)


# --- Deploy -----------------------------------------------------------


@router.post("/{project_id}/deploy", response_model=DeployStatus)
async def trigger_deploy(
    project_id: UUID,
    body: DeployRequest | None,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> DeployStatus:
    project = await _project_owned_by(session, project_id, current_user.id)
    sha = body.commit_sha if body is not None else None
    idempotency_key = body.idempotency_key if body is not None else None
    # BYO-VPS: если у проекта выбран свой сервер — грузим цель, расшифровываем
    # креды и передаём оркестратору, чтобы он развернул образ на машине юзера.
    # None = наш хостинг (текущее поведение).
    target: dict[str, Any] | None = None
    domains: list[str] | None = None
    runtime_env: dict[str, str] | None = None
    if project.template == "max_miniapp":
        max_integration = (
            await session.execute(
                select(MaxIntegration).where(MaxIntegration.project_id == project_id)
            )
        ).scalar_one_or_none()
        if max_integration is not None:
            runtime_env = {
                "MAX_BOT_TOKEN": decrypt_strong(max_integration.bot_token_enc),
                "MAX_WEBHOOK_SECRET": decrypt_strong(max_integration.webhook_secret_enc),
                "MAX_API_BASE_URL": "https://platform-api2.max.ru",
            }
    if project.deploy_target_id is not None:
        dt = await session.get(DeployTarget, project.deploy_target_id)
        if dt is not None:
            if dt.verify_status != "ok" or not dt.known_host_key or not dt.resolved_ip:
                raise ApiError(
                    "deploy_target_not_verified",
                    "Выбранный VPS не прошёл защищённую проверку. Проверьте его заново.",
                    status.HTTP_409_CONFLICT,
                )
            target = {
                "host": dt.ssh_host,
                "port": dt.ssh_port,
                "user": dt.ssh_user,
                "auth_type": dt.ssh_auth_type,
                "secret": decrypt_strong(dt.ssh_secret_enc),
                "known_host_key": dt.known_host_key,
                "resolved_ip": dt.resolved_ip,
                "label": dt.label,
                "id": str(dt.id),
            }
            # Домены проекта — агент настроит их на VPS юзера (edge + авто-SSL).
            rows = (
                (
                    await session.execute(
                        select(CustomDomain.host).where(
                            CustomDomain.project_id == project_id,
                            CustomDomain.dns_status == "ok",
                        )
                    )
                )
                .scalars()
                .all()
            )
            domains = list(rows) or None
    # Deploy-attestation gate (Step 3, deploy ↔ proven): look up the build's saved
    # attestation, log its verdict, and refuse an unproven deploy only when blocking
    # is enabled. Advisory by default; lookup errors are advisory-safe (a DB hiccup
    # must never break a deploy).
    if get_settings().use_deploy_attestation_gate:
        _proven: bool | None = None
        _digest: str | None = None
        try:
            _stmt = select(Attestation).where(Attestation.project_id == project_id)
            if sha:
                _stmt = _stmt.where(Attestation.commit_sha == sha)
            _att = (
                (await session.execute(_stmt.order_by(Attestation.created_at.desc()).limit(1)))
                .scalars()
                .first()
            )
            _proven = bool(_att and _att.overall_passed)
            _digest = _att.digest if _att else None
        except Exception as _ge:
            print(f"[DEPLOY-GATE] lookup skipped: {_ge}", flush=True)
        if _proven is not None:
            print(
                f"[DEPLOY-GATE] project={project_id} sha={sha} proven={_proven} digest={_digest}",
                flush=True,
            )
            if get_settings().deploy_attestation_blocking and not _proven:
                raise ApiError(
                    "deploy_not_proven",
                    "Деплой заблокирован: сборка не прошла проверку изоляции/"
                    "безопасности. Уточни запрос и пересобери.",
                    status.HTTP_409_CONFLICT,
                )
    payload = await orchestrator_client.deploy(
        project_id,
        commit_sha=sha,
        target=target,
        domains=domains,
        runtime_env=runtime_env,
        idempotency_key=idempotency_key,
    )
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
    project = await _project_owned_by(session, project_id, current_user.id)
    payload = await orchestrator_client.get_deploy(project_id)
    current_target = str(project.deploy_target_id) if project.deploy_target_id else None
    deploy_matches_target = payload.get("target_id") == current_target
    if (
        payload.get("phase") == "done"
        and deploy_matches_target
        and project.previous_deploy_target_id is not None
    ):
        previous = await session.get(DeployTarget, project.previous_deploy_target_id)
        if previous is not None and previous.known_host_key and previous.resolved_ip:
            await orchestrator_client.teardown_remote_project(
                project.id,
                {
                    "host": previous.ssh_host,
                    "port": previous.ssh_port,
                    "user": previous.ssh_user,
                    "auth_type": previous.ssh_auth_type,
                    "secret": decrypt_strong(previous.ssh_secret_enc),
                    "known_host_key": previous.known_host_key,
                    "resolved_ip": previous.resolved_ip,
                },
            )
        project.previous_deploy_target_id = None
        await session.commit()
    if (
        payload.get("phase") == "done"
        and deploy_matches_target
        and project.deploy_target_id is not None
    ):
        domains = (
            (
                await session.execute(
                    select(CustomDomain).where(
                        CustomDomain.project_id == project_id,
                        CustomDomain.dns_status == "ok",
                    )
                )
            )
            .scalars()
            .all()
        )
        changed = False
        for domain in domains:
            if domain.cert_status not in {"active", "issuing"}:
                domain.cert_status = "issuing"
                domain.last_detail = "Caddy на VPS выпускает HTTPS-сертификат."
                changed = True
        if changed:
            await session.commit()
    return _to_deploy_status(payload)


@router.post("/{project_id}/deploy/cancel", response_model=DeployStatus)
async def cancel_deploy(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> DeployStatus:
    await _project_owned_by(session, project_id, current_user.id)
    payload = await orchestrator_client.cancel_deploy(project_id)
    return _to_deploy_status(payload)


@router.get("/{project_id}/deploy/history", response_model=list[DeployStatus])
async def deploy_history(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> list[DeployStatus]:
    await _project_owned_by(session, project_id, current_user.id)
    payloads = await orchestrator_client.get_deploy_history(project_id)
    return [_to_deploy_status(payload) for payload in payloads]

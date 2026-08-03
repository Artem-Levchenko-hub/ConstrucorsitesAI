"""Model-free MAX Studio configuration, managed kit and launch readiness."""

from __future__ import annotations

import asyncio
from datetime import datetime
from urllib.parse import parse_qsl, urlparse
from uuid import UUID

import structlog
from fastapi import APIRouter, Response, status
from pydantic import ValidationError
from sqlalchemy import func, select, text

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.usage import Usage
from omnia_api.schemas.max_studio import (
    MaxPreviewSessionPublic,
    MaxPreviewSessionUpstream,
    MaxProjectConfigPayload,
    MaxProjectConfigPublic,
    MaxReadinessItem,
    MaxReadinessPublic,
    MaxUrlAttachedPayload,
    MaxUsagePublic,
    MaxUsageStagePublic,
)
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.deploy_attestation import ensure_current_release_proof
from omnia_api.services.deployment_state import (
    current_snapshot_id_fresh,
    deployment_is_active,
)
from omnia_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
from omnia_api.services.max_project_kit import (
    MAX_MANAGED_KIT_VERSION,
    default_max_project_config,
    max_legacy_snapshot_incompatibility,
    render_max_entry_migration_files,
    render_max_managed_files,
)
from omnia_api.services.runtime_sync import (
    mark_runtime_sync_required,
    reconcile_locked_runtime,
)

router = APIRouter(prefix="/api/projects", tags=["max-studio"])
log = structlog.get_logger(__name__)


async def _owned_max_project(
    session: SessionDep, project_id: UUID, owner_id: UUID, *, lock: bool = False
) -> Project:
    statement = select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update()
    project = (await session.execute(statement)).scalar_one_or_none()
    if project is None:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.template != "max_miniapp":
        raise ApiError(
            "max_project_required",
            "Настройки MAX доступны только для проекта MAX Mini App",
            status.HTTP_409_CONFLICT,
        )
    return project


def _default_config(project: Project) -> MaxProjectConfigPayload:
    return default_max_project_config(project.name)


def _max_config_sync_files(
    payload: MaxProjectConfigPayload,
    project_id: UUID,
    current_files: dict[str, str],
) -> dict[str, str]:
    """Prepare a lossless kit/config sync or reject before any commit."""

    incompatibility = max_legacy_snapshot_incompatibility(current_files)
    if incompatibility:
        raise ApiError(
            "conflict",
            (
                "Старая версия использует серверную структуру, которую нельзя "
                "безопасно обновить только настройками. Сначала пересоберите приложение."
            ),
            status.HTTP_409_CONFLICT,
        )
    return {
        **render_max_managed_files(payload, project_id),
        **render_max_entry_migration_files(current_files),
    }


def _public(project: Project, record: MaxProjectConfig | None) -> MaxProjectConfigPublic:
    if record is None:
        return MaxProjectConfigPublic(
            project_id=project.id,
            config_version=0,
            config=_default_config(project),
        )
    return MaxProjectConfigPublic(
        project_id=project.id,
        config_version=record.config_version,
        config=MaxProjectConfigPayload.model_validate(record.config),
        synced_snapshot_id=record.synced_snapshot_id,
        updated_at=record.updated_at,
    )


async def _refresh_release_proof(session: SessionDep, project: Project) -> None:
    """Best-effort proof refresh; a failed proof remains deploy-blocking."""
    try:
        proof = await ensure_current_release_proof(session, project)
        log.info(
            "max.release_proof_refreshed",
            project_id=str(project.id),
            commit_sha=proof.commit_sha,
            passed=proof.passed,
            reason=proof.reason,
        )
    except Exception:
        log.warning(
            "max.release_proof_refresh_failed",
            project_id=str(project.id),
            exc_info=True,
        )


def _preview_session_public(project: Project, payload: object) -> MaxPreviewSessionPublic:
    """Accept only signed preview URLs for this project's dev hostname."""
    try:
        session = MaxPreviewSessionUpstream.model_validate(payload)
        parsed = urlparse(session.bootstrap_url)
        hostname = parsed.hostname or ""
    except (ValidationError, ValueError) as exc:
        raise orchestrator_client.OrchestratorUnavailable(
            "Orchestrator returned an invalid MAX preview session"
        ) from exc

    expected_prefix = f"{project.slug}-dev."
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query_keys = [key for key, _value in query_pairs]
    query = dict(query_pairs)
    expires = query.get("expires", "")
    signature = query.get("signature", "")
    valid_url = (
        session.project_id == project.id
        and parsed.scheme == "https"
        and hostname.startswith(expected_prefix)
        and len(hostname) > len(expected_prefix)
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.fragment
        and parsed.path == "/api/omnia/preview-session"
        and len(query_keys) == 2
        and set(query_keys) == {"expires", "signature"}
        and 10 <= len(expires) <= 12
        and expires.isascii()
        and expires.isdigit()
        and len(signature) == 43
        and all(char.isalnum() or char in "-_" for char in signature)
    )
    if not valid_url:
        raise orchestrator_client.OrchestratorUnavailable(
            "Orchestrator returned an invalid MAX preview session"
        )
    return MaxPreviewSessionPublic(url=session.bootstrap_url, expires_at=session.expires_at)


@router.get("/{project_id}/max/config", response_model=MaxProjectConfigPublic)
async def get_max_config(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> MaxProjectConfigPublic:
    project = await _owned_max_project(session, project_id, current_user.id)
    record = await session.get(MaxProjectConfig, project_id)
    return _public(project, record)


@router.post(
    "/{project_id}/max/preview-session",
    response_model=MaxPreviewSessionPublic,
)
async def create_max_preview_session(
    project_id: UUID,
    response: Response,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> MaxPreviewSessionPublic:
    project = await _owned_max_project(session, project_id, current_user.id)
    payload = await orchestrator_client.create_max_preview_session(project.id)
    response.headers["Cache-Control"] = "no-store"
    return _preview_session_public(project, payload)


@router.patch(
    "/{project_id}/max/config/url-attached",
    response_model=MaxProjectConfigPublic,
)
async def patch_max_url_attached(
    project_id: UUID,
    payload: MaxUrlAttachedPayload,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> MaxProjectConfigPublic:
    """Persist the manual MAX-cabinet confirmation without changing app code."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    project = await _owned_max_project(session, project_id, current_user.id, lock=True)
    record = await session.get(MaxProjectConfig, project_id)
    current = (
        MaxProjectConfigPayload.model_validate(record.config)
        if record is not None
        else _default_config(project)
    )
    if current.max_url_attached == payload.attached:
        return _public(project, record)

    updated = current.model_copy(update={"max_url_attached": payload.attached})
    if record is None:
        record = MaxProjectConfig(
            project_id=project.id,
            owner_id=current_user.id,
            config=updated.model_dump(mode="json"),
            config_version=1,
        )
        session.add(record)
    else:
        record.config = updated.model_dump(mode="json")
        record.config_version += 1
    await session.commit()
    await session.refresh(record)
    return _public(project, record)


@router.put("/{project_id}/max/config", response_model=MaxProjectConfigPublic)
async def put_max_config(
    project_id: UUID,
    payload: MaxProjectConfigPayload,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> MaxProjectConfigPublic:
    # Share the same per-project transaction lock as prompt reservation. This
    # prevents a no-code config commit and an LLM commit from branching off the
    # same parent and making one another disappear from the current timeline.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    project = await _owned_max_project(session, project_id, current_user.id, lock=True)
    active_generation = (
        await session.execute(
            select(GenerationRun.id).where(
                GenerationRun.project_id == project.id,
                GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if active_generation is not None:
        raise ApiError(
            "conflict",
            "Дождитесь завершения текущей сборки и сохраните настройки ещё раз",
            status.HTTP_409_CONFLICT,
        )
    try:
        deployment = await orchestrator_client.get_deploy(project.id)
    except Exception as exc:
        raise ApiError(
            "deployment_state_unavailable",
            "Не удалось безопасно проверить публикацию проекта. Повторите позже.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if deployment_is_active(deployment):
        raise ApiError(
            "conflict",
            "Дождитесь завершения публикации и сохраните настройки ещё раз",
            status.HTTP_409_CONFLICT,
        )
    if project.current_snapshot_id is None:
        raise ApiError("project_empty", "У проекта нет исходного снимка", status.HTTP_409_CONFLICT)
    current = await session.get(Snapshot, project.current_snapshot_id)
    if current is None:
        raise ApiError(
            "project_empty",
            "Текущий снимок проекта не найден",
            status.HTTP_409_CONFLICT,
        )

    config_data = payload.model_dump(mode="json")
    record = await session.get(MaxProjectConfig, project_id)
    if (
        record is not None
        and record.config == config_data
        and record.synced_snapshot_id == project.current_snapshot_id
        and record.managed_kit_version == MAX_MANAGED_KIT_VERSION
    ):
        if project.runtime_sync_required:
            try:
                synced = await reconcile_locked_runtime(
                    session,
                    project,
                    ensure_running=False,
                    full_tree=True,
                )
                if synced:
                    await session.commit()
            except Exception as sync_exc:
                log.error(
                    "max.config_noop_runtime_sync_pending",
                    project_id=str(project_id),
                    err=str(sync_exc),
                )
                raise ApiError(
                    "orchestrator_unavailable",
                    "Настройки сохранены; превью будет восстановлено перед следующим запуском.",
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                ) from sync_exc
        await _refresh_release_proof(session, project)
        return _public(project, record)

    current_files = await asyncio.to_thread(repo_svc.read_files, project.id, current.commit_sha)
    files = _max_config_sync_files(payload, project.id, current_files)
    commit_sha = await repo_svc.commit_files_async(
        project.id,
        files,
        "Update MAX business configuration",
        current.commit_sha,
    )
    snapshot = Snapshot(
        project_id=project.id,
        commit_sha=commit_sha,
        prompt_text=None,
        model_id=None,
        parent_id=current.id,
    )
    session.add(snapshot)
    await session.flush()
    project.current_snapshot_id = snapshot.id
    if record is None:
        record = MaxProjectConfig(
            project_id=project.id,
            owner_id=current_user.id,
            config=config_data,
            config_version=1,
            managed_kit_version=MAX_MANAGED_KIT_VERSION,
        )
        session.add(record)
    else:
        record.config = config_data
        record.config_version += 1
        record.managed_kit_version = MAX_MANAGED_KIT_VERSION
    record.synced_snapshot_id = snapshot.id
    mark_runtime_sync_required(project, files)

    # Canonical state and its durable runtime-sync guard commit atomically. The
    # live tree is touched only afterwards; therefore a failed/lost COMMIT ACK
    # can never require a best-effort reverse mutation.
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        try:
            canonical_snapshot_id = await current_snapshot_id_fresh(project.id)
        except Exception as state_exc:
            log.error(
                "max.config_commit_state_unknown",
                project_id=str(project.id),
                err=str(state_exc),
            )
            raise ApiError(
                "deployment_state_unavailable",
                "Не удалось подтвердить результат сохранения. Повторите позже.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        if canonical_snapshot_id != snapshot.id:
            raise ApiError(
                "conflict",
                "Проект уже изменился; обновите страницу перед сохранением.",
                status.HTTP_409_CONFLICT,
            ) from exc
        reloaded_project = await session.get(Project, project_id)
        record = await session.get(MaxProjectConfig, project_id)
        if reloaded_project is None or record is None:
            raise ApiError(
                "deployment_state_unavailable",
                "Настройки сохранены, но результат пока нельзя отобразить.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        project = reloaded_project
    try:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
            {"project_id": str(project_id)},
        )
        await session.refresh(project, with_for_update=True)
        if project.current_snapshot_id != snapshot.id:
            raise ApiError(
                "conflict",
                "Проект уже изменился; обновите страницу перед сохранением.",
                status.HTTP_409_CONFLICT,
            )
        synced = await reconcile_locked_runtime(
            session,
            project,
            ensure_running=False,
            full_tree=True,
        )
        if synced:
            await session.commit()
    except Exception as sync_exc:
        log.error(
            "max.config_runtime_sync_pending",
            project_id=str(project_id),
            err=str(sync_exc),
        )
        raise ApiError(
            "orchestrator_unavailable",
            "Настройки сохранены; превью будет восстановлено перед следующим запуском.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from sync_exc
    await session.refresh(record)

    # Proof refresh performs a full-tree hot reload. Reacquire the common lock
    # after the commit and recheck both long-running state machines so it cannot
    # overwrite a Google agent or a deploy that won the post-commit race.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    await session.refresh(project, with_for_update=True)
    active_generation = (
        await session.execute(
            select(GenerationRun.id).where(
                GenerationRun.project_id == project.id,
                GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if active_generation is not None:
        return _public(project, record)
    try:
        deployment = await orchestrator_client.get_deploy(project.id)
    except Exception:
        return _public(project, record)
    if deployment_is_active(deployment):
        return _public(project, record)
    await _refresh_release_proof(session, project)
    return _public(project, record)


@router.post("/{project_id}/max/sync-kit", response_model=MaxProjectConfigPublic)
async def sync_max_managed_kit(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> MaxProjectConfigPublic:
    """Apply the current model-free managed kit to an existing MAX project."""
    project = await _owned_max_project(session, project_id, current_user.id)
    record = await session.get(MaxProjectConfig, project_id)
    payload = (
        MaxProjectConfigPayload.model_validate(record.config)
        if record is not None
        else _default_config(project)
    )
    return await put_max_config(project_id, payload, session, current_user)


@router.get("/{project_id}/max/readiness", response_model=MaxReadinessPublic)
async def get_max_readiness(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> MaxReadinessPublic:
    project = await _owned_max_project(session, project_id, current_user.id)
    record = await session.get(MaxProjectConfig, project_id)
    config = MaxProjectConfigPayload.model_validate(record.config) if record else None
    integration = (
        await session.execute(select(MaxIntegration).where(MaxIntegration.project_id == project.id))
    ).scalar_one_or_none()
    generated_count = int(
        (
            await session.execute(
                select(func.count(Snapshot.id)).where(
                    Snapshot.project_id == project.id,
                    Snapshot.prompt_text.is_not(None),
                    func.length(func.trim(Snapshot.prompt_text)) > 0,
                )
            )
        ).scalar_one()
    )
    try:
        deployment = await orchestrator_client.get_deploy(project.id)
    except Exception:
        deployment = {}
    deployed_at: datetime | None = None
    finished_at = deployment.get("finished_at")
    if isinstance(finished_at, str):
        try:
            deployed_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        except ValueError:
            deployed_at = None
    current_snapshot = (
        await session.get(Snapshot, project.current_snapshot_id)
        if project.current_snapshot_id
        else None
    )
    published = bool(
        deployment.get("phase") == "done"
        and deployment.get("prod_url")
        and deployed_at
        and current_snapshot
        and deployed_at >= current_snapshot.created_at
    )
    configured = bool(
        config
        and config.app_name
        and config.summary
        and config.primary_action
        and config.operator.legal_name
        and config.support.email
    )
    items = [
        MaxReadinessItem(
            id="business",
            label="Бизнес-профиль и поддержка заполнены",
            done=configured,
            action="Заполнить настройки",
        ),
        MaxReadinessItem(
            id="legal",
            label="Юридические данные подтверждены",
            done=bool(config and config.legal.terms_accepted),
            action="Подтвердить данные",
        ),
        MaxReadinessItem(
            id="build",
            label="Рабочая версия приложения собрана",
            done=generated_count > 0,
            action="Завершить сборку",
        ),
        MaxReadinessItem(
            id="bot",
            label="MAX-бот проверен",
            done=bool(integration and integration.verified_at),
            action="Подключить бота",
        ),
        MaxReadinessItem(
            id="publish",
            label="Текущая версия доступна по HTTPS",
            done=published,
            action="Опубликовать",
        ),
        MaxReadinessItem(
            id="webhook",
            label="Webhook MAX активирован",
            done=bool(integration and integration.status == "active"),
            action="Активировать webhook",
        ),
        MaxReadinessItem(
            id="max_url",
            label="URL приложения привязан в кабинете MAX",
            done=bool(config and config.max_url_attached),
            action="Подтвердить привязку",
        ),
    ]
    done = sum(item.done for item in items)
    return MaxReadinessPublic(
        ready_to_launch=done == len(items),
        progress=round(done / len(items) * 100),
        items=items,
    )


_USAGE_STAGE_LABELS = {
    "template": "Проверенный MAX-шаблон",
    "native_agent": "Точечная AI-доработка",
    "build_plan": "План приложения",
    "verification": "Проверка сборки",
    "media": "Изображения и видео",
    "other": "Прочие AI-операции",
}
_USAGE_STAGE_ORDER = tuple(_USAGE_STAGE_LABELS)


@router.get("/{project_id}/max/usage", response_model=MaxUsagePublic)
async def get_max_usage(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> MaxUsagePublic:
    """Actual gateway ledger grouped into Studio-friendly generation stages."""
    await _owned_max_project(session, project_id, current_user.id)
    latest_run = (
        await session.execute(
            select(GenerationRun)
            .where(GenerationRun.project_id == project_id)
            .order_by(GenerationRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    ledger_rows = list(
        (
            await session.execute(
                select(Usage).where(Usage.project_id == project_id).order_by(Usage.created_at.asc())
            )
        ).scalars()
    )
    pending_rows = [
        row for row in ledger_rows if row.provider_request_id == "native-budget-reservation"
    ]
    rows = [row for row in ledger_rows if row.provider_request_id != "native-budget-reservation"]
    run_rows = [row for row in rows if latest_run is not None and row.run_id == latest_run.id]
    run_pending_rows = [
        row for row in pending_rows if latest_run is not None and row.run_id == latest_run.id
    ]
    visible_rows = run_rows if latest_run is not None else rows
    grouped: dict[str, dict[str, int | float]] = {}
    for row in visible_rows:
        stage = row.stage if row.stage in _USAGE_STAGE_LABELS else "other"
        bucket = grouped.setdefault(
            stage,
            {
                "cost_rub": 0.0,
                "calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "retries": 0,
            },
        )
        bucket["cost_rub"] = float(bucket["cost_rub"]) + float(row.cost_rub)
        bucket["calls"] = int(bucket["calls"]) + 1
        bucket["tokens_in"] = int(bucket["tokens_in"]) + row.tokens_in
        bucket["tokens_out"] = int(bucket["tokens_out"]) + row.tokens_out
        bucket["cache_read_tokens"] = int(bucket["cache_read_tokens"]) + row.cache_read_tokens
        bucket["cache_write_tokens"] = int(bucket["cache_write_tokens"]) + row.cache_write_tokens
        bucket["retries"] = int(bucket["retries"]) + row.retry_count

    # The deterministic base is intentionally visible even though it has no LLM
    # ledger row: users can see that this stage adds zero provider usage.
    grouped.setdefault(
        "template",
        {
            "cost_rub": 0.0,
            "calls": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "retries": 0,
        },
    )
    stages = [
        MaxUsageStagePublic(
            id=stage,
            label=_USAGE_STAGE_LABELS[stage],
            cost_rub=float(grouped[stage]["cost_rub"]),
            calls=int(grouped[stage]["calls"]),
            tokens_in=int(grouped[stage]["tokens_in"]),
            tokens_out=int(grouped[stage]["tokens_out"]),
            cache_read_tokens=int(grouped[stage]["cache_read_tokens"]),
            cache_write_tokens=int(grouped[stage]["cache_write_tokens"]),
            retries=int(grouped[stage]["retries"]),
        )
        for stage in _USAGE_STAGE_ORDER
        if stage in grouped
    ]
    return MaxUsagePublic(
        total_cost_rub=round(sum(float(row.cost_rub) for row in rows), 4),
        run_cost_rub=round(sum(float(row.cost_rub) for row in run_rows), 4),
        pending_reservation_rub=round(sum(float(row.cost_rub) for row in pending_rows), 4),
        run_pending_reservation_rub=round(sum(float(row.cost_rub) for row in run_pending_rows), 4),
        pending_reservation_calls=len(run_pending_rows if latest_run else pending_rows),
        run_id=latest_run.id if latest_run else None,
        run_status=latest_run.status if latest_run else None,
        stages=stages,
    )

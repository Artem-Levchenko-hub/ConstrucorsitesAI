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
    MaxLegal,
    MaxOperator,
    MaxPreviewSessionPublic,
    MaxPreviewSessionUpstream,
    MaxProjectConfigPayload,
    MaxProjectConfigPublic,
    MaxReadinessItem,
    MaxReadinessPublic,
    MaxSupport,
    MaxUrlAttachedPayload,
    MaxUsagePublic,
    MaxUsageStagePublic,
)
from omnia_api.services import orchestrator_client, project_cell_runtime
from omnia_api.services import repo as repo_svc
from omnia_api.services.deploy_attestation import ensure_current_release_proof
from omnia_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
from omnia_api.services.max_project_kit import (
    MAX_MANAGED_KIT_VERSION,
    render_max_managed_files,
)

router = APIRouter(prefix="/api/projects", tags=["max-studio"])
log = structlog.get_logger(__name__)


def _coerce_preview_expiry(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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
    return MaxProjectConfigPayload(
        app_name=project.name,
        app_type="custom",
        summary="Мини-приложение для пользователей MAX",
        operator=MaxOperator(),
        support=MaxSupport(),
        legal=MaxLegal(),
    )


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
    result = _public(project, record)
    selection = await project_cell_runtime.resolve_project_cell_public_selection(
        session, project, owner=current_user,
    )
    if selection.selected:
        result.application_mode = "runtime"
    return result


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
    cell_preview = await project_cell_runtime.create_project_cell_preview_session(
        session,
        project,
        owner=current_user,
    )
    response.headers["Cache-Control"] = "no-store"
    if cell_preview is not None:
        return MaxPreviewSessionPublic(
            url=cell_preview.bootstrap_url,
            expires_at=_coerce_preview_expiry(cell_preview.expires_at),
        )
    payload = await orchestrator_client.create_max_preview_session(project.id)
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
    # Same project serialization as generation, without waiting behind a cold
    # preview start until the request/DB timeout turns a busy state into a 500.
    await _owned_max_project(session, project_id, current_user.id)
    await project_cell_runtime._try_preview_project_lock(session, project_id)
    project = await _owned_max_project(session, project_id, current_user.id, lock=True)
    cell_selection = await project_cell_runtime.resolve_project_cell_public_selection(
        session,
        project,
        owner=current_user,
        populate_existing=True,
    )
    if cell_selection.selected:
        if cell_selection.workspace is not None:
            workspace = cell_selection.workspace
            if (
                workspace.project_id != project.id
                or workspace.owner_id != project.owner_id
                or workspace.owner_id != current_user.id
            ):
                raise ApiError("conflict", "Project Cell workspace identity mismatch", 409)
        return await _save_cell_business_config(
            project, payload, session, current_user, cell_selection,
        )
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
    except Exception:
        deployment = {}
    if deployment.get("phase") in {"building", "pushing", "swapping", "cancelling"}:
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
        await _refresh_release_proof(session, project)
        return _public(project, record)

    files = render_max_managed_files(payload, project.id)
    commit_sha = await asyncio.to_thread(
        repo_svc.commit_files,
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
    await session.commit()
    await session.refresh(record)

    # A stopped runtime will receive the canonical snapshot on the next start.
    # A live runtime can be updated immediately; a preview outage must not roll
    # back the safely persisted business configuration.
    try:
        runtime = await orchestrator_client.get_status(project.id)
        if runtime.get("state") == "running":
            await orchestrator_client.hot_reload(project.id, project.slug, files)
    except Exception:
        log.warning("max_config_live_sync_failed", project_id=str(project.id), exc_info=True)
    await _refresh_release_proof(session, project)
    return _public(project, record)


async def _save_cell_business_config(
    project: Project,
    payload: MaxProjectConfigPayload,
    session: SessionDep,
    current_user: CurrentUserDep,
    selection: project_cell_runtime.ProjectCellPublicSelection,
) -> MaxProjectConfigPublic:
    """Metadata has its own version, not a generation or source/build snapshot."""
    if await project_cell_runtime._active_generation(session, project.id) is not None:
        raise ApiError("conflict", "Дождитесь завершения сборки и сохраните данные ещё раз", 409)
    config_data = payload.model_dump(mode="json")
    record = await session.get(MaxProjectConfig, project.id)
    if record is not None and record.owner_id != current_user.id:
        raise ApiError("conflict", "MAX configuration ownership mismatch", 409)
    if record is None:
        record = MaxProjectConfig(
            project_id=project.id, owner_id=current_user.id, config=config_data,
            config_version=1, managed_kit_version=MAX_MANAGED_KIT_VERSION,
        )
        session.add(record)
    elif record.config != config_data:
        record.config = config_data
        record.config_version += 1
        record.synced_snapshot_id = None
    version = record.config_version
    record.synced_snapshot_id = None
    # A preview outage cannot erase entered data. An unconfirmed application
    # keeps synced_snapshot_id empty, and the next save reuses the same version.
    await session.commit()
    await session.refresh(record)
    if selection.workspace is not None and project.current_snapshot_id is not None:
        try:
            await orchestrator_client.configure_published_cell(project.id, {
                "owner_id": str(current_user.id),
                "business_config": payload.model_dump(mode="json", exclude={"max_url_attached"}),
                "business_config_version": version,
            })
            await project_cell_runtime.start_project_cell_runtime(
                session, project, owner=current_user,
            )
            await project_cell_runtime._try_preview_project_lock(session, project.id)
            await session.refresh(record)
            if record.config_version != version or record.config != config_data:
                raise ApiError("conflict", "Данные изменены в другом окне. Обновите вкладку", 409)
            if await project_cell_runtime._active_generation(session, project.id) is not None:
                raise ApiError("conflict", "Данные сохранены. Примените их после сборки", 409)
            applied = await orchestrator_client.project_cell_apply_business_config(
                selection.workspace.id, project_id=project.id, owner_id=current_user.id,
                version=version,
                config=payload.model_dump(mode="json", exclude={"max_url_attached"}),
            )
            if not applied:
                raise ApiError("orchestrator_unavailable", "Применение данных не подтверждено", 503)
        except Exception as exc:
            if isinstance(exc, ApiError) and exc.status_code < 500:
                raise
            log.warning("max_config_cell_sync_failed", project_id=str(project.id), version=version)
            raise ApiError(
                "orchestrator_unavailable",
                "Данные сохранены на сервере, но ещё не применены. "
                "Повторите сохранение — генерация не нужна.",
                503,
            ) from exc
        record.synced_snapshot_id = project.current_snapshot_id
        await session.commit()
        await session.refresh(record)
    result = _public(project, record)
    result.application_mode = "runtime"
    return result


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
    selection = await project_cell_runtime.resolve_project_cell_public_selection(
        session,
        project,
        owner=current_user,
    )
    if selection.selected and record is None:
        return _public(project, record)
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
    build_ready = generated_count > 0
    selection = await project_cell_runtime.resolve_project_cell_public_selection(
        session, project, owner=current_user,
    )
    if selection.selected:
        from omnia_api.services.cell_publication import load_publication_evidence

        build_ready = False
        if selection.workspace is not None:
            try:
                await load_publication_evidence(session, project, selection.workspace)
                build_ready = True
            except ApiError:
                pass
        # Timestamps do not prove WHICH version reached the public runtime.
        published = bool(
            deployment.get("phase") == "done"
            and deployment.get("prod_url")
            and current_snapshot
            and deployment.get("snapshot_id") == str(current_snapshot.id)
            and deployment.get("commit_sha") == current_snapshot.commit_sha
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
            label="Данные приложения и поддержка заполнены",
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
            done=build_ready,
            action="Завершить сборку",
        ),
        MaxReadinessItem(
            id="bot",
            label="Безопасный доступ MAX подключён",
            done=bool(integration and integration.verified_at),
            action="Подключить безопасный вход",
        ),
        MaxReadinessItem(
            id="publish",
            label="Текущая версия доступна по HTTPS",
            done=published,
            action="Опубликовать",
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
    rows = list(
        (
            await session.execute(
                select(Usage).where(Usage.project_id == project_id).order_by(Usage.created_at.asc())
            )
        ).scalars()
    )
    run_rows = [row for row in rows if latest_run is not None and row.run_id == latest_run.id]
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
        run_id=latest_run.id if latest_run else None,
        run_status=latest_run.status if latest_run else None,
        stages=stages,
    )

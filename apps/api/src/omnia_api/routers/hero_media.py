from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from omnia_api.core.config import get_settings
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.core.redis import publish_event
from omnia_api.models.hero_media_asset import HeroMediaAsset
from omnia_api.models.hero_media_brief import HeroMediaBrief
from omnia_api.models.hero_media_render import HeroMediaRender
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.routers.public import _INDEX_CANDIDATES
from omnia_api.schemas.hero_media import (
    HeroMediaAssetPublic,
    HeroMediaDecision,
    HeroMediaPlanApproveRequest,
    HeroMediaPlanCreateRequest,
    HeroMediaPlanPublic,
    HeroMediaRenderCreateRequest,
    HeroMediaRenderPublic,
)
from omnia_api.schemas.snapshot import SnapshotPublic
from omnia_api.services import repo as repo_svc
from omnia_api.services.hero_media_assembler import render_preview_document
from omnia_api.services.hero_media_planner import plan_hero_media
from omnia_api.services.queue import enqueue_hero_media_render, enqueue_preview
from omnia_api.services.user_uploads import UploadRejected, sanitize_and_upload_record

router = APIRouter(prefix="/api/projects", tags=["hero-media"])

_MAX_UPLOAD_BYTES = 6 * 1024 * 1024
_STATIC_TEMPLATES = {"blank", "landing", "portfolio", "blog"}
_HERO_BLOCK_RE = re.compile(
    r"<!-- OMNIA_HERO_MEDIA_START -->.*?<!-- OMNIA_HERO_MEDIA_END -->",
    re.DOTALL,
)
_BODY_OPEN_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def _require_feature() -> None:
    if not get_settings().use_hero_media_mvp:
        raise ApiError(
            "feature_disabled",
            "Hero media MVP is disabled in this environment.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )


async def _owned_project(session: SessionDep, project_id: UUID, user_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


async def _owned_plan(
    session: SessionDep,
    project_id: UUID,
    plan_id: UUID,
    user_id: UUID,
) -> HeroMediaBrief:
    plan = await session.get(HeroMediaBrief, plan_id)
    if plan is None or plan.project_id != project_id or plan.owner_id != user_id:
        raise ApiError(
            "hero_media_plan_not_found",
            "hero media plan not found",
            status.HTTP_404_NOT_FOUND,
        )
    return plan


async def _owned_render(
    session: SessionDep,
    project_id: UUID,
    render_id: UUID,
    user_id: UUID,
) -> HeroMediaRender:
    render = await session.get(HeroMediaRender, render_id)
    if render is None or render.project_id != project_id or render.owner_id != user_id:
        raise ApiError(
            "hero_media_render_not_found",
            "hero media render not found",
            status.HTTP_404_NOT_FOUND,
        )
    return render


def _asset_public(asset: HeroMediaAsset) -> HeroMediaAssetPublic:
    return HeroMediaAssetPublic.model_validate(asset)


def _plan_public(plan: HeroMediaBrief) -> HeroMediaPlanPublic:
    return HeroMediaPlanPublic(
        id=plan.id,
        project_id=plan.project_id,
        owner_id=plan.owner_id,
        status=plan.status,
        input_prompt=plan.input_prompt,
        business_type=plan.business_type,
        style_preference=plan.style_preference,
        focus_preference=plan.focus_preference,
        motion_preference=plan.motion_preference,
        asset_ids=[UUID(value) for value in plan.asset_ids],
        recommended_plan_kind=plan.recommended_plan_kind,
        selected_plan_kind=plan.selected_plan_kind,
        plan=HeroMediaDecision.model_validate(plan.plan),
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _render_public(render: HeroMediaRender) -> HeroMediaRenderPublic:
    return HeroMediaRenderPublic(
        id=render.id,
        project_id=render.project_id,
        owner_id=render.owner_id,
        brief_id=render.brief_id,
        status=render.status,
        media_plan=render.media_plan,
        status_detail=render.status_detail,
        provider_summary=render.provider_summary,
        poster_asset_id=render.poster_asset_id,
        video_asset_id=render.video_asset_id,
        applied_snapshot_id=render.applied_snapshot_id,
        bundle=render.bundle,
        progress_log=render.progress_log or [],
        error=render.error,
        retry_count=render.retry_count,
        created_at=render.created_at,
        started_at=render.started_at,
        finished_at=render.finished_at,
        applied_at=render.applied_at,
    )


def _snapshot_public(snapshot: Snapshot) -> SnapshotPublic:
    return SnapshotPublic.model_validate(
        {
            "id": snapshot.id,
            "project_id": snapshot.project_id,
            "commit_sha": snapshot.commit_sha,
            "prompt_text": snapshot.prompt_text,
            "model_id": snapshot.model_id,
            "parent_id": snapshot.parent_id,
            "preview_url": preview_public_url(snapshot.preview_key),
            "is_rollback_target": snapshot.is_rollback_target,
            "created_at": snapshot.created_at,
        }
    )


def _render_block(render: HeroMediaRenderPublic) -> str:
    bundle = render.bundle
    if bundle is None:
        raise ApiError(
            "hero_media_invalid_state",
            "Hero bundle is not ready yet.",
            status.HTTP_409_CONFLICT,
        )
    return (
        "<!-- OMNIA_HERO_MEDIA_START -->\n"
        f'<style id="omnia-hero-media-style">{bundle.css}</style>\n'
        f"{bundle.html}\n"
        f'<script id="omnia-hero-media-script">{bundle.js}</script>\n'
        "<!-- OMNIA_HERO_MEDIA_END -->"
    )


def _apply_hero_block(html: str, block: str) -> str:
    # A previous MVP inserted inside the first <main>, inheriting its max-width
    # and utility layout. Always remove the old block and reinsert as the first
    # body child so the namespaced hero owns a predictable viewport.
    cleaned = _HERO_BLOCK_RE.sub("", html, count=1)
    body_match = _BODY_OPEN_RE.search(cleaned)
    if body_match:
        body_tag = body_match.group(0)
        if "data-omnia-hero-media" not in body_tag.lower():
            marked_body = body_tag[:-1] + ' data-omnia-hero-media="true">'
            cleaned = cleaned[: body_match.start()] + marked_body + cleaned[body_match.end() :]
            insert_at = body_match.start() + len(marked_body)
        else:
            insert_at = body_match.end()
        return cleaned[:insert_at] + "\n" + block + cleaned[insert_at:]
    return block + "\n" + cleaned


def _prepare_render_retry(render: HeroMediaRender) -> None:
    render.status = "queued"
    render.status_detail = "Повторно поставлено в очередь"
    render.provider_summary = None
    render.error = None
    render.bundle = None
    render.poster_asset_id = None
    render.video_asset_id = None
    render.applied_snapshot_id = None
    render.applied_at = None
    render.started_at = None
    render.finished_at = None
    render.retry_count += 1
    log = list(render.progress_log or [])
    log.append(
        {
            "at": datetime.now(UTC).isoformat(),
            "status": "queued",
            "detail": "Повторно поставлено в очередь",
        }
    )
    render.progress_log = log


@router.post("/{project_id}/hero-media/assets", response_model=HeroMediaAssetPublic)
async def upload_hero_media_asset(
    project_id: UUID,
    request: Request,
    session: SessionDep,
    current_user: CurrentUserDep,
    consent_confirmed: bool = Query(default=False),
    filename: str | None = Query(default=None, max_length=240),
) -> HeroMediaAssetPublic:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    if not consent_confirmed:
        raise ApiError(
            "validation_failed",
            "Confirm that you have the rights to use these photos before uploading.",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    raw = await request.body()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise ApiError(
            "validation_failed",
            "Image is too large (max 6 MB).",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    try:
        uploaded = await asyncio.to_thread(sanitize_and_upload_record, raw, str(project_id))
    except UploadRejected as exc:
        raise ApiError("validation_failed", str(exc), status.HTTP_400_BAD_REQUEST) from exc
    except Exception as exc:
        raise ApiError(
            "internal_error",
            "Could not store the uploaded image.",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc

    asset = HeroMediaAsset(
        project_id=project_id,
        owner_id=current_user.id,
        asset_role="source",
        media_kind="image",
        storage_url=uploaded.url,
        storage_key=uploaded.storage_key,
        mime_type=uploaded.mime_type,
        original_filename=filename,
        width=uploaded.width,
        height=uploaded.height,
        duration_ms=None,
        bytes_size=uploaded.bytes_size,
        consent_confirmed=True,
        moderation_status="unreviewed",
        details={"uploaded_for": "hero-media"},
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return _asset_public(asset)


@router.get("/{project_id}/hero-media/assets", response_model=list[HeroMediaAssetPublic])
async def list_hero_media_assets(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> list[HeroMediaAssetPublic]:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    result = await session.execute(
        select(HeroMediaAsset)
        .where(
            HeroMediaAsset.project_id == project_id,
            HeroMediaAsset.owner_id == current_user.id,
        )
        .order_by(HeroMediaAsset.created_at.desc())
    )
    return [_asset_public(asset) for asset in result.scalars()]


@router.post("/{project_id}/hero-media/plans", response_model=HeroMediaPlanPublic)
async def create_hero_media_plan(
    project_id: UUID,
    payload: HeroMediaPlanCreateRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> HeroMediaPlanPublic:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    asset_ids = [
        str(asset_id) for asset_id in payload.asset_ids[: get_settings().hero_media_max_assets]
    ]
    assets: list[HeroMediaAsset] = []
    if asset_ids:
        result = await session.execute(
            select(HeroMediaAsset).where(
                HeroMediaAsset.project_id == project_id,
                HeroMediaAsset.owner_id == current_user.id,
                HeroMediaAsset.asset_role == "source",
                HeroMediaAsset.id.in_(payload.asset_ids),
            )
        )
        rows = list(result.scalars())
        row_by_id = {str(row.id): row for row in rows}
        missing = [asset_id for asset_id in asset_ids if asset_id not in row_by_id]
        if missing:
            raise ApiError(
                "hero_media_asset_not_found",
                "One or more uploaded photos were not found for this project.",
                status.HTTP_404_NOT_FOUND,
                details={"missing_asset_ids": missing},
            )
        assets = [row_by_id[asset_id] for asset_id in asset_ids]

    decision = await plan_hero_media(
        owner_id=current_user.id,
        project_id=project_id,
        prompt=payload.prompt,
        business_type=payload.business_type,
        style_preference=payload.style_preference,
        focus_preference=payload.focus_preference,
        motion_preference=payload.motion_preference,
        asset_urls=[asset.storage_url for asset in assets],
    )
    plan = HeroMediaBrief(
        project_id=project_id,
        owner_id=current_user.id,
        status="draft",
        input_prompt=payload.prompt,
        business_type=payload.business_type,
        style_preference=payload.style_preference,
        focus_preference=payload.focus_preference,
        motion_preference=payload.motion_preference,
        asset_ids=asset_ids,
        recommended_plan_kind=decision.plan_kind,
        selected_plan_kind=None,
        plan=decision.model_dump(),
    )
    session.add(plan)
    await session.commit()
    await session.refresh(plan)
    return _plan_public(plan)


@router.get("/{project_id}/hero-media/plans/{plan_id}", response_model=HeroMediaPlanPublic)
async def get_hero_media_plan(
    project_id: UUID,
    plan_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> HeroMediaPlanPublic:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    plan = await _owned_plan(session, project_id, plan_id, current_user.id)
    return _plan_public(plan)


@router.get("/{project_id}/hero-media/plans", response_model=list[HeroMediaPlanPublic])
async def list_hero_media_plans(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[HeroMediaPlanPublic]:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    result = await session.execute(
        select(HeroMediaBrief)
        .where(
            HeroMediaBrief.project_id == project_id,
            HeroMediaBrief.owner_id == current_user.id,
        )
        .order_by(HeroMediaBrief.created_at.desc())
        .limit(limit)
    )
    return [_plan_public(plan) for plan in result.scalars()]


@router.post("/{project_id}/hero-media/plans/{plan_id}/approve", response_model=HeroMediaPlanPublic)
async def approve_hero_media_plan(
    project_id: UUID,
    plan_id: UUID,
    payload: HeroMediaPlanApproveRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> HeroMediaPlanPublic:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    plan = await _owned_plan(session, project_id, plan_id, current_user.id)
    plan.status = "approved"
    plan.selected_plan_kind = payload.selected_plan_kind
    plan.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(plan)
    return _plan_public(plan)


@router.post("/{project_id}/hero-media/renders", response_model=HeroMediaRenderPublic)
async def create_hero_media_render(
    project_id: UUID,
    payload: HeroMediaRenderCreateRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> HeroMediaRenderPublic:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    plan = await _owned_plan(session, project_id, payload.plan_id, current_user.id)
    if plan.status != "approved":
        raise ApiError(
            "hero_media_invalid_state",
            "Approve the recommended media plan before starting a render.",
            status.HTTP_409_CONFLICT,
        )
    media_plan = plan.selected_plan_kind or plan.recommended_plan_kind
    render = HeroMediaRender(
        project_id=project_id,
        owner_id=current_user.id,
        brief_id=plan.id,
        status="queued",
        media_plan=media_plan,
        status_detail="План принят, задача поставлена в очередь",
        provider_summary=None,
        poster_asset_id=None,
        video_asset_id=None,
        bundle=None,
        error=None,
    )
    session.add(render)
    await session.commit()
    await session.refresh(render)
    enqueue_hero_media_render(render.id)
    return _render_public(render)


@router.get("/{project_id}/hero-media/renders/{render_id}", response_model=HeroMediaRenderPublic)
async def get_hero_media_render(
    project_id: UUID,
    render_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> HeroMediaRenderPublic:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    render = await _owned_render(session, project_id, render_id, current_user.id)
    return _render_public(render)


@router.get("/{project_id}/hero-media/renders", response_model=list[HeroMediaRenderPublic])
async def list_hero_media_renders(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[HeroMediaRenderPublic]:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    result = await session.execute(
        select(HeroMediaRender)
        .where(
            HeroMediaRender.project_id == project_id,
            HeroMediaRender.owner_id == current_user.id,
        )
        .order_by(HeroMediaRender.created_at.desc())
        .limit(limit)
    )
    return [_render_public(render) for render in result.scalars()]


@router.post(
    "/{project_id}/hero-media/renders/{render_id}/retry",
    response_model=HeroMediaRenderPublic,
)
async def retry_hero_media_render(
    project_id: UUID,
    render_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> HeroMediaRenderPublic:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    render = await _owned_render(session, project_id, render_id, current_user.id)
    if render.status not in {"failed", "completed"}:
        raise ApiError(
            "hero_media_invalid_state",
            "Render can be retried only after it failed or completed.",
            status.HTTP_409_CONFLICT,
        )
    _prepare_render_retry(render)
    await session.commit()
    await session.refresh(render)
    enqueue_hero_media_render(render.id)
    return _render_public(render)


@router.get(
    "/{project_id}/hero-media/renders/{render_id}/preview",
    response_class=HTMLResponse,
)
async def preview_hero_media_render(
    project_id: UUID,
    render_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> HTMLResponse:
    _require_feature()
    await _owned_project(session, project_id, current_user.id)
    render = await _owned_render(session, project_id, render_id, current_user.id)
    if render.status != "completed" or not render.bundle:
        raise ApiError(
            "hero_media_invalid_state",
            "Hero preview is not ready yet.",
            status.HTTP_409_CONFLICT,
        )
    bundle = _render_public(render).bundle
    assert bundle is not None
    return HTMLResponse(render_preview_document(bundle))


@router.post(
    "/{project_id}/hero-media/renders/{render_id}/apply",
    response_model=SnapshotPublic,
)
async def apply_hero_media_render(
    project_id: UUID,
    render_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotPublic:
    _require_feature()
    project = await _owned_project(session, project_id, current_user.id)
    render = await _owned_render(session, project_id, render_id, current_user.id)
    if project.template not in _STATIC_TEMPLATES:
        raise ApiError(
            "hero_media_invalid_state",
            "Hero apply is supported only for static website templates in this MVP.",
            status.HTTP_409_CONFLICT,
        )
    if render.status != "completed" or not render.bundle:
        raise ApiError(
            "hero_media_invalid_state",
            "Hero render is not ready yet.",
            status.HTTP_409_CONFLICT,
        )
    if project.current_snapshot_id is None:
        raise ApiError(
            "no_snapshot",
            "Project has no snapshot to update.",
            status.HTTP_400_BAD_REQUEST,
        )
    current = await session.get(Snapshot, project.current_snapshot_id)
    if current is None:
        raise ApiError(
            "no_snapshot",
            "Current snapshot is missing.",
            status.HTTP_400_BAD_REQUEST,
        )
    files = await asyncio.to_thread(repo_svc.read_files, project_id, current.commit_sha)
    index_path = next((c for c in _INDEX_CANDIDATES if c in files), None)
    if index_path is None:
        raise ApiError(
            "no_index",
            "This project has no editable static index.html.",
            status.HTTP_400_BAD_REQUEST,
        )
    new_html = _apply_hero_block(files[index_path], _render_block(_render_public(render)))
    new_sha = await asyncio.to_thread(
        repo_svc.commit_files,
        project_id,
        {index_path: new_html},
        "hero-media: apply generated hero",
        current.commit_sha,
    )
    snapshot = Snapshot(
        project_id=project_id,
        commit_sha=new_sha,
        prompt_text="(hero media)",
        model_id=None,
        parent_id=project.current_snapshot_id,
    )
    session.add(snapshot)
    await session.flush()
    project.current_snapshot_id = snapshot.id
    render.applied_snapshot_id = snapshot.id
    render.applied_at = datetime.now(UTC)
    render.progress_log = [
        *(render.progress_log or []),
        {
            "at": render.applied_at.isoformat(),
            "status": "completed",
            "detail": "Hero применён в текущий static snapshot",
        },
    ]
    await session.commit()
    await session.refresh(snapshot)
    await asyncio.to_thread(enqueue_preview, snapshot.id)
    await publish_event(
        project_id,
        "snapshot.created",
        {"snapshot": _snapshot_public(snapshot).model_dump()},
    )
    return _snapshot_public(snapshot)

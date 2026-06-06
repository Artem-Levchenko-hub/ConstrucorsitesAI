import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, status
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.core.redis import publish_event
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.project import ProjectCreate, ProjectPublic, ProjectUpdate
from omnia_api.services import repo as repo_svc
from omnia_api.services.preset_classifier import classify_preset_sync
from omnia_api.services.queue import enqueue_preview

_UNTITLED_NAMES = frozenset({"untitled", "новый проект", "проект", "new project"})

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectPublic, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> Project:
    short_id = uuid4().hex[:6]
    base_slug = slugify(payload.name)[:60] or "project"
    slug = f"{base_slug}-{short_id}"

    # Auto-classify design preset from project name if informative.
    # Heuristic-only (sync, no LLM) on hot path — if name is generic ("Untitled")
    # or too short, leave NULL; classifier in routers/messages.py will
    # fill it on the first prompt via Haiku-fallback.
    preset_id: str | None = None
    name_stripped = payload.name.strip()
    if len(name_stripped) > 5 and name_stripped.lower() not in _UNTITLED_NAMES:
        preset_id = classify_preset_sync(
            project_name=name_stripped,
            template=payload.template,
            first_prompt=None,
        ) or None

    project = Project(
        owner_id=current_user.id,
        name=payload.name,
        slug=slug,
        template=payload.template,
        design_preset_id=preset_id,
    )
    session.add(project)
    await session.flush()

    template_dir = TEMPLATES_DIR / payload.template
    commit_sha = await asyncio.to_thread(
        repo_svc.init_repo, project.id, template_dir, payload.template
    )

    snapshot = Snapshot(
        project_id=project.id,
        commit_sha=commit_sha,
        prompt_text=None,
        model_id=None,
        parent_id=None,
    )
    session.add(snapshot)
    await session.flush()

    project.current_snapshot_id = snapshot.id

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise ApiError(
            "conflict", "slug already exists", status.HTTP_409_CONFLICT
        ) from e

    await session.refresh(project)
    await session.refresh(snapshot)

    await asyncio.to_thread(enqueue_preview, snapshot.id)
    await publish_event(
        project.id,
        "snapshot.created",
        {
            "snapshot": {
                "id": str(snapshot.id),
                "project_id": str(snapshot.project_id),
                "commit_sha": snapshot.commit_sha,
                "prompt_text": snapshot.prompt_text,
                "model_id": snapshot.model_id,
                "parent_id": str(snapshot.parent_id) if snapshot.parent_id else None,
                "preview_url": preview_public_url(snapshot.preview_key),
                "is_rollback_target": snapshot.is_rollback_target,
                "created_at": snapshot.created_at.isoformat(),
            }
        },
    )

    return project


@router.get("", response_model=list[ProjectPublic])
async def list_projects(
    session: SessionDep, current_user: CurrentUserDep
) -> list[Project]:
    res = await session.execute(
        select(Project)
        .where(Project.owner_id == current_user.id)
        .order_by(Project.created_at.desc())
    )
    projects = list(res.scalars().all())

    # Attach each project's current-snapshot thumbnail in ONE batch query
    # (not N+1) so the projects grid can show a mini preview per card.
    snap_ids = [p.current_snapshot_id for p in projects if p.current_snapshot_id]
    previews: dict[UUID, str | None] = {}
    if snap_ids:
        rows = await session.execute(
            select(Snapshot.id, Snapshot.preview_key).where(Snapshot.id.in_(snap_ids))
        )
        previews = {sid: preview_public_url(key) for sid, key in rows.all()}
    for p in projects:
        p.preview_url = previews.get(p.current_snapshot_id)
    return projects


@router.get("/{project_id}", response_model=ProjectPublic)
async def get_project(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.current_snapshot_id:
        snap = await session.get(Snapshot, project.current_snapshot_id)
        project.preview_url = preview_public_url(snap.preview_key) if snap else None
    return project


@router.patch("/{project_id}", response_model=ProjectPublic)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if payload.image_gen_enabled is not None:
        project.image_gen_enabled = payload.image_gen_enabled
    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> None:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    await session.delete(project)
    await session.commit()

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from yleum_api.core.deps import (
    CurrentUserDep,
    OptionalUserDep,
    SessionDep,
)
from yleum_api.core.errors import ApiError
from yleum_api.core.minio import preview_public_url
from yleum_api.core.redis import publish_event
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.project import (
    ProjectCreate,
    ProjectPublic,
    ProjectUpdate,
)
from yleum_api.schemas.snapshot import snapshot_event_dict
from yleum_api.services import repo as repo_svc
from yleum_api.services.entitlements import assert_can_create_project
from yleum_api.services.max_access import require_max_studio_access
from yleum_api.services.preset_classifier import classify_preset_sync
from yleum_api.services.project_cell_access import admit_new_project_cell
from yleum_api.services.project_deletion import request_project_deletion

_UNTITLED_NAMES = frozenset({"untitled", "новый проект", "проект", "new project"})

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def _commit_first_snapshot(session: SessionDep, project: Project, commit_sha: str) -> Project:
    """Commit the flushed project together with its first snapshot, then announce it."""
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
        raise ApiError("conflict", "slug already exists", status.HTTP_409_CONFLICT) from e

    await session.refresh(project)
    await session.refresh(snapshot)

    await publish_event(
        project.id,
        "snapshot.created",
        {"snapshot": snapshot_event_dict(snapshot)},
    )

    return project


@router.post("", response_model=ProjectPublic, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: SessionDep,
    current_user: OptionalUserDep,
) -> Project:
    if current_user is None:
        raise ApiError(
            "max_registration_required",
            "Для Yleum нужна регистрация",
            status.HTTP_403_FORBIDDEN,
        )
    require_max_studio_access(current_user)
    # Plan limit on the number of apps (402 `entitlement_exceeded` when full).
    await assert_can_create_project(session, current_user.id)
    owner = current_user
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
        preset_id = (
            classify_preset_sync(
                project_name=name_stripped,
                template=payload.template,
                first_prompt=None,
            )
            or None
        )

    # A project that cannot get its own cell has nowhere to be built: the legacy
    # dev-container runtime left with the site builder. Refuse at creation rather
    # than hand the owner a project that will fail on the first prompt.
    if not admit_new_project_cell(owner):
        raise ApiError(
            "runtime_unavailable",
            "Сейчас нельзя создать приложение: среда приложений недоступна. "
            "Попробуйте позже или напишите в поддержку.",
            status.HTTP_409_CONFLICT,
        )
    project = Project(
        owner_id=owner.id,
        name=payload.name,
        slug=slug,
        template=payload.template,
        design_preset_id=preset_id,
        project_cell_enabled=True,
    )
    session.add(project)
    await session.flush()

    template_dir = TEMPLATES_DIR / payload.template
    commit_sha = await asyncio.to_thread(
        repo_svc.init_repo, project.id, template_dir, payload.template
    )

    return await _commit_first_snapshot(session, project, commit_sha)


@router.get("", response_model=list[ProjectPublic])
async def list_projects(
    session: SessionDep, current_user: CurrentUserDep
) -> list[ProjectPublic]:
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
    public_projects: list[ProjectPublic] = []
    for project in projects:
        preview = (
            previews.get(project.current_snapshot_id)
            if project.current_snapshot_id is not None
            else None
        )
        public_projects.append(
            ProjectPublic.model_validate(project).model_copy(
                update={"preview_url": preview}
            )
        )
    return public_projects


@router.get("/{project_id}", response_model=ProjectPublic)
async def get_project(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> ProjectPublic:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    preview_url: str | None = None
    if project.current_snapshot_id:
        snap = await session.get(Snapshot, project.current_snapshot_id)
        preview_url = preview_public_url(snap.preview_key) if snap else None
    return ProjectPublic.model_validate(project).model_copy(update={"preview_url": preview_url})


@router.get("/{project_id}/download")
async def download_project(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> StreamingResponse:
    """User code/archive exports are unavailable on every plan and role."""
    raise ApiError(
        "forbidden", "Скачивание и экспорт исходного кода недоступны.", status.HTTP_403_FORBIDDEN
    )


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


@router.delete(
    "/{project_id}", status_code=status.HTTP_204_NO_CONTENT,
    responses={202: {
        "description": "Deletion accepted; the server continues until GET returns 404",
    }},
)
async def delete_project(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> Response:
    """Accept durable Cell deletion; report completion only after full cleanup."""
    if await request_project_deletion(session, project_id, current_user.id):
        return JSONResponse(
            {"state": "deleting", "project_id": str(project_id)},
            status_code=status.HTTP_202_ACCEPTED,
            headers={"Retry-After": "2", "Cache-Control": "no-store"},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

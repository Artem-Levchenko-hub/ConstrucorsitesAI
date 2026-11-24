from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.project import Project
from yleum_api.models.project_version import ProjectVersion
from yleum_api.schemas.project_version import (
    PreviewStatus,
    ProjectVersionPublic,
    ProjectVersionsPage,
    VersionPreview,
    VersionStatus,
)
from yleum_api.services.project_versions import (
    current_accepted_version,
    resolve_version,
    version_preview_fields,
)

router = APIRouter(prefix="/api/projects", tags=["versions"])


@router.get("/{project_id}/versions", response_model=ProjectVersionsPage)
async def list_project_versions(
    project_id: UUID,
    response: Response,
    session: SessionDep,
    current_user: CurrentUserDep,
    before: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ProjectVersionsPage:
    project = await session.get(Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", 404)
    response.headers["Cache-Control"] = "no-store"
    query = select(ProjectVersion).where(ProjectVersion.project_id == project_id)
    page_query = query.where(ProjectVersion.number < before) if before is not None else query
    page = (
        await session.scalars(page_query.order_by(ProjectVersion.number.desc()).limit(limit + 1))
    ).all()
    candidate = await current_accepted_version(session, project)
    current_id = candidate.id if candidate else None
    result = []
    for version in page[:limit]:
        state, snapshot = await resolve_version(session, version)
        preview_status, previews = version_preview_fields(state, snapshot)
        result.append(
            ProjectVersionPublic(
                id=version.id,
                number=version.number,
                project_id=project_id,
                source_message_id=version.source_message_id,
                generation_run_id=version.generation_run_id,
                snapshot_id=snapshot.id if snapshot else None,
                commit_sha=snapshot.commit_sha if snapshot else version.commit_sha,
                prompt_text=version.prompt_text,
                model_id=version.model_id,
                created_at=version.created_at,
                status=cast(VersionStatus, state),
                preview_status=cast(PreviewStatus, preview_status),
                previews=[VersionPreview.model_validate(item) for item in previews],
                is_current=version.id == current_id,
                can_restore=(
                    project.template != "max_miniapp"
                    and snapshot is not None
                    and state in {"ready", "unchanged"}
                ),
            )
        )
    return ProjectVersionsPage(
        versions=result, next_cursor=page[limit - 1].number if len(page) > limit else None
    )

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import Text, func, literal, or_, select
from sqlalchemy import cast as sql_cast

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_version import ProjectVersion
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.project_version import (
    PreviewStatus,
    ProjectVersionPublic,
    ProjectVersionsPage,
    VersionPreview,
    VersionStatus,
)
from omnia_api.services.project_versions import resolve_version, version_preview_fields

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
    current_id = None
    resolved = {}
    if project.current_snapshot_id:
        # Managed commits may change HEAD without accepting a new user version.
        # Resolve the nearest represented ancestor, with an explicit depth bound
        # and project membership checked at every edge (including corrupt cycles).
        ancestors = (
            select(Snapshot.id, Snapshot.parent_id, literal(0).label("depth"))
            .where(Snapshot.id == project.current_snapshot_id, Snapshot.project_id == project_id)
            .cte("version_ancestors", recursive=True)
        )
        ancestors = ancestors.union_all(
            select(Snapshot.id, Snapshot.parent_id, (ancestors.c.depth + 1).label("depth"))
            .join(ancestors, Snapshot.id == ancestors.c.parent_id)
            .where(Snapshot.project_id == project_id, ancestors.c.depth < 256)
        )
        effective_id = func.coalesce(
            sql_cast(Message.snapshot_id, Text),
            GenerationRun.agent_state["snapshot_id"].astext,
            sql_cast(ProjectVersion.snapshot_id, Text),
        )
        candidate = await session.scalar(
            query.outerjoin(GenerationRun, ProjectVersion.generation_run_id == GenerationRun.id)
            .outerjoin(
                Message,
                (GenerationRun.assistant_message_id == Message.id)
                & (Message.project_id == project_id),
            )
            .join(ancestors, effective_id == sql_cast(ancestors.c.id, Text))
            .where(
                or_(
                    GenerationRun.status == "completed",
                    ProjectVersion.generation_run_id.is_(None)
                    & ProjectVersion.status.in_(["ready", "unchanged"]),
                )
            )
            .order_by(ancestors.c.depth.asc(), ProjectVersion.number.desc())
            .limit(1)
        )
        if candidate:
            current_id = candidate.id
    for version in page[:limit]:
        if version.id not in resolved:
            resolved[version.id] = await resolve_version(session, version)
    result = []
    for version in page[:limit]:
        state, snapshot = resolved[version.id]
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

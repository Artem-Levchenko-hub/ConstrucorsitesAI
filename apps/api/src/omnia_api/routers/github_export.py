"""Project → GitHub export endpoint.

Thin HTTP layer: auth + ownership check + WS progress events. The create-repo
and push mechanics live in services/github_export.py.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.redis import publish_event
from omnia_api.models.github_connection import GithubConnection
from omnia_api.models.project import Project
from omnia_api.schemas.github import GithubExportRequest, GithubExportResult
from omnia_api.services import github_export

router = APIRouter(prefix="/api/projects", tags=["github"])


@router.post("/{project_id}/export/github", response_model=GithubExportResult)
async def export_to_github(
    project_id: UUID,
    payload: GithubExportRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> GithubExportResult:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)

    connection = await session.get(GithubConnection, current_user.id)
    if connection is None:
        raise ApiError(
            "github_not_connected",
            "Connect your GitHub account before exporting.",
            status.HTTP_400_BAD_REQUEST,
        )

    repo_name = payload.repo_name or project.slug
    await publish_event(
        project_id,
        "github.export.progress",
        {"project_id": str(project_id), "stage": "pushing"},
    )
    try:
        result = await github_export.export_project_to_github(
            project,
            connection,
            repo_name=repo_name,
            private=payload.private,
            description=payload.description,
        )
    except ApiError as exc:
        await publish_event(
            project_id,
            "github.export.failed",
            {"project_id": str(project_id), "error": exc.message},
        )
        raise
    await session.commit()
    await publish_event(
        project_id,
        "github.export.complete",
        {
            "project_id": str(project_id),
            "repo_url": result.repo_url,
            "repo_full_name": result.repo_full_name,
        },
    )
    return result

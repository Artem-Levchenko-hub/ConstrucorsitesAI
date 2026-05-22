"""Export a project's git repo to GitHub: create (or adopt) the repo, then push.

Source of truth stays MinIO/pygit2 — GitHub is a mirror. Re-exporting an
already-linked project pushes new commits to the same repo.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from omnia_api.core.config import get_settings
from omnia_api.core.crypto import decrypt_token
from omnia_api.core.errors import ApiError
from omnia_api.models.github_connection import GithubConnection
from omnia_api.models.project import Project
from omnia_api.schemas.github import GithubExportResult
from omnia_api.services import github_client, repo

log = structlog.get_logger(__name__)


async def _resolve_repo(
    token: str,
    username: str,
    project: Project,
    *,
    repo_name: str,
    private: bool,
    description: str | None,
) -> tuple[str, str, str]:
    """Return (full_name, html_url, default_branch) of the target repo."""
    if project.github_repo_full_name:
        info = await github_client.get_repo(token, project.github_repo_full_name)
        if info is None:
            raise ApiError(
                "github_rejected",
                f"Linked repo {project.github_repo_full_name} no longer exists on GitHub.",
                404,
            )
        return (
            str(info["full_name"]),
            str(info["html_url"]),
            str(info.get("default_branch") or "main"),
        )

    existing = await github_client.get_repo(token, f"{username}/{repo_name}")
    if existing is not None:
        return (
            str(existing["full_name"]),
            str(existing["html_url"]),
            str(existing.get("default_branch") or "main"),
        )

    created = await github_client.create_repo(
        token, name=repo_name, private=private, description=description
    )
    return (
        str(created["full_name"]),
        str(created["html_url"]),
        str(created.get("default_branch") or "main"),
    )


async def export_project_to_github(
    project: Project,
    connection: GithubConnection,
    *,
    repo_name: str,
    private: bool,
    description: str | None,
) -> GithubExportResult:
    """Create/adopt the GitHub repo and push the project's history. Mutates the
    project's github_* fields (caller is responsible for committing)."""
    token = decrypt_token(connection.access_token_encrypted)
    full_name, html_url, default_branch = await _resolve_repo(
        token,
        connection.github_username,
        project,
        repo_name=repo_name,
        private=private,
        description=description,
    )

    git_host = get_settings().github_oauth_base.rstrip("/")
    remote_url = f"{git_host}/{full_name}.git"
    await asyncio.to_thread(repo.push_to_remote, project.id, remote_url, token, default_branch)

    pushed_at = datetime.now(UTC)
    project.github_repo_full_name = full_name
    project.github_repo_url = html_url
    project.github_last_pushed_at = pushed_at
    log.info("github.export.pushed", project_id=str(project.id), repo=full_name)

    return GithubExportResult(
        repo_url=html_url,
        repo_full_name=full_name,
        default_branch=default_branch,
        pushed_at=pushed_at,
    )

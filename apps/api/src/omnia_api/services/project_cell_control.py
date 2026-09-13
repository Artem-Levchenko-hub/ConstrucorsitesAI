"""Side-effect-free Project Cell control readiness inspection."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from omnia_api.models.user import User
from omnia_api.services.orchestrator_client import get_project_cell_capabilities
from omnia_api.services.project_cell_access import (
    decide_project_cell_access,
    decide_project_cell_selection,
)


@dataclass(frozen=True, slots=True)
class ProjectCellControlReadiness:
    selected: bool
    ready: bool
    provider: str
    reason: str


def _selected_failure(reason: str) -> ProjectCellControlReadiness:
    return ProjectCellControlReadiness(
        selected=True,
        ready=False,
        provider="docker_owner_canary",
        reason=reason,
    )


async def inspect_project_cell_control(
    user: User,
    project_id: UUID,
    *,
    project_cell_enabled: bool = False,
    has_workspace: bool = False,
) -> ProjectCellControlReadiness:
    access = (
        decide_project_cell_selection(
            user, project_cell_enabled=project_cell_enabled, has_workspace=has_workspace,
        )
        if project_cell_enabled is True or has_workspace is True
        else decide_project_cell_access(user)
    )
    if not access.enabled or access.provider == "legacy":
        return ProjectCellControlReadiness(
            selected=False,
            ready=False,
            provider="legacy",
            reason=access.reason,
        )

    # Provider assignment is durable, but it does not authorize a suspended or
    # unverified account to start a generation. Stay selected and fail closed.
    if user.status != "active" or user.is_anon:
        return _selected_failure("account_ineligible")
    if user.email is None or user.email_verified_at is None:
        return _selected_failure("email_unverified")

    try:
        capability = await get_project_cell_capabilities(project_id)
    except Exception:
        return _selected_failure("provider_unavailable")

    remote_project_id = capability.get("project_id")
    provider = capability.get("provider")
    enabled = capability.get("enabled")
    ready = capability.get("ready")
    state = capability.get("state")
    detail = capability.get("detail")
    if (
        type(remote_project_id) is not str
        or type(provider) is not str
        or type(enabled) is not bool
        or type(ready) is not bool
        or type(state) is not str
        or type(detail) is not str
    ):
        return _selected_failure("invalid_capability_response")
    if remote_project_id != str(project_id) or provider != access.provider:
        return _selected_failure("capability_mismatch")
    if enabled is not True:
        return _selected_failure("invalid_capability_response")
    if ready is True and state == "ready":
        return ProjectCellControlReadiness(
            selected=True,
            ready=True,
            provider="docker_owner_canary",
            reason="ready",
        )
    if ready is False and state == "unsupported":
        return _selected_failure("provider_unsupported")
    return _selected_failure("invalid_capability_response")

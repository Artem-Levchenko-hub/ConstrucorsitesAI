from dataclasses import dataclass
from typing import Literal

from omnia_api.core.config import Settings, get_settings
from omnia_api.models.user import User


@dataclass(frozen=True, slots=True)
class ProjectCellAccessDecision:
    enabled: bool
    provider: Literal["legacy", "docker_owner_canary"]
    reason: str


def admit_new_project_cell(user: User, settings: Settings | None = None) -> bool:
    """New-project admission only; never use a rollout flag to classify old data."""
    config = settings or get_settings()
    return (
        config.project_cell_general_availability_enabled is True
        and user.status == "active" and not user.is_anon
        and user.email is not None and user.email_verified_at is not None
    )


def decide_project_cell_selection(
    user: User,
    *,
    project_cell_enabled: bool = False,
    has_workspace: bool = False,
    settings: Settings | None = None,
) -> ProjectCellAccessDecision:
    """Choose storage/runtime, not request authorization or record permissions.

    A persisted assignment cannot fall back to legacy when admission is disabled.
    Existing owner/active-account checks remain at the API and generation boundary.
    """
    if has_workspace is True:
        return ProjectCellAccessDecision(True, "docker_owner_canary", "durable_workspace")
    if project_cell_enabled is True:
        return ProjectCellAccessDecision(True, "docker_owner_canary", "project_admitted")
    return decide_project_cell_access(user, settings)


def decide_project_cell_access(
    user: User,
    settings: Settings | None = None,
) -> ProjectCellAccessDecision:
    config = settings or get_settings()
    if not config.project_cell_docker_canary_enabled:
        return ProjectCellAccessDecision(False, "legacy", "feature_disabled")
    if user.status != "active" or user.is_anon:
        return ProjectCellAccessDecision(False, "legacy", "account_ineligible")
    if user.email is None or user.email_verified_at is None:
        return ProjectCellAccessDecision(False, "legacy", "email_unverified")
    if user.email.strip().casefold() not in config.project_cell_canary_email_set:
        return ProjectCellAccessDecision(False, "legacy", "account_not_allowlisted")
    return ProjectCellAccessDecision(True, "docker_owner_canary", "owner_canary")

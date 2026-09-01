from dataclasses import dataclass
from typing import Literal

from omnia_api.core.config import Settings, get_settings
from omnia_api.models.user import User


@dataclass(frozen=True, slots=True)
class ProjectCellAccessDecision:
    enabled: bool
    provider: Literal["legacy", "docker_owner_canary"]
    reason: str


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

from omnia_api.core.config import get_settings
from omnia_api.models.user import User


def has_unlimited_generation_access(user: User) -> bool:
    """Return the narrow wallet/demo exemption for one account.

    Administrator access and generation access are deliberately independent:
    operational staff must not receive an open-ended provider entitlement merely
    because they can manage users.  The global setting remains as the existing
    environment-wide testing escape hatch.
    """

    return get_settings().unlimited_generations or user.unlimited_generations


def should_consume_free_generation(
    user: User,
    *,
    is_free: bool,
    max_demo_reserved: bool,
    project_template: str,
) -> bool:
    """Keep onboarding counters unchanged for the persisted owner entitlement."""

    return (
        is_free
        and not max_demo_reserved
        and project_template != "max_miniapp"
        and not user.unlimited_generations
    )

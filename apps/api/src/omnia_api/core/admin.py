from omnia_api.core.config import get_settings
from omnia_api.models.user import User


def is_admin_user(user: User) -> bool:
    """Persisted role is primary; the email allowlist remains bootstrap access."""
    return user.role == "admin" or bool(
        user.email and user.email.lower() in get_settings().admin_emails_set
    )

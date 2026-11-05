from yleum_api.models.user import User


def is_admin_user(user: User) -> bool:
    """Administrator authority comes only from the persisted account role."""
    return user.role == "admin"

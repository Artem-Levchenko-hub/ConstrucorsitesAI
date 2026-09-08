"""Shared owner and support requirements for MAX launch and publication."""

from omnia_api.schemas.max_studio import MaxProjectConfigPayload


def has_launch_owner_and_support(config: MaxProjectConfigPayload | None) -> bool:
    return bool(
        config and config.operator.legal_name.strip() and (config.support.email or "").strip()
    )

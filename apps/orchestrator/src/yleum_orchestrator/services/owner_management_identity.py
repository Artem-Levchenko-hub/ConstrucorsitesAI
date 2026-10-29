"""Publication-only owner capability. Never pass a bot token to product code."""

import hashlib
import hmac
from collections.abc import Mapping
from uuid import UUID

OWNER_CAPABILITY_KEYS = (
    "YLEUM_OWNER_ASSERTION_KEY", "YLEUM_OWNER_PROJECT_ID", "YLEUM_OWNER_ID",
    "YLEUM_OWNER_PUBLICATION_ID", "YLEUM_BUSINESS_ANALYTICS_KEY",
)


def owner_capabilities_match(actual: Mapping[str, str], desired: Mapping[str, str]) -> bool:
    """Revocation also removes old keys; unrelated environment is not authority."""
    return all(actual.get(key) == desired.get(key) for key in OWNER_CAPABILITY_KEYS)


def owner_management_environment(
    project_id: UUID,
    owner_id: UUID,
    publication_id: UUID | str,
    runtime_env: Mapping[str, str],
) -> dict[str, str]:
    # The controller binding identifies supported releases without granting
    # authority. Retain it after revoke so a later connection can install keys.
    environment = {
        "YLEUM_OWNER_PROJECT_ID": str(project_id),
        "YLEUM_OWNER_ID": str(owner_id),
        "YLEUM_OWNER_PUBLICATION_ID": str(UUID(str(publication_id))),
    }
    token = runtime_env.get("MAX_BOT_TOKEN")
    if not token:
        return environment
    # Purpose keys cannot sign a MAX login or call the external bot API.
    environment["YLEUM_OWNER_ASSERTION_KEY"] = hmac.new(
        token.encode(), b"yleum:owner-management:v1", hashlib.sha256,
    ).hexdigest()
    environment["YLEUM_BUSINESS_ANALYTICS_KEY"] = hmac.new(
        token.encode(), b"yleum:business-analytics:v1", hashlib.sha256,
    ).hexdigest()
    return environment

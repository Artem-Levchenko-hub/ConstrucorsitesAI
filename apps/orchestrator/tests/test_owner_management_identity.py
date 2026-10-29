import hashlib
import hmac
from uuid import UUID

from yleum_orchestrator.services.owner_management_identity import (
    owner_capabilities_match,
    owner_management_environment,
)

PROJECT = UUID("11111111-1111-4111-8111-111111111111")
OWNER = UUID("22222222-2222-4222-8222-222222222222")
RELEASE = UUID("33333333-3333-4333-8333-333333333333")


def test_app_receives_only_purpose_key_bound_to_actual_owner_and_release():
    env = owner_management_environment(
        PROJECT, OWNER, RELEASE, {"MAX_BOT_TOKEN": "synthetic-token"}
    )
    assert env == {
        "YLEUM_OWNER_ASSERTION_KEY": hmac.new(
            b"synthetic-token",
            b"yleum:owner-management:v1",
            hashlib.sha256,
        ).hexdigest(),
        "YLEUM_OWNER_PROJECT_ID": str(PROJECT),
        "YLEUM_OWNER_ID": str(OWNER),
        "YLEUM_OWNER_PUBLICATION_ID": str(RELEASE),
        "YLEUM_BUSINESS_ANALYTICS_KEY": hmac.new(
            b"synthetic-token",
            b"yleum:business-analytics:v1",
            hashlib.sha256,
        ).hexdigest(),
    }
    assert "synthetic-token" not in env.values()


def test_absent_bot_does_not_create_owner_authority_and_rotation_changes_key():
    assert owner_management_environment(PROJECT, OWNER, RELEASE, {}) == {
        "YLEUM_OWNER_PROJECT_ID": str(PROJECT),
        "YLEUM_OWNER_ID": str(OWNER),
        "YLEUM_OWNER_PUBLICATION_ID": str(RELEASE),
    }
    first = owner_management_environment(PROJECT, OWNER, RELEASE, {"MAX_BOT_TOKEN": "one"})
    second = owner_management_environment(PROJECT, OWNER, RELEASE, {"MAX_BOT_TOKEN": "two"})
    assert first["YLEUM_OWNER_ASSERTION_KEY"] != second["YLEUM_OWNER_ASSERTION_KEY"]


def test_rotation_and_revoke_require_exact_capability_environment_refresh():
    old = owner_management_environment(PROJECT, OWNER, RELEASE, {"MAX_BOT_TOKEN": "old"})
    fresh = owner_management_environment(PROJECT, OWNER, RELEASE, {"MAX_BOT_TOKEN": "fresh"})
    assert owner_capabilities_match(old, old)
    assert not owner_capabilities_match(old, fresh)
    assert not owner_capabilities_match(old, {})
    assert owner_capabilities_match({"OTHER": "value"}, {})

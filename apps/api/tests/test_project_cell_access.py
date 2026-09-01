from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from omnia_api.core.config import Settings
from omnia_api.models.user import User
from omnia_api.services.project_cell_access import decide_project_cell_access


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    def build(**overrides: object) -> Settings:
        return Settings(**overrides)

    return build


def _user(
    *,
    email: str | None,
    verified: bool,
    anon: bool = False,
    status: str = "active",
) -> User:
    return User(
        id=uuid4(),
        email=email,
        password_hash="unused",
        is_anon=anon,
        status=status,
        email_verified_at=datetime.now(UTC) if verified else None,
    )


# Mutations caught: enabling the feature by default or evaluating account eligibility first.
def test_project_cell_access_is_disabled_by_default(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()

    decision = decide_project_cell_access(
        _user(email="owner@example.com", verified=True), settings
    )

    assert decision.enabled is False
    assert decision.provider == "legacy"
    assert decision.reason == "feature_disabled"


@pytest.mark.parametrize(
    ("user", "expected_reason"),
    [
        (_user(email="owner@example.com", verified=True, status="suspended"), "account_ineligible"),
        (_user(email="owner@example.com", verified=True, anon=True), "account_ineligible"),
        (_user(email=None, verified=True), "email_unverified"),
        (_user(email="owner@example.com", verified=False), "email_unverified"),
        (_user(email="stranger@example.com", verified=True), "account_not_allowlisted"),
    ],
    ids=["inactive", "anonymous", "missing-email", "unverified-email", "not-allowlisted"],
)
# Mutations caught: skipping an eligibility check or returning the wrong rejection reason.
def test_project_cell_access_rejects_ineligible_accounts_with_specific_reason(
    settings_factory: Callable[..., Settings],
    user: User,
    expected_reason: str,
) -> None:
    settings = settings_factory(
        project_cell_docker_canary_enabled=True,
        project_cell_canary_emails="owner@example.com",
    )

    decision = decide_project_cell_access(user, settings)

    assert decision.enabled is False
    assert decision.provider == "legacy"
    assert decision.reason == expected_reason


# Mutations caught: case-sensitive comparison, missing trim, or selecting the legacy provider.
def test_project_cell_access_normalizes_verified_allowlisted_owner(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory(
        project_cell_docker_canary_enabled=True,
        project_cell_canary_emails=" stranger@example.com,  OWNER@Example.COM , ",
    )

    decision = decide_project_cell_access(
        _user(email="  Owner@example.com  ", verified=True), settings
    )

    assert decision.enabled is True
    assert decision.provider == "docker_owner_canary"
    assert decision.reason == "owner_canary"

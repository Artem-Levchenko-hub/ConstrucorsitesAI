from types import SimpleNamespace

from omnia_api.core import generation_access
from omnia_api.models.user import User


def _settings(*, global_unlimited: bool = False) -> SimpleNamespace:
    return SimpleNamespace(unlimited_generations=global_unlimited)


def test_admin_role_alone_does_not_bypass_generation_limits(monkeypatch) -> None:
    monkeypatch.setattr(generation_access, "get_settings", lambda: _settings())
    user = User(email="admin@example.com", role="admin", unlimited_generations=False)

    assert generation_access.has_unlimited_generation_access(user) is False


def test_account_entitlement_bypasses_generation_limits(monkeypatch) -> None:
    monkeypatch.setattr(generation_access, "get_settings", lambda: _settings())
    user = User(email="creator@example.com", role="admin", unlimited_generations=True)

    assert generation_access.has_unlimited_generation_access(user) is True


def test_environment_escape_hatch_still_applies(monkeypatch) -> None:
    monkeypatch.setattr(
        generation_access,
        "get_settings",
        lambda: _settings(global_unlimited=True),
    )
    user = User(email="tester@example.com", role="user", unlimited_generations=False)

    assert generation_access.has_unlimited_generation_access(user) is True


def test_account_entitlement_does_not_consume_onboarding_counter() -> None:
    user = User(email="creator@example.com", unlimited_generations=True)

    assert (
        generation_access.should_consume_free_generation(
            user,
            is_free=True,
            max_demo_reserved=False,
            project_template="landing",
        )
        is False
    )


def test_regular_free_generation_consumes_onboarding_counter() -> None:
    user = User(email="user@example.com", unlimited_generations=False)

    assert (
        generation_access.should_consume_free_generation(
            user,
            is_free=True,
            max_demo_reserved=False,
            project_template="landing",
        )
        is True
    )

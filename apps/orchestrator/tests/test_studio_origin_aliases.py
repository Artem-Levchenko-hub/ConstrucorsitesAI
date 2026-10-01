"""The two supported Studio origins must both embed owner previews."""

from __future__ import annotations

import pytest

from yleum_orchestrator.core.config import Settings
from yleum_orchestrator.services import studio_origins as origins_module
from yleum_orchestrator.services.machine_boundary import owner_framing

_APEX = "https://yleum.ru"
_WWW = "https://www.yleum.ru"
_LEGACY = "https://constructor.lead-generator.ru"


def _configure(monkeypatch: pytest.MonkeyPatch, primary: str, legacy: str) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@127.0.0.1:1/no_database",
        internal_token="synthetic-origin-regression-secret",
        workspace_origin=primary,
        workspace_legacy_origins=legacy,
    )
    monkeypatch.setattr(origins_module, "get_settings", lambda: settings)


@pytest.mark.parametrize("primary,expected_primary", [
    (_APEX, _APEX),
    (_WWW, _WWW),
    (_APEX + ":443", _APEX + ":443"),
    (_WWW + ":443", _WWW + ":443"),
    ("HTTPS://YLEUM.RU/", _APEX),
    ("HTTPS://WWW.YLEUM.RU:443/", _WWW + ":443"),
])
@pytest.mark.parametrize("legacy", ["", _LEGACY])
def test_supported_studio_pair_survives_legacy_configuration_override(
    monkeypatch: pytest.MonkeyPatch,
    primary: str,
    expected_primary: str,
    legacy: str,
) -> None:
    _configure(monkeypatch, primary, legacy)
    origins = origins_module.studio_origins()
    assert origins[0] == expected_primary
    assert set(origins) == {expected_primary, _APEX, _WWW, *([legacy] if legacy else [])}
    assert set(owner_framing(origins).split()) == {"frame-ancestors", "'self'", *origins}
    assert "*" not in origins


@pytest.mark.parametrize(
    "primary",
    [
        "https://studio.example",
        "https://yleum.ru.attacker.example",
        "https://www.yleum.ru.attacker.example",
        "https://app.yleum.ru",
        "https://yleum.ru:8443",
    ],
)
def test_other_configured_origins_do_not_gain_a_studio_alias(
    monkeypatch: pytest.MonkeyPatch,
    primary: str,
) -> None:
    _configure(monkeypatch, primary, "https://legacy.example")
    assert origins_module.studio_origins() == [primary, "https://legacy.example"]


def test_default_origin_fallback_also_allows_both_supported_studio_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_settings():
        raise ValueError("synthetic settings unavailable")

    monkeypatch.setattr(origins_module, "get_settings", unavailable_settings)
    assert set(origins_module.studio_origins()) == {_APEX, _WWW, _LEGACY}


def test_wildcard_configuration_cannot_expand_the_owner_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, "https://*.yleum.ru", "*,https://*.example,https://ok.example")
    assert origins_module.studio_origins() == ["https://ok.example"]
    assert (
        owner_framing(origins_module.studio_origins())
        == "frame-ancestors 'self' https://ok.example"
    )

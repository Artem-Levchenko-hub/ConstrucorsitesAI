"""Unit tests for the wake-on-request host→container mapping.

The docker-touching parts (wake, status) are integration; here we pin the
pure hostname parsing that decides WHICH container to boot — getting this
wrong would either fail to wake or, worse, start the wrong project.
"""

from __future__ import annotations

import pytest

from omnia_orchestrator.routers import ingress


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("RUNTIME_HOST_SUFFIX", "preview.omniadevelop.ru")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_dev_host_maps_to_dev_container() -> None:
    assert (
        ingress._resolve_container("my-app-dev.preview.omniadevelop.ru")
        == "omnia-dev-my-app"
    )


def test_prod_host_maps_to_app_container() -> None:
    assert (
        ingress._resolve_container("my-app.preview.omniadevelop.ru")
        == "omnia-app-my-app"
    )


def test_host_with_port_is_normalized() -> None:
    assert (
        ingress._resolve_container("My-App-Dev.preview.omniadevelop.ru:443")
        == "omnia-dev-my-app"
    )


@pytest.mark.parametrize(
    "host",
    [
        "",  # empty
        "preview.omniadevelop.ru",  # bare suffix, no label
        "evil.com",  # different domain
        "a.b.preview.omniadevelop.ru",  # multi-label, not a single slug
        "-dev.preview.omniadevelop.ru",  # empty slug before -dev
        "under_score-dev.preview.omniadevelop.ru",  # invalid label char
    ],
)
def test_unknown_hosts_resolve_to_none(host: str) -> None:
    assert ingress._resolve_container(host) is None

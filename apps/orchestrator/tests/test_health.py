from types import SimpleNamespace

import pytest

from omnia_orchestrator.routers import health


@pytest.mark.asyncio
async def test_health_exposes_release_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(omnia_release_sha="a7c4fc22"),
        raising=False,
    )

    assert await health.health() == {"status": "ok", "release_sha": "a7c4fc22"}


@pytest.mark.parametrize("unsafe", ["A7C4FC22", "abc123", "a7c4fc22\nSECRET=x"])
@pytest.mark.asyncio
async def test_health_never_reflects_unsafe_release_values(
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(omnia_release_sha=unsafe),
    )

    assert await health.health() == {"status": "ok", "release_sha": "unknown"}

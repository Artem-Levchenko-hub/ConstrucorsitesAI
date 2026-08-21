from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from omnia_api.services import readiness


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b'{"at":"2026-08-20T12:00:00+00:00","release_sha":"a7c4fc22"}', (True, "a7c4fc22")),
        (b"2026-08-20T12:00:00+00:00", (True, "unknown")),
        (b'{"at":"2026-08-20T12:00:00+00:00","release_sha":"UNSAFE"}', (True, "unknown")),
        (None, (False, "unknown")),
    ],
)
def test_worker_heartbeat_parser_is_rolling_upgrade_safe(raw, expected) -> None:
    assert readiness.parse_worker_heartbeat(raw) == expected


@pytest.mark.asyncio
async def test_worker_heartbeat_has_a_bounded_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, int]] = []

    class FakeRedis:
        async def set(self, key: str, value: str, *, ex: int) -> None:
            calls.append((key, value, ex))

    monkeypatch.setattr(readiness, "get_redis", lambda: FakeRedis())
    monkeypatch.setattr(
        readiness,
        "get_settings",
        lambda: SimpleNamespace(omnia_release_sha="a7c4fc22"),
    )
    await readiness.write_worker_heartbeat(180)
    assert calls[0][0] == readiness.WORKER_HEARTBEAT_KEY
    payload = json.loads(calls[0][1])
    assert payload["at"].endswith("+00:00")
    assert payload["release_sha"] == "a7c4fc22"
    assert calls[0][2] == 180


@pytest.mark.asyncio
async def test_readiness_names_independent_control_plane_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def yes() -> bool:
        return True

    async def redis_and_worker() -> tuple[bool, bool, str]:
        return True, True, "a7c4fc22"

    async def deploy_control_plane() -> tuple[bool, str]:
        return True, "a7c4fc22"

    monkeypatch.setattr(readiness, "_database_ok", yes)
    monkeypatch.setattr(readiness, "_redis_and_worker", redis_and_worker)
    monkeypatch.setattr(readiness, "_deploy_control_plane_ok", deploy_control_plane)
    monkeypatch.setattr(readiness, "_preview_storage_ok", yes)
    report = await readiness.probe_readiness()
    assert report.checks == {
        "database": "ok",
        "redis": "ok",
        "worker": "ok",
        "deploy_control_plane": "ok",
        "preview_storage": "ok",
    }
    assert report.dependencies == {
        "worker_release_sha": "a7c4fc22",
        "orchestrator_release_sha": "a7c4fc22",
    }


@pytest.mark.asyncio
async def test_deploy_control_plane_reports_orchestrator_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "release_sha": "a7c4fc22"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        readiness.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=transport),
    )
    monkeypatch.setattr(
        readiness,
        "get_settings",
        lambda: SimpleNamespace(orchestrator_url="http://orchestrator.test"),
    )

    assert await readiness._deploy_control_plane_ok() == (True, "a7c4fc22")

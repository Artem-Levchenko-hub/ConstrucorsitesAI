from __future__ import annotations

import pytest

from omnia_api.services import readiness

pytestmark = pytest.mark.asyncio


async def test_worker_heartbeat_has_a_bounded_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, int]] = []

    class FakeRedis:
        async def set(self, key: str, value: str, *, ex: int) -> None:
            calls.append((key, value, ex))

    monkeypatch.setattr(readiness, "get_redis", lambda: FakeRedis())
    await readiness.write_worker_heartbeat(180)
    assert calls[0][0] == readiness.WORKER_HEARTBEAT_KEY
    assert calls[0][1].endswith("+00:00")
    assert calls[0][2] == 180

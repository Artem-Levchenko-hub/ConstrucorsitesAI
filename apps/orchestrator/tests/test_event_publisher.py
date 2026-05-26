"""Unit tests for `core.event_publisher` — fail-soft Redis pub-sub fan-out.

Critical invariants:
- A downed Redis MUST NOT raise — provision/deploy can't be killed by a
  missing live-update.
- A serialization slip MUST NOT raise — same reason.
- Channel name MUST be `omnia:project:<project_id>` exactly so the api
  ws_hub's `psubscribe("omnia:project:*")` actually matches.
"""

from __future__ import annotations

import json

import pytest

from omnia_orchestrator.core import event_publisher


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    # Reset module state so each test starts with a fresh client.
    event_publisher._redis_client = None


def test_channel_name_matches_ws_hub_pattern() -> None:
    """The api's ws_hub psubscribes to `omnia:project:*`. If this drifts
    nothing will reach the WebSocket clients and the workspace will spin
    forever on "provisioning"."""
    assert (
        event_publisher._channel("01234567-89ab-cdef-0123-456789abcdef")
        == "omnia:project:01234567-89ab-cdef-0123-456789abcdef"
    )


async def test_publish_no_op_when_redis_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis hiccup must NOT raise. Without this, a single Redis blip
    would kill an in-flight provision/deploy and the user would see a
    500 for what's really a notification-channel failure."""

    async def fake_get_client() -> None:
        return None  # simulates connect_failed branch

    monkeypatch.setattr(event_publisher, "_get_client", fake_get_client)
    # Must not raise.
    await event_publisher.publish_project_event(
        "00000000-0000-0000-0000-000000000001",
        "runtime.started",
        {"runtime": {"state": "running"}},
    )


async def test_publish_no_op_when_serialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pathological data payload (e.g. non-JSON object that slipped past
    a caller) must not bring down the publish — log + swallow."""

    class FakeRedis:
        async def publish(self, channel: str, payload: str) -> None:
            pass  # never reached in this test

    async def fake_get_client() -> object:
        return FakeRedis()

    monkeypatch.setattr(event_publisher, "_get_client", fake_get_client)

    class Unserializable:
        pass

    # Must not raise.
    await event_publisher.publish_project_event(
        "00000000-0000-0000-0000-000000000001",
        "deploy.failed",
        {"weird": Unserializable()},  # type: ignore[dict-item]
    )


async def test_publish_serializes_data_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: publish gets the exact channel name and a JSON envelope
    `{type, data}` matching what ws_hub.broadcast_local hands to the
    WebSocket client."""
    captured: dict[str, str] = {}

    class FakeRedis:
        async def publish(self, channel: str, payload: bytes | str) -> None:
            captured["channel"] = channel
            captured["payload"] = (
                payload.decode() if isinstance(payload, bytes) else payload
            )

    async def fake_get_client() -> object:
        return FakeRedis()

    monkeypatch.setattr(event_publisher, "_get_client", fake_get_client)

    await event_publisher.publish_project_event(
        "00000000-0000-0000-0000-000000000001",
        "runtime.started",
        {"runtime": {"state": "running", "port": 3200}},
    )

    assert captured["channel"] == "omnia:project:00000000-0000-0000-0000-000000000001"
    body = json.loads(captured["payload"])
    assert body == {
        "type": "runtime.started",
        "data": {"runtime": {"state": "running", "port": 3200}},
    }

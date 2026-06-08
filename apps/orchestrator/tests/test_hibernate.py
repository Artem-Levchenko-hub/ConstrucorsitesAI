"""Unit tests for `services.hibernate` sweep + activity tracking.

Integration tests against real Docker + Redis are out of scope here — they
need a docker daemon and a redis server, neither of which we spin up in
unit tests. We cover the deterministic pieces that regress under refactors:

- tier → threshold mapping (free 15 min, pro/business 60 min)
- pause-vs-stop decision per tier
- `_sweep_once` bootstrap, threshold respect, action dispatch, error isolation
- `record_activity` mutates the shared map
- start/stop idempotency

`_list_dev_containers` and `docker_client.stop_container` are monkey-patched —
this is intentional. The pure sweep logic (decide whether to act) is what
regresses; the docker SDK glue is covered separately in docker_client tests
(when added) and end-to-end smoke on the VPS.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.services import hibernate


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset settings + module state for every test."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("HIBERNATE_FREE_TIER_MINUTES", "15")
    monkeypatch.setenv("HIBERNATE_PRO_TIER_MINUTES", "60")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    hibernate._last_activity.clear()
    hibernate._last_rx.clear()
    # Isolate the sweep from real Docker: the network probe is exercised by its
    # own tests below, which re-patch this. Default to "no traffic" so the
    # timer-only sweep tests behave exactly as before the probe existed.
    monkeypatch.setattr(hibernate, "_read_dev_rx", lambda: {})


# ---------- tier policy ----------


def test_tier_threshold_free_is_15_min() -> None:
    assert hibernate._tier_threshold_seconds("free") == 15 * 60


def test_tier_threshold_pro_is_60_min() -> None:
    assert hibernate._tier_threshold_seconds("pro") == 60 * 60


def test_tier_threshold_business_uses_pro_window() -> None:
    """Business is treated like pro — same warm-pause behaviour, not a
    separate window. Update if pricing introduces a third threshold."""
    assert hibernate._tier_threshold_seconds("business") == 60 * 60


def test_tier_threshold_unknown_defaults_to_free() -> None:
    """A label typo or missing tier must NEVER grant pro privileges."""
    assert hibernate._tier_threshold_seconds("vip-anonymous") == 15 * 60
    assert hibernate._tier_threshold_seconds("") == 15 * 60


def test_should_pause_pro_true() -> None:
    assert hibernate._should_pause("pro") is True
    assert hibernate._should_pause("business") is True


def test_should_pause_free_false() -> None:
    assert hibernate._should_pause("free") is False
    assert hibernate._should_pause("") is False
    assert hibernate._should_pause("typo") is False


# ---------- record_activity ----------


async def test_record_activity_writes_current_time() -> None:
    pid = "00000000-0000-0000-0000-000000000001"
    before = time.time()
    await hibernate.record_activity(pid)
    after = time.time()
    assert before <= hibernate._last_activity[pid] <= after


async def test_record_activity_overwrites_existing() -> None:
    pid = "00000000-0000-0000-0000-000000000002"
    hibernate._last_activity[pid] = 0.0  # ancient
    await hibernate.record_activity(pid)
    assert hibernate._last_activity[pid] > 1_000_000_000  # not the ancient one


# ---------- network-activity probe ----------


PID_NET = "00000000-0000-0000-0000-0000000000a0"


async def test_network_activity_bumps_when_rx_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RX grew since last sweep → preview is being watched → reset idle timer."""
    hibernate._last_rx[PID_NET] = 100.0
    monkeypatch.setattr(hibernate, "_read_dev_rx", lambda: {PID_NET: 5000.0})

    now = time.time()
    await hibernate._refresh_network_activity(now)

    assert hibernate._last_activity[PID_NET] == now
    assert hibernate._last_rx[PID_NET] == 5000.0  # baseline advanced


async def test_network_activity_no_bump_when_rx_flat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No new traffic (RX unchanged) → idle timer untouched, so idleness
    keeps accruing and a truly-idle preview still hibernates."""
    hibernate._last_rx[PID_NET] = 5000.0
    monkeypatch.setattr(hibernate, "_read_dev_rx", lambda: {PID_NET: 5000.0})

    await hibernate._refresh_network_activity(time.time())

    assert PID_NET not in hibernate._last_activity  # never marked active


async def test_network_activity_first_sight_only_seeds_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First reading has nothing to compare against — seed the baseline but
    do NOT mark active (a fresh container shouldn't look 'busy' for free)."""
    monkeypatch.setattr(hibernate, "_read_dev_rx", lambda: {PID_NET: 5000.0})

    await hibernate._refresh_network_activity(time.time())

    assert hibernate._last_rx[PID_NET] == 5000.0
    assert PID_NET not in hibernate._last_activity


async def test_sweep_keeps_watched_preview_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ The P0 fix: a container idle 16 min on the timer is NOT hibernated if
    its preview served traffic this cycle. Without the network probe this
    container would be stopped right under the viewer."""
    hibernate._last_activity[PID_NET] = time.time() - 16 * 60  # past threshold
    hibernate._last_rx[PID_NET] = 100.0
    monkeypatch.setattr(hibernate, "_read_dev_rx", lambda: {PID_NET: 999_999.0})
    monkeypatch.setattr(
        hibernate,
        "_list_dev_containers",
        lambda: [("omnia-dev-watched", "running", PID_NET, "free")],
    )
    stop_mock = AsyncMock()
    monkeypatch.setattr(hibernate.docker_client, "stop_container", stop_mock)

    await hibernate._sweep_once()

    stop_mock.assert_not_called()


async def test_sweep_hibernates_unwatched_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No traffic this cycle (RX flat) + idle past threshold → still hibernate.
    Guards that the probe didn't accidentally pin every container awake."""
    hibernate._last_activity[PID_NET] = time.time() - 16 * 60
    hibernate._last_rx[PID_NET] = 5000.0
    monkeypatch.setattr(hibernate, "_read_dev_rx", lambda: {PID_NET: 5000.0})
    monkeypatch.setattr(
        hibernate,
        "_list_dev_containers",
        lambda: [("omnia-dev-quiet", "running", PID_NET, "free")],
    )
    stop_mock = AsyncMock()
    monkeypatch.setattr(hibernate.docker_client, "stop_container", stop_mock)

    await hibernate._sweep_once()

    stop_mock.assert_awaited_once_with("omnia-dev-quiet", pause=False)


def test_read_dev_rx_failsoft_on_docker_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker daemon unreachable → `{}`, never raises (sweep must survive)."""
    import docker as docker_sdk  # type: ignore[import-untyped]

    def boom(*_a: object, **_kw: object) -> object:
        raise docker_sdk.errors.DockerException("daemon down")

    monkeypatch.setattr(hibernate.docker, "DockerClient", boom)

    assert hibernate._read_dev_rx() == {}


# ---------- sweep behaviour ----------


PID_BOOT = "00000000-0000-0000-0000-000000000010"
PID_FREE_IDLE = "00000000-0000-0000-0000-000000000020"
PID_PRO_IDLE = "00000000-0000-0000-0000-000000000030"
PID_UNDER = "00000000-0000-0000-0000-000000000040"
PID_PAUSED = "00000000-0000-0000-0000-000000000050"


async def test_sweep_bootstraps_unknown_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First sighting → record `now`, don't hibernate yet."""
    monkeypatch.setattr(
        hibernate,
        "_list_dev_containers",
        lambda: [("omnia-dev-x", "running", PID_BOOT, "free")],
    )
    stop_mock = AsyncMock()
    monkeypatch.setattr(hibernate.docker_client, "stop_container", stop_mock)

    await hibernate._sweep_once()

    stop_mock.assert_not_called()
    assert PID_BOOT in hibernate._last_activity


async def test_sweep_stops_free_tier_past_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle 16 min on free tier → stop (not pause)."""
    hibernate._last_activity[PID_FREE_IDLE] = time.time() - 16 * 60
    monkeypatch.setattr(
        hibernate,
        "_list_dev_containers",
        lambda: [("omnia-dev-y", "running", PID_FREE_IDLE, "free")],
    )
    stop_mock = AsyncMock()
    monkeypatch.setattr(hibernate.docker_client, "stop_container", stop_mock)

    await hibernate._sweep_once()

    stop_mock.assert_awaited_once_with("omnia-dev-y", pause=False)


async def test_sweep_pauses_pro_tier_past_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle 61 min on pro tier → pause (keep memory)."""
    hibernate._last_activity[PID_PRO_IDLE] = time.time() - 61 * 60
    monkeypatch.setattr(
        hibernate,
        "_list_dev_containers",
        lambda: [("omnia-dev-z", "running", PID_PRO_IDLE, "pro")],
    )
    stop_mock = AsyncMock()
    monkeypatch.setattr(hibernate.docker_client, "stop_container", stop_mock)

    await hibernate._sweep_once()

    stop_mock.assert_awaited_once_with("omnia-dev-z", pause=True)


async def test_sweep_skips_when_idle_under_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 min idle on free tier (threshold 15) → no action."""
    hibernate._last_activity[PID_UNDER] = time.time() - 5 * 60
    monkeypatch.setattr(
        hibernate,
        "_list_dev_containers",
        lambda: [("omnia-dev-w", "running", PID_UNDER, "free")],
    )
    stop_mock = AsyncMock()
    monkeypatch.setattr(hibernate.docker_client, "stop_container", stop_mock)

    await hibernate._sweep_once()

    stop_mock.assert_not_called()


async def test_sweep_ignores_already_hibernated_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused container, however ancient, is not re-paused. Idempotency
    protection — without this, every sweep would re-call pause on a
    long-paused container and burn API calls for nothing."""
    hibernate._last_activity[PID_PAUSED] = time.time() - 24 * 60 * 60
    monkeypatch.setattr(
        hibernate,
        "_list_dev_containers",
        lambda: [("omnia-dev-q", "paused", PID_PAUSED, "free")],
    )
    stop_mock = AsyncMock()
    monkeypatch.setattr(hibernate.docker_client, "stop_container", stop_mock)

    await hibernate._sweep_once()

    stop_mock.assert_not_called()


async def test_sweep_continues_after_one_container_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One stop_container raising OrchestratorError must NOT break the sweep
    for sibling containers. Critical: a single buggy project shouldn't keep
    every other project running past its idle window."""
    pid_a = "00000000-0000-0000-0000-000000000061"
    pid_b = "00000000-0000-0000-0000-000000000062"
    hibernate._last_activity[pid_a] = time.time() - 20 * 60
    hibernate._last_activity[pid_b] = time.time() - 20 * 60
    monkeypatch.setattr(
        hibernate,
        "_list_dev_containers",
        lambda: [
            ("omnia-dev-a", "running", pid_a, "free"),
            ("omnia-dev-b", "running", pid_b, "free"),
        ],
    )

    attempts: list[str] = []

    async def flaky_stop(name: str, pause: bool = False) -> None:
        attempts.append(name)
        if name == "omnia-dev-a":
            raise OrchestratorError(
                code="container_failure",
                message="docker daemon hiccup",
                status_code=500,
            )

    monkeypatch.setattr(hibernate.docker_client, "stop_container", flaky_stop)

    await hibernate._sweep_once()

    assert attempts == ["omnia-dev-a", "omnia-dev-b"]


async def test_sweep_skips_containers_without_project_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A container with `omnia.kind=dev` but missing `omnia.project_id` is
    foreign and must be left alone. Real case: hand-launched debug containers
    by an engineer testing on the same docker host."""
    # _list_dev_containers already filters these out internally; this test
    # documents that contract via the sweep level.
    monkeypatch.setattr(hibernate, "_list_dev_containers", lambda: [])
    stop_mock = AsyncMock()
    monkeypatch.setattr(hibernate.docker_client, "stop_container", stop_mock)

    await hibernate._sweep_once()

    stop_mock.assert_not_called()


async def test_sweep_handles_docker_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker daemon down → log + return cleanly, no exception bubbles up."""
    import docker as docker_sdk  # type: ignore[import-untyped]

    def boom() -> list[tuple[str, str, str, str]]:
        raise docker_sdk.errors.DockerException("daemon down")

    monkeypatch.setattr(hibernate, "_list_dev_containers", boom)
    stop_mock = AsyncMock()
    monkeypatch.setattr(hibernate.docker_client, "stop_container", stop_mock)

    # Must NOT raise.
    await hibernate._sweep_once()
    stop_mock.assert_not_called()


# ---------- start/stop loop ----------


async def test_start_then_stop_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end-ish: start spawns a sweep task, stop cancels it cleanly.
    Redis import is monkey-patched to fail so we never try a real connection."""

    # Sabotage the redis import so pubsub setup fails fast (fail-soft path).
    import sys

    def _boom(*_a: object, **_kw: object) -> object:
        raise RuntimeError("no redis")

    stub = type("stub", (), {"from_url": staticmethod(_boom)})
    monkeypatch.setitem(sys.modules, "redis.asyncio", stub)

    await hibernate.start_hibernate_loop()
    assert hibernate._loop_task is not None
    assert not hibernate._loop_task.done()

    # Second call is a no-op.
    first = hibernate._loop_task
    await hibernate.start_hibernate_loop()
    assert hibernate._loop_task is first

    await hibernate.stop_hibernate_loop()
    assert hibernate._loop_task is None
    assert hibernate._pubsub_task is None


async def test_stop_when_never_started_is_safe() -> None:
    """Calling stop without start (or twice) must not raise."""
    await hibernate.stop_hibernate_loop()
    await hibernate.stop_hibernate_loop()  # idempotent

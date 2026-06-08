"""Idle timer that hibernates inactive dev containers.

Design (sprint A1 — fills in for the prior scaffold):

- **Activity ingest** — three reinforcing signals feed `_last_activity`:
  1. Redis pub-sub on channel `activity:<project_id>` — whoever fronts the
     dev preview (apps/api proxy / ws hub) publishes on every request that
     reaches a live container; we `psubscribe("activity:*")`.
  2. `POST /internal/projects/<id>/heartbeat` — bare HTTP fallback for envs
     without Redis (tests, bare-metal compose) and the workspace UI keepalive.
  3. **Network probe** (`_read_dev_rx`) — a direct hit to a project's preview
     subdomain proxies straight to `127.0.0.1:<port>` via its own nginx
     vhost and never touches the orchestrator, so signals 1-2 miss it. Each
     sweep reads every running dev container's network RX counter; if it grew
     since last sweep, a browser is rendering the preview (HMR holds a socket
     open only while a tab is connected) → reset the idle timer. This is what
     keeps a preview that's being actively watched from hibernating under the
     user. Wake explicitly resets the timer too, so a click never races us.

- **Sweep** — every 60 s walk every container with label `omnia.kind=dev`,
  read `omnia.tier` (defaulting to "free"), compare idle vs tier threshold,
  and call `docker_client.stop_container(pause=<tier∈{pro,business}>)`. Free
  tier frees RAM (cold-start ~30-60 s on wake); Pro keeps the process state
  (warm wake ~1-3 s).

- **Bootstrap** — a container the sweeper sees for the first time (no
  `_last_activity` entry, e.g. after orchestrator restart) is recorded as
  "active now". Worst case: we miss one hibernate cycle for that project
  (15 min on free). The alternative — hibernating brand-new containers — is
  strictly worse.

Fail-soft (R-10):
- Redis unreachable → pubsub task isn't started, sweep still runs (everything
  hibernates at threshold, same as a quiet preview). Sweep retry-reconnects
  on every loss with a 5 s backoff so a Redis restart heals automatically.
- Docker daemon hiccup during list → sweep skip this cycle, try again.
- One container failing to stop → log + continue with the rest.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any

import docker  # type: ignore[import-untyped]
import structlog

from omnia_orchestrator.core import docker_client
from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError

log = structlog.get_logger("omnia_orchestrator.hibernate")

# Single-process orchestrator on a single event loop — plain dict is safe
# without a lock (all writes happen from coroutines, not threads).
_last_activity: dict[str, float] = {}
# Last-seen total RX bytes per project, for the network-activity probe. A
# growth between two sweeps means the preview served traffic (a viewer is
# connected) → activity. Seeded on first sight; never used as an absolute.
_last_rx: dict[str, float] = {}
_loop_task: asyncio.Task[None] | None = None
_pubsub_task: asyncio.Task[None] | None = None
_redis_client: Any = None  # redis.asyncio.Redis | None — lazy import

# Sweep cadence: short enough to enforce 15-min idle within ±1 min, long
# enough to stay invisible in `docker ps` rates on a 100-project tenant.
_SWEEP_INTERVAL_SECONDS = 60
# Backoff between Redis reconnect attempts on the pubsub side.
_PUBSUB_RECONNECT_BACKOFF_SECONDS = 5


def _tier_threshold_seconds(tier: str) -> int:
    """Idle window before this tier's container is hibernated."""
    s = get_settings()
    if tier in ("pro", "business"):
        return s.hibernate_pro_tier_minutes * 60
    return s.hibernate_free_tier_minutes * 60


def _should_pause(tier: str) -> bool:
    """Pro / business → pause (keep memory). Free → stop (free RAM)."""
    return tier in ("pro", "business")


async def record_activity(project_id: str) -> None:
    """Mark a project as active right now.

    Public so the `/wake` endpoint and `/heartbeat` fallback can reset the
    idle timer without going through Redis. Tests use this directly to seed
    state.
    """
    _last_activity[project_id] = time.time()


def _list_dev_containers() -> list[tuple[str, str, str, str]]:
    """Return (name, status, project_id, tier) for every dev container.

    Sync docker-py call — the caller runs us in a thread. Containers without
    a `omnia.project_id` label are skipped (something else's container with
    a clashing kind label).
    """
    client = docker.DockerClient(base_url=get_settings().docker_host)
    containers = client.containers.list(
        all=True,
        filters={"label": "omnia.kind=dev"},
    )
    out: list[tuple[str, str, str, str]] = []
    for c in containers:
        labels = c.labels or {}
        project_id = labels.get("omnia.project_id")
        if not project_id:
            continue
        out.append(
            (c.name, c.status, project_id, labels.get("omnia.tier", "free"))
        )
    return out


def _read_dev_rx() -> dict[str, float]:
    """Total network RX bytes per project for every *running* dev container.

    Traffic to a dev container means a browser is rendering its preview — the
    HMR WebSocket stays open only while a tab is connected, and the sweep can't
    otherwise see preview requests (they hit the per-project nginx vhost and
    proxy straight to `127.0.0.1:<port>`, never the orchestrator).

    Sync docker-py call — the caller runs us in a thread. One `stats` read per
    running dev container per sweep; cheap at our scale (a handful of live
    previews). Fail-soft (R-10): any docker hiccup returns `{}`, so the sweep
    silently falls back to timer-only hibernation and never raises.
    """
    out: dict[str, float] = {}
    try:
        client = docker.DockerClient(base_url=get_settings().docker_host)
        containers = client.containers.list(
            filters={"label": "omnia.kind=dev", "status": "running"},
        )
    except docker.errors.DockerException:
        return {}
    for c in containers:
        project_id = (c.labels or {}).get("omnia.project_id")
        if not project_id:
            continue
        try:
            stats = c.stats(stream=False)
        except Exception:
            continue  # one container's stats failing must not blind the rest
        rx = 0.0
        for iface in (stats.get("networks") or {}).values():
            rx += float(iface.get("rx_bytes", 0) or 0)
        out[project_id] = rx
    return out


async def _refresh_network_activity(now: float) -> None:
    """Reset the idle timer for any dev container whose RX grew since the last
    sweep — i.e. its preview is being actively watched. First sight only seeds
    the baseline (no prior reading to compare), so a freshly-listed container
    isn't falsely marked active. Complements the Redis/heartbeat paths, which
    the direct-preview case never triggers.
    """
    rx_now = await asyncio.to_thread(_read_dev_rx)
    for project_id, rx in rx_now.items():
        prev = _last_rx.get(project_id)
        _last_rx[project_id] = rx
        if prev is not None and rx > prev:
            _last_activity[project_id] = now


async def _sweep_once() -> None:
    """One sweep pass: hibernate any container idle past its tier threshold."""
    now = time.time()
    # Fold in network traffic before judging idleness so an actively-watched
    # preview (whose hits never reach us) isn't hibernated under the viewer.
    await _refresh_network_activity(now)

    try:
        containers = await asyncio.to_thread(_list_dev_containers)
    except docker.errors.DockerException as exc:
        log.warning("hibernate.docker_unavailable", err=str(exc))
        return

    for name, status, project_id, tier in containers:
        if status != "running":
            continue  # already paused / stopped / exited — nothing to do

        threshold = _tier_threshold_seconds(tier)
        last = _last_activity.get(project_id)
        if last is None:
            # First time we see this container — bootstrap to "active now"
            # so we don't hibernate a freshly-provisioned project before its
            # first request lands.
            _last_activity[project_id] = now
            continue

        idle = now - last
        if idle < threshold:
            continue

        pause = _should_pause(tier)
        log.info(
            "hibernate.idle_detected",
            project_id=project_id,
            container=name,
            tier=tier,
            idle_seconds=int(idle),
            action="pause" if pause else "stop",
        )
        try:
            await docker_client.stop_container(name, pause=pause)
        except OrchestratorError as exc:
            log.warning(
                "hibernate.action_failed",
                project_id=project_id,
                container=name,
                err=exc.message,
            )
        # Note: we keep _last_activity[project_id] as-is. Wake will reset it;
        # if Redis fires activity for a paused container that's a no-op (the
        # next sweep finds status != running and skips).


async def _sweep_loop() -> None:
    """Run `_sweep_once` every `_SWEEP_INTERVAL_SECONDS` forever."""
    while True:
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let a bug kill the loop
            log.warning("hibernate.sweep_error", err=str(exc))
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)


async def _consume_pubsub() -> None:
    """Subscribe to `activity:*` and update timestamps. Reconnects forever."""
    while True:
        try:
            if _redis_client is None:
                return  # never connected — loop has nothing to do
            pubsub = _redis_client.pubsub()
            try:
                await pubsub.psubscribe("activity:*")
                log.info("hibernate.pubsub.subscribed")
                async for msg in pubsub.listen():
                    if msg.get("type") != "pmessage":
                        continue
                    raw_channel = msg.get("channel")
                    if isinstance(raw_channel, (bytes, bytearray)):
                        channel = raw_channel.decode("utf-8", errors="replace")
                    else:
                        channel = str(raw_channel)
                    _, _, project_id = channel.partition(":")
                    if project_id:
                        _last_activity[project_id] = time.time()
            finally:
                with suppress(Exception):
                    await pubsub.aclose()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("hibernate.pubsub.disconnected", err=str(exc))
            await asyncio.sleep(_PUBSUB_RECONNECT_BACKOFF_SECONDS)


async def start_hibernate_loop() -> None:
    """Spawn pub-sub listener + sweep loop. Idempotent — re-call is a no-op.

    Order matters: sweep starts even if Redis is down. Without Redis the
    sweeper still hibernates at threshold; without the sweeper, free-tier
    containers would never pause, which is the worse failure.
    """
    global _redis_client, _loop_task, _pubsub_task

    if _loop_task is not None and not _loop_task.done():
        return

    settings = get_settings()
    redis_url = settings.redis_url
    try:
        # Lazy import: redis is a runtime optional during PoC scaffolding.
        from redis import asyncio as redis_async

        _redis_client = redis_async.from_url(  # type: ignore[no-untyped-call]
            redis_url, decode_responses=False
        )
        await _redis_client.ping()
        _pubsub_task = asyncio.create_task(_consume_pubsub())
        log.info("hibernate.pubsub.connected", url=redis_url)
    except Exception as exc:
        _redis_client = None
        log.warning(
            "hibernate.pubsub_unavailable",
            url=redis_url,
            err=str(exc),
        )

    _loop_task = asyncio.create_task(_sweep_loop())
    log.info("hibernate.loop.started")


async def stop_hibernate_loop() -> None:
    """Cancel the sweep + pub-sub tasks and close the Redis client."""
    global _loop_task, _pubsub_task, _redis_client

    for task in (_pubsub_task, _loop_task):
        if task is None or task.done():
            continue
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task

    _loop_task = None
    _pubsub_task = None

    if _redis_client is not None:
        with suppress(Exception):
            await _redis_client.aclose()
        _redis_client = None

    log.info("hibernate.loop.stopped")

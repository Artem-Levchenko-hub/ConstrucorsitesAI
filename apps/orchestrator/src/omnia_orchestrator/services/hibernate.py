"""Idle timer that hibernates inactive dev containers.

Design:
- Each container has an `activity` record in memory + persisted snapshots in
  Redis (TTL == idle-threshold + buffer). Any HTTP request to the dev URL
  resets the timer (ingress publishes to Redis pub-sub `activity:<id>`).
- Background loop checks every 60s: if `now - last_activity > tier_threshold`,
  call docker pause (Pro) or stop (Free).
- Wake is on-demand: ingress receives a request, calls `wake` endpoint, then
  proxies the request once container is healthy.

TODO sprint A1:
  - implement the Redis pub-sub listener (reuse `apps/api/core/redis.py` pattern)
  - implement the periodic sweep task started from lifespan
  - tier lookup: fetch from apps/api shared Postgres (wallets/users join)
"""

from __future__ import annotations

import structlog

log = structlog.get_logger("omnia_orchestrator.hibernate")


async def start_hibernate_loop() -> None:
    """Background task started in lifespan. Runs forever.

    TODO sprint A1: implement the sweep + pub-sub consumer.
    """
    log.info("hibernate.loop.scaffold")
    # Stub: real impl will iterate over active containers and decide pause vs stop.


async def stop_hibernate_loop() -> None:
    log.info("hibernate.loop.stop.scaffold")

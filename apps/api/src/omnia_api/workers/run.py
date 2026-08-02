"""Entrypoint для `python -m omnia_api.workers.run` (или `uv run rq worker omnia-previews`)."""

from __future__ import annotations

import asyncio
import threading

from redis import Redis
from rq import Connection, Worker

from omnia_api.core.config import get_settings
from omnia_api.services.queue import QUEUE_NAME
from omnia_api.services.subscription_lifecycle import run_subscription_lifecycle_forever


def _run_billing_lifecycle() -> None:
    asyncio.run(run_subscription_lifecycle_forever())


def main() -> None:
    threading.Thread(
        target=_run_billing_lifecycle,
        name="subscription-lifecycle",
        daemon=True,
    ).start()
    conn = Redis.from_url(get_settings().redis_url)
    with Connection(conn):
        # Preview jobs use bounded delayed retries. The embedded scheduler is
        # required by RQ when Retry.interval is configured.
        Worker([QUEUE_NAME]).work(with_scheduler=True)


if __name__ == "__main__":
    main()

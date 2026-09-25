"""Entrypoint для `python -m yleum_api.workers.run` (или `uv run rq worker omnia-previews`)."""

from __future__ import annotations

import asyncio
import threading

from redis import Redis
from rq import Connection, Worker

from yleum_api.core.config import get_settings
from yleum_api.services.queue import QUEUE_NAME
from yleum_api.services.readiness import run_worker_heartbeat_forever
from yleum_api.services.restoration_reconciliation import run_restoration_reconciliation_forever
from yleum_api.services.subscription_lifecycle import run_subscription_lifecycle_forever
from yleum_api.services.task_board_attachment_cleanup import run_attachment_cleanup_forever


def _run_worker_heartbeat() -> None:
    asyncio.run(run_worker_heartbeat_forever())


def _run_billing_lifecycle() -> None:
    asyncio.run(run_subscription_lifecycle_forever())


def _run_attachment_cleanup() -> None:
    asyncio.run(run_attachment_cleanup_forever())


def _run_restoration_reconciliation() -> None:
    asyncio.run(run_restoration_reconciliation_forever())


def main() -> None:
    # The worker heartbeat (`/api/health` → checks.worker) no longer depends on
    # the billing thread: the billing tick may live in the commerce cluster
    # instead (BILLING_LIFECYCLE_ENABLED=false here), and the RQ worker must
    # still report itself alive.
    threading.Thread(
        target=_run_worker_heartbeat,
        name="worker-heartbeat",
        daemon=True,
    ).start()
    if get_settings().billing_lifecycle_enabled:
        threading.Thread(
            target=_run_billing_lifecycle,
            name="subscription-lifecycle",
            daemon=True,
        ).start()
    threading.Thread(
        target=_run_attachment_cleanup,
        name="task-board-attachment-cleanup",
        daemon=True,
    ).start()
    # AV19.1: restoration cancel/apply/prepare finish without a client GET.
    threading.Thread(
        target=_run_restoration_reconciliation,
        name="restoration-reconciliation",
        daemon=True,
    ).start()
    conn = Redis.from_url(get_settings().redis_url)
    with Connection(conn):
        Worker([QUEUE_NAME]).work(with_scheduler=False)


if __name__ == "__main__":
    main()

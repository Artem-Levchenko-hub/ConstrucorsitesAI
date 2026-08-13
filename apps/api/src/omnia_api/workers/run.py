"""Supervise isolated RQ capacity for durable generation and preview work."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import signal
import threading
from multiprocessing.process import BaseProcess
from typing import Any, cast

from redis import Redis
from rq import Connection, Worker

from omnia_api.core.config import get_settings
from omnia_api.services.queue import GENERATION_QUEUE_NAME, QUEUE_NAME
from omnia_api.services.subscription_lifecycle import run_subscription_lifecycle_forever

_WORKER_QUEUES = (GENERATION_QUEUE_NAME, QUEUE_NAME)


def _run_billing_lifecycle() -> None:
    asyncio.run(run_subscription_lifecycle_forever())


def _run_worker(queue_name: str) -> None:
    """One OS process per workload prevents preview head-of-line blocking."""

    # Give each RQ worker and its per-job horse a private process group. The
    # supervisor can then stop the entire tree, including a stuck horse, without
    # signalling the sibling queue or any generated project container.
    if hasattr(os, "setsid"):
        os.setsid()
    connection = Redis.from_url(get_settings().redis_url)
    with Connection(connection):
        Worker(
            [queue_name],
            name=f"omnia-{queue_name}",
            # Observe and reap a killed/overdue horse promptly. RQ's Unix
            # SIGALRM remains the hard outer job_timeout enforcement.
            job_monitoring_interval=5,
            maintenance_interval=30,
        ).work(with_scheduler=True)


def _spawn_worker(
    context: Any,
    queue_name: str,
) -> BaseProcess:
    process = cast(
        BaseProcess,
        context.Process(
            target=_run_worker,
            args=(queue_name,),
            name=f"rq-{queue_name}",
        ),
    )
    process.start()
    return process


def _signal_worker_tree(process: BaseProcess, *, force: bool) -> None:
    pid = getattr(process, "pid", None)
    kill_group = getattr(os, "killpg", None)
    if os.name == "posix" and pid is not None and callable(kill_group):
        signum = int(
            getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM
        )
        try:
            kill_group(pid, signum)
            return
        except ProcessLookupError:
            pass
        except OSError:
            # Child may not have reached setsid yet; fall back to the direct
            # multiprocessing signal below.
            pass
    if force:
        process.kill()
    else:
        process.terminate()


def _shutdown_workers(workers: dict[str, BaseProcess]) -> None:
    """Stop, reap and finally kill children that ignore graceful SIGTERM."""

    for process in workers.values():
        if process.is_alive():
            _signal_worker_tree(process, force=False)
    for process in workers.values():
        process.join(timeout=10)
    for process in workers.values():
        if process.is_alive():
            _signal_worker_tree(process, force=True)
            process.join(timeout=5)
        process.close()


def main() -> None:
    # ``spawn`` is deliberate: forking after starting the billing thread can
    # copy locked interpreter/Redis state into a child. Each RQ process starts
    # cleanly and may safely fork its own per-job horse on Linux.
    context = multiprocessing.get_context("spawn")
    workers = {name: _spawn_worker(context, name) for name in _WORKER_QUEUES}
    threading.Thread(
        target=_run_billing_lifecycle,
        name="subscription-lifecycle",
        daemon=True,
    ).start()
    stopping = threading.Event()

    def _stop(_signum: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        while not stopping.wait(2):
            for name, process in list(workers.items()):
                if process.is_alive():
                    continue
                # If the RQ parent crashed while its horse was alive, clear the
                # private process group before replacing capacity.
                _signal_worker_tree(process, force=True)
                process.join(timeout=1)
                process.close()
                logging.getLogger(__name__).error(
                    "RQ worker exited; restoring isolated capacity queue=%s", name
                )
                workers[name] = _spawn_worker(context, name)
    finally:
        _shutdown_workers(workers)


if __name__ == "__main__":
    main()

"""Entrypoint для `python -m omnia_api.workers.run` (или `uv run rq worker omnia-previews`)."""

from __future__ import annotations

from redis import Redis
from rq import Connection, Worker

from omnia_api.core.config import get_settings
from omnia_api.services.queue import QUEUE_NAME


def main() -> None:
    conn = Redis.from_url(get_settings().redis_url)
    with Connection(conn):
        Worker([QUEUE_NAME]).work(with_scheduler=False)


if __name__ == "__main__":
    main()

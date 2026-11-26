"""Shared physical admission limit for on-demand MAX source inspection."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import BoundedSemaphore

_MAX_SOURCE_READS = 2
_SOURCE_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MAX_SOURCE_READS, thread_name_prefix="max-owner-source",
)
_SOURCE_SLOTS = BoundedSemaphore(_MAX_SOURCE_READS)
_HISTORY_SLOTS = BoundedSemaphore(1)


class SourceReadUnavailable(Exception):
    """No MAX source reader is available within the request deadline."""


async def run_max_source_read[T](read: Callable[[], T], *, historical: bool = False) -> T:
    # One archived report can occupy a worker while the other is available for diagnosis.
    gates = (_HISTORY_SLOTS, _SOURCE_SLOTS) if historical else (_SOURCE_SLOTS,)
    with ExitStack() as admissions:
        for gate in gates:
            if not gate.acquire(blocking=False):
                raise SourceReadUnavailable
            admissions.callback(gate.release)
        future = _SOURCE_EXECUTOR.submit(read)
        admitted_slots = admissions.pop_all()
    # Cancellation of the request does not finish a running object-store read.
    future.add_done_callback(lambda _completed: admitted_slots.close())
    try:
        async with asyncio.timeout(4):
            return await asyncio.wrap_future(future)
    except TimeoutError as exc:
        raise SourceReadUnavailable from exc

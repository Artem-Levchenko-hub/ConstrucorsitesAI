"""Finish one receipt on disconnect, then preserve the caller's cancellation."""

import asyncio
from collections.abc import Coroutine
from typing import Any

import structlog
from anyio import CancelScope

log = structlog.get_logger(__name__)


async def await_provider_receipt[ReceiptResult](
    operation: Coroutine[Any, Any, ReceiptResult] | asyncio.Task[ReceiptResult],
) -> ReceiptResult:
    task = operation if isinstance(operation, asyncio.Task) else asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        if task.cancelled():
            raise
        with CancelScope(shield=True):
            while True:
                try:
                    await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise cancelled from None
                except Exception as exc:
                    log.warning("provider_receipt.failed_after_cancel", error_type=type(exc).__name__)
                    break
        raise cancelled

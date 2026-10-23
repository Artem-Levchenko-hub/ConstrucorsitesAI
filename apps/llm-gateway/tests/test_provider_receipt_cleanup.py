import asyncio

import pytest

from yleum_gateway.services.provider_receipt_cleanup import await_provider_receipt


async def test_cancellation_finishes_same_receipt_then_propagates_before_backup():
    started, release = asyncio.Event(), asyncio.Event()
    outcomes = []
    async def receipt():
        started.set()
        await release.wait()
        outcomes.append("receipt")
    async def request():
        await await_provider_receipt(receipt())
        outcomes.append("backup")
    task = asyncio.create_task(request())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert outcomes == ["receipt"]

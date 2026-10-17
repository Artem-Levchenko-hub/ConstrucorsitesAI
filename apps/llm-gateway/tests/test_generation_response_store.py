from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from yleum_gateway.services.generation_response_store import ChunkWriter, ResponseIndeterminate


class Objects:
    def __init__(self):
        self.data = {}
        self.fail = False

    async def put(self, key, data):
        if self.fail:
            raise OSError("synthetic unavailable storage")
        if key in self.data:
            assert self.data[key] == data
        self.data[key] = data

    async def get(self, key):
        return self.data[key]


class Ledger:
    def __init__(self):
        self.parts = []
        self.committed = None
        self.unknown = False

    async def reserve(self, part):
        self.parts.append(part)

    async def acknowledge(self, part):
        assert part in self.parts

    async def complete(self, manifest, key, digest):
        self.committed = (manifest, key, digest)

    async def indeterminate(self):
        self.unknown = True


async def chunks(*parts):
    for part in parts:
        yield part


async def test_streamed_chunks_commit_only_after_final_digest():
    objects, ledger = Objects(), Ledger()
    writer = ChunkWriter(uuid4(), objects, ledger, chunk_bytes=3)
    result = await writer.store(
        chunks(b"abcde", b"fghi"), finish_reason="stop", route="/v1/messages"
    )
    assert result.complete
    assert result.sha256 == hashlib.sha256(b"abcdefghi").hexdigest()
    assert ledger.committed[0]["chunks"] == 3
    assert ledger.committed[0]["bytes"] == 9
    assert all(len(value) <= 3 for key, value in objects.data.items() if "/chunks/" in key)


async def test_partial_object_and_cleanup_never_certify_response():
    objects, ledger = Objects(), Ledger()
    writer = ChunkWriter(uuid4(), objects, ledger, chunk_bytes=3)

    async def partial():
        yield b"abc"
        raise OSError("synthetic EOF before complete receipt")

    with pytest.raises(ResponseIndeterminate):
        await writer.store(partial(), finish_reason="stop", route="/v1/messages")
    assert ledger.committed is None and ledger.unknown
    assert not any("manifest" in key for key in objects.data)


async def test_unknown_upload_keeps_reservation_and_does_not_commit():
    objects, ledger = Objects(), Ledger()
    objects.fail = True
    with pytest.raises(ResponseIndeterminate):
        await ChunkWriter(uuid4(), objects, ledger).store(
            chunks(b"body"), finish_reason="stop", route="/v1/messages"
        )
    assert ledger.parts and ledger.committed is None and ledger.unknown


async def test_storage_failure_closes_owned_provider_iterator_before_return():
    objects, ledger = Objects(), Ledger()
    objects.fail = True
    settled = []

    async def provider():
        try:
            yield b"complete chunk"
            pytest.fail("storage failure must not read another provider event")
        finally:
            settled.append("financial-finally")

    stream = provider()
    with pytest.raises(ResponseIndeterminate):
        await ChunkWriter(uuid4(), objects, ledger, chunk_bytes=3).store(
            stream, finish_reason="stop", route="/v1/messages"
        )
    assert settled == ["financial-finally"]
    assert ledger.committed is None and ledger.unknown


async def test_large_answer_has_no_cumulative_storage_ceiling():
    # Keep only the latest chunk: verifies >64MiB without a large test heap.
    class RollingObjects(Objects):
        async def put(self, key, data):
            self.data = {key: data}

    objects, ledger = RollingObjects(), Ledger()
    block = b"x" * (1024 * 1024)

    async def large():
        for _ in range(65):
            yield block

    result = await ChunkWriter(uuid4(), objects, ledger).store(
        large(), finish_reason="stop", route="/v1/messages"
    )
    assert result.complete and ledger.committed[0]["bytes"] == 65 * len(block)
    assert ledger.committed[0]["chunks"] == 65


async def test_tiny_transport_packets_are_coalesced_into_bounded_storage_chunks():
    from yleum_gateway.services.generation_response_store import CHUNK_BYTES

    objects, ledger = Objects(), Ledger()

    async def packets():
        for _ in range(10000):
            yield b"data: tiny SSE event\n\n"

    await ChunkWriter(uuid4(), objects, ledger).store(
        packets(), finish_reason="complete_sse", route="/v1/chat/completions"
    )
    data_parts = [part for part in ledger.parts if part["kind"] == "chunks"]
    assert len(data_parts) == 1  # 10,000 packets create one data row/object.
    assert len(ledger.parts) == len(objects.data) == 2  # Plus the fixed manifest.
    assert all(part["bytes"] <= CHUNK_BYTES for part in data_parts)

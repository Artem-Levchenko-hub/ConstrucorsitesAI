"""Private immutable chunks; body completeness is independent of financial evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from yleum_gateway.core.errors import GatewayError

CHUNK_BYTES = 1024 * 1024
OBJECT_OVERHEAD = 4096
MANIFEST_RESERVE = 16 * 1024
_EMPTY_CHAIN = "0" * 64


class ResponseIndeterminate(GatewayError):
    code = "generation_response_indeterminate"
    http_status = 409

    def __init__(self, message: str) -> None:
        super().__init__(message, details={"provider_charge_ambiguous": True, "retry_safe": False})


@dataclass(frozen=True, slots=True)
class StoredGenerationResponse:
    attempt_id: UUID
    sha256: str
    object_key: str
    complete: bool


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...


class BodyLedger(Protocol):
    async def reserve(self, part: dict[str, Any]) -> None: ...
    async def acknowledge(self, part: dict[str, Any]) -> None: ...
    async def complete(self, manifest: dict[str, Any], key: str, digest: str) -> None: ...
    async def indeterminate(self) -> None: ...


class ChunkWriter:
    """Only bounded work units; no cumulative byte/chunk-count ceiling."""

    def __init__(
        self,
        attempt_id: UUID,
        objects: ObjectStore,
        ledger: BodyLedger,
        *,
        chunk_bytes: int = CHUNK_BYTES,
    ) -> None:
        if not 0 < chunk_bytes <= CHUNK_BYTES:
            raise ValueError("invalid chunk buffer")
        self.attempt_id, self.objects, self.ledger = attempt_id, objects, ledger
        self.chunk_bytes = chunk_bytes
        self.digest = hashlib.sha256()
        self.chain, self.count, self.length = _EMPTY_CHAIN, 0, 0

    async def _part(self, data: bytes, *, manifest: bool = False) -> str:
        digest = sha(data)
        kind = "manifest" if manifest else "chunks"
        key = f"v1/{self.attempt_id}/{kind}/{self.count}-{digest}"
        part = {"index": self.count, "key": key, "sha256": digest, "bytes": len(data), "kind": kind}
        # Durable reservation precedes S3; missing acknowledgement retains it.
        await self.ledger.reserve(part)
        await self.objects.put(key, data)
        if await self.objects.get(key) != data:
            raise ResponseIndeterminate("stored response verification failed")
        await self.ledger.acknowledge(part)
        if not manifest:
            self.chain = sha(bytes.fromhex(self.chain) + canonical(part))
            self.digest.update(data)
            self.length += len(data)
            self.count += 1
        return key

    async def store(
        self, chunks: AsyncIterator[bytes], *, finish_reason: str, route: str
    ) -> StoredGenerationResponse:
        if route not in {"/v1/messages", "/v1/project-cell/messages", "/v1/chat/completions"}:
            raise ValueError("invalid response route")
        if not isinstance(finish_reason, str) or not finish_reason or len(finish_reason) > 80:
            raise ValueError("missing complete transport receipt")
        buffer = bytearray()
        try:
            async for data in chunks:
                if not isinstance(data, bytes):
                    raise TypeError("response chunks must be bytes")
                view = memoryview(data)
                offset = 0
                while offset < len(view):
                    take = min(self.chunk_bytes - len(buffer), len(view) - offset)
                    buffer.extend(view[offset : offset + take])
                    offset += take
                    if len(buffer) == self.chunk_bytes:
                        await self._part(bytes(buffer))
                        buffer.clear()
            if buffer:
                await self._part(bytes(buffer))
            manifest = {
                "version": 1,
                "attempt_id": str(self.attempt_id),
                "route": route,
                "finish_reason": finish_reason,
                "chunks": self.count,
                "bytes": self.length,
                "sha256": self.digest.hexdigest(),
                "chain_sha256": self.chain,
            }
            encoded = canonical(manifest)
            key = await self._part(encoded, manifest=True)
            await self.ledger.complete(manifest, key, sha(encoded))
            return StoredGenerationResponse(self.attempt_id, self.digest.hexdigest(), key, True)
        except BaseException as exc:
            # Stop the owned input deterministically: its finally block owns the
            # existing provider journal/settlement, even if object storage fails.
            close = getattr(chunks, "aclose", None)
            if close is not None:
                with suppress(BaseException):
                    await close()
            # Process death/unknown commit must retain all reservations. This
            # best-effort marker is not required for the fail-closed default.
            with suppress(BaseException):
                await asyncio.shield(self.ledger.indeterminate())
            if isinstance(exc, (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt)):
                raise
            raise ResponseIndeterminate("generation response was not confirmed") from None


async def load_generation_response(
    identity: Any, *, objects: ObjectStore | None = None
) -> bytes | None:
    """Only a current owned reserved intent is confirmed absence; never generic 404."""
    from yleum_gateway.core.db import get_pool
    from yleum_gateway.services.generation_attempts import _owned
    from yleum_gateway.services.generation_response_ledger import matches
    from yleum_gateway.services.generation_response_objects import PrivateObjects

    try:
        # Read never acquires budget. Completed readers and cleanup share body
        # locks; cleanup uses NOWAIT, so a slow read cannot hold the quota lock.
        async with get_pool().acquire() as conn, conn.transaction():
            attempt = await _owned(conn, identity.attempt_id)
            if not matches(attempt, identity):
                raise ResponseIndeterminate("generation response ownership conflicts")
            body = await conn.fetchrow(
                "SELECT * FROM generation_responses WHERE attempt_id=$1 FOR UPDATE",
                identity.attempt_id,
            )
            if body is None and attempt["phase"] == "reserved":
                return None
            if (
                body is None
                or body["state"] != "complete"
                or body["reconciliation_hold"]
                or attempt["phase"] != "closed"
                or attempt["terminal_reason"]
                not in {"completed", "preconnect", "rate_limit_refused"}
            ):
                raise ResponseIndeterminate("generation response is not confirmed")
            source = objects or PrivateObjects()
            encoded = await source.get(body["manifest_key"])
            if sha(encoded) != body["manifest_sha256"]:
                raise ResponseIndeterminate("generation response manifest conflicts")
            manifest = json.loads(encoded)
            if (
                not isinstance(manifest, dict)
                or set(manifest)
                != {
                    "version",
                    "attempt_id",
                    "route",
                    "finish_reason",
                    "chunks",
                    "bytes",
                    "sha256",
                    "chain_sha256",
                }
                or manifest["version"] != 1
                or manifest["attempt_id"] != str(identity.attempt_id)
                or manifest["sha256"] != body["response_sha256"]
                or body["response_sha256"] != attempt["response_sha256"]
                or body["manifest_key"] != attempt["response_manifest_key"]
            ):
                raise ResponseIndeterminate("generation response manifest conflicts")
            chain, count, data = _EMPTY_CHAIN, 0, bytearray()
            digest = hashlib.sha256()
            async for part in conn.cursor(
                "SELECT * FROM generation_response_chunks WHERE attempt_id=$1 AND kind='chunks' ORDER BY chunk_index",
                identity.attempt_id,
            ):
                if part["chunk_index"] != count or not part["acknowledged"]:
                    raise ResponseIndeterminate("generation response chunk order conflicts")
                expected_key = f"v1/{identity.attempt_id}/chunks/{count}-{part['sha256']}"
                if part["object_key"] != expected_key:
                    raise ResponseIndeterminate("generation response chunk binding conflicts")
                chunk = await source.get(part["object_key"])
                if len(chunk) != part["byte_length"] or sha(chunk) != part["sha256"]:
                    raise ResponseIndeterminate("generation response chunk integrity conflicts")
                record = {
                    "index": count,
                    "key": part["object_key"],
                    "sha256": part["sha256"],
                    "bytes": len(chunk),
                    "kind": "chunks",
                }
                chain = sha(bytes.fromhex(chain) + canonical(record))
                digest.update(chunk)
                data.extend(chunk)
                count += 1
            if (
                manifest["chunks"] != count
                or manifest["bytes"] != len(data)
                or manifest["sha256"] != digest.hexdigest()
                or manifest["chain_sha256"] != chain
            ):
                raise ResponseIndeterminate("generation response final integrity conflicts")
            return bytes(data)
    except ResponseIndeterminate:
        raise
    except Exception:
        raise ResponseIndeterminate("generation response recovery unavailable") from None

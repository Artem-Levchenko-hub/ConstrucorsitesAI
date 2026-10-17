"""Short quota transactions; no global lock is held during object I/O."""

from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Any
from uuid import UUID, uuid4

from yleum_gateway.core.db import get_pool
from yleum_gateway.services.generation_attempts import _owned
from yleum_gateway.services.generation_identity import GenerationProviderIdentity
from yleum_gateway.services.generation_response_store import (
    CHUNK_BYTES,
    MANIFEST_RESERVE,
    OBJECT_OVERHEAD,
    ResponseIndeterminate,
)

_INITIAL = CHUNK_BYTES + MANIFEST_RESERVE + 2 * OBJECT_OVERHEAD
# Existing attempt/journal authority, reused only for body lifetime. NULL/unknown
# outcomes never release a hold or authorize another physical provider call.
CONFIRMED_TERMINAL_SQL = (
    "a.phase='closed' AND c.receipt_hash IS NOT NULL AND ("
    "(a.terminal_reason='completed' AND c.status='completed') OR "
    "(a.terminal_reason IN ('preconnect','rate_limit_refused') AND c.status='failed'))"
)


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    bucket: str
    quota_bytes: int
    free_bytes: int
    epoch: UUID
    nonce: UUID
    expires_at: int

    def __post_init__(self) -> None:
        if (
            not self.bucket
            or any(
                type(value) is not int or value < 0 for value in (self.quota_bytes, self.free_bytes)
            )
            or self.quota_bytes == 0
            or not isinstance(self.nonce, UUID)
            or not isinstance(self.epoch, UUID)
            or type(self.expires_at) is not int
            or self.expires_at <= 0
        ):
            raise ValueError("invalid capacity snapshot")


def _now() -> float:
    return time()


def require_fresh_capacity(expires_at: int | None) -> None:
    if type(expires_at) is not int or _now() >= expires_at:
        raise ResponseIndeterminate("generation storage capacity observation expired")


def matches(row: Any, identity: GenerationProviderIdentity) -> bool:
    return row is not None and all(
        row[key] == getattr(identity, key)
        for key in ("run_id", "project_id", "user_id", "prompt_hash", "request_sha256", "version")
    )


async def validate_current_identity(identity: GenerationProviderIdentity) -> None:
    async with get_pool().acquire() as conn, conn.transaction():
        if not matches(await _owned(conn, identity.attempt_id), identity):
            raise ResponseIndeterminate("generation response ownership conflicts")


async def admit_response(
    identity: GenerationProviderIdentity, capacity: CapacitySnapshot
) -> ResponseLedger:
    token = uuid4()
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO generation_response_budgets(bucket,quota_bytes,observation_nonce,storage_epoch) "
                "VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                capacity.bucket,
                capacity.quota_bytes,
                capacity.nonce,
                capacity.epoch,
            )
            budget = await conn.fetchrow(
                "SELECT * FROM generation_response_budgets WHERE bucket=$1 FOR UPDATE",
                capacity.bucket,
            )
            if budget["storage_epoch"] != capacity.epoch:
                raise ResponseIndeterminate("generation storage epoch conflicts")
            # A duplicate can race a reader holding this attempt during S3 I/O.
            # Never retain global quota while waiting for that reader.
            attempt = await _owned(conn, identity.attempt_id, nowait=True)
            if not matches(attempt, identity) or attempt["phase"] != "reserved":
                raise ResponseIndeterminate("generation response admission conflicts")
            if await conn.fetchval(
                "SELECT 1 FROM generation_responses WHERE attempt_id=$1", identity.attempt_id
            ):
                raise ResponseIndeterminate("generation response already admitted")
            require_fresh_capacity(capacity.expires_at)
            # Dedicated bucket/sole normal writer is a provisioning prerequisite.
            # MinIO's lagged quota scanner never erases SQL-held unknown bytes.
            # Charge every allocation against the earlier physical snapshot:
            # current ACKs may postdate it and cannot grant that space again.
            quota = capacity.quota_bytes
            if (
                budget["allocated_bytes"] + _INITIAL > quota
                or budget["allocated_bytes"] + _INITIAL > capacity.free_bytes
            ):
                raise ResponseIndeterminate("generation storage capacity unavailable")
            await conn.execute(
                "UPDATE generation_response_budgets SET quota_bytes=$2,allocated_bytes=allocated_bytes+$3,"
                "observation_nonce=$4,observed_at=now() WHERE bucket=$1",
                capacity.bucket,
                quota,
                _INITIAL,
                capacity.nonce,
            )
            await conn.execute(
                "INSERT INTO generation_responses(attempt_id,bucket,writer_token,allocated_bytes) VALUES($1,$2,$3,$4)",
                identity.attempt_id,
                capacity.bucket,
                token,
                _INITIAL,
            )
        # Commit acknowledgement itself can wait. Expiry never grants dispatch
        # and never releases a reservation whose commit may have succeeded.
        require_fresh_capacity(capacity.expires_at)
        return ResponseLedger(identity.attempt_id, capacity.bucket, token, capacity.expires_at)
    except ResponseIndeterminate:
        raise
    except Exception:
        raise ResponseIndeterminate("generation storage admission was not confirmed") from None


class ResponseLedger:
    def __init__(
        self,
        attempt_id: UUID,
        bucket: str,
        writer_token: UUID,
        capacity_expires_at: int | None = None,
    ) -> None:
        self.attempt_id, self.bucket, self.writer_token = attempt_id, bucket, writer_token
        self.capacity_expires_at = capacity_expires_at

    async def _locked(self, conn: Any) -> tuple[Any, Any]:
        budget = await conn.fetchrow(
            "SELECT * FROM generation_response_budgets WHERE bucket=$1 FOR UPDATE", self.bucket
        )
        await conn.fetchrow(
            "SELECT attempt_id FROM generation_provider_attempts WHERE attempt_id=$1 FOR UPDATE",
            self.attempt_id,
        )
        body = await conn.fetchrow(
            "SELECT * FROM generation_responses WHERE attempt_id=$1 FOR UPDATE", self.attempt_id
        )
        if (
            budget is None
            or body is None
            or body["bucket"] != self.bucket
            or body["writer_token"] != self.writer_token
            or body["state"] != "uploading"
        ):
            raise ResponseIndeterminate("generation response writer conflicts")
        return budget, body

    async def reserve(self, part: dict[str, Any]) -> None:
        amount = part["bytes"] + OBJECT_OVERHEAD
        async with get_pool().acquire() as conn, conn.transaction():
            budget, body = await self._locked(conn)
            extra = max(0, body["used_bytes"] + amount - body["allocated_bytes"])
            if budget["allocated_bytes"] + extra > budget["quota_bytes"]:
                raise ResponseIndeterminate("generation storage quota unavailable")
            await conn.execute(
                "INSERT INTO generation_response_chunks(attempt_id,kind,chunk_index,object_key,sha256,byte_length) "
                "VALUES($1,$2,$3,$4,$5,$6)",
                self.attempt_id,
                part["kind"],
                part["index"],
                part["key"],
                part["sha256"],
                part["bytes"],
            )
            await conn.execute(
                "UPDATE generation_response_budgets SET allocated_bytes=allocated_bytes+$2 WHERE bucket=$1",
                self.bucket,
                extra,
            )
            await conn.execute(
                "UPDATE generation_responses SET allocated_bytes=allocated_bytes+$2,used_bytes=used_bytes+$3 WHERE attempt_id=$1",
                self.attempt_id,
                extra,
                amount,
            )

    async def acknowledge(self, part: dict[str, Any]) -> None:
        async with get_pool().acquire() as conn, conn.transaction():
            await self._locked(conn)
            changed = await conn.fetchval(
                "UPDATE generation_response_chunks SET acknowledged=true WHERE attempt_id=$1 AND kind=$2 "
                "AND chunk_index=$3 AND object_key=$4 AND sha256=$5 AND byte_length=$6 AND NOT acknowledged RETURNING 1",
                self.attempt_id,
                part["kind"],
                part["index"],
                part["key"],
                part["sha256"],
                part["bytes"],
            )
            if changed != 1:
                raise ResponseIndeterminate("generation response upload acknowledgement conflicts")
            await conn.execute(
                "UPDATE generation_responses SET stored_bytes=stored_bytes+$2 WHERE attempt_id=$1",
                self.attempt_id,
                part["bytes"] + OBJECT_OVERHEAD,
            )

    async def complete(self, manifest: dict[str, Any], key: str, digest: str) -> None:
        async with get_pool().acquire() as conn, conn.transaction():
            _, body = await self._locked(conn)
            pending = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM generation_response_chunks WHERE attempt_id=$1 AND NOT acknowledged)",
                self.attempt_id,
            )
            if pending or body["used_bytes"] != body["stored_bytes"]:
                raise ResponseIndeterminate("generation response has incomplete uploads")
            unused = body["allocated_bytes"] - body["used_bytes"]
            await conn.execute(
                "UPDATE generation_response_budgets SET allocated_bytes=allocated_bytes-$2 WHERE bucket=$1",
                self.bucket,
                unused,
            )
            await conn.execute(
                "UPDATE generation_responses SET state='complete',allocated_bytes=used_bytes,manifest_key=$2,"
                "manifest_sha256=$3,response_sha256=$4 WHERE attempt_id=$1",
                self.attempt_id,
                key,
                digest,
                manifest["sha256"],
            )
            await conn.execute(
                "UPDATE generation_provider_attempts SET response_manifest_key=$2,response_sha256=$3 WHERE attempt_id=$1",
                self.attempt_id,
                key,
                manifest["sha256"],
            )

    async def indeterminate(self) -> None:
        async with get_pool().acquire() as conn, conn.transaction():
            await self._locked(conn)
            await conn.execute(
                "UPDATE generation_responses SET state='indeterminate' WHERE attempt_id=$1",
                self.attempt_id,
            )

    async def reconciled(self) -> None:
        # Called only after the existing route's full journal+wallet chain returns.
        async with get_pool().acquire() as conn, conn.transaction():
            await conn.fetchrow(
                "SELECT bucket FROM generation_response_budgets WHERE bucket=$1 FOR UPDATE",
                self.bucket,
            )
            changed = await conn.fetchval(
                "UPDATE generation_responses b SET reconciliation_hold=false,reconciled_at=now() "
                "FROM generation_provider_attempts a JOIN provider_calls c ON c.id=a.provider_call_id "
                "WHERE b.attempt_id=$1 AND b.writer_token=$2 AND b.state='complete' AND a.attempt_id=b.attempt_id "
                "AND " + CONFIRMED_TERMINAL_SQL + " RETURNING 1",
                self.attempt_id,
                self.writer_token,
            )
            if changed != 1:
                raise ResponseIndeterminate("generation response accounting remains unresolved")

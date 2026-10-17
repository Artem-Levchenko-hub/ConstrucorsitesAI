"""Sole body cleanup owner. Unknown outcomes retain bytes and all financial rows."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.services.generation_response_ledger import CONFIRMED_TERMINAL_SQL
from yleum_gateway.services.generation_response_objects import PrivateObjects
from yleum_gateway.services.generation_response_store import ResponseIndeterminate


async def _claim(attempt_id: UUID, bucket: str, now: datetime, ttl: int) -> bool:
    async with get_pool().acquire() as conn, conn.transaction():
        await conn.fetchrow(
            "SELECT bucket FROM generation_response_budgets WHERE bucket=$1 FOR UPDATE", bucket
        )
        # Never hold global quota waiting on a reader doing S3 I/O.
        await conn.fetchrow(
            "SELECT attempt_id FROM generation_provider_attempts WHERE attempt_id=$1 FOR UPDATE NOWAIT",
            attempt_id,
        )
        body = await conn.fetchrow(
            "SELECT * FROM generation_responses WHERE attempt_id=$1 FOR UPDATE NOWAIT", attempt_id
        )
        if (
            body is None
            or body["state"] not in {"complete", "deleting"}
            or body["reconciliation_hold"]
        ):
            return False
        terminal = await conn.fetchrow(
            "SELECT r.finished_at,c.receipt_hash FROM generation_provider_attempts a "
            "JOIN generation_runs r ON r.id=a.run_id JOIN provider_calls c ON c.id=a.provider_call_id "
            "WHERE a.attempt_id=$1 AND " + CONFIRMED_TERMINAL_SQL + " "
            "AND r.status IN ('completed','failed','cancelled') AND r.finished_at IS NOT NULL",
            attempt_id,
        )
        if terminal is None or body["reconciled_at"] is None:
            return False
        eligible = await conn.fetchval(
            "UPDATE generation_responses SET retain_until=GREATEST($2::timestamptz,reconciled_at) "
            "+ make_interval(secs=>$3::double precision) WHERE attempt_id=$1 RETURNING retain_until<=$4",
            attempt_id,
            terminal["finished_at"],
            ttl,
            now,
        )
        if not eligible:
            return False
        await conn.execute(
            "UPDATE generation_responses SET state='deleting' WHERE attempt_id=$1", attempt_id
        )
        return True


async def delete_expired_generation_responses(now: datetime, *, objects: Any = None) -> int:
    settings = get_settings()
    if (
        not settings.generation_response_cleanup_enabled
        or not settings.generation_response_retention_enabled
    ):
        return 0
    ttl = settings.generation_response_retention_seconds
    if type(ttl) is not int or ttl <= 0:
        raise ResponseIndeterminate("invalid generation response retention interval")
    async with get_pool().acquire() as conn:
        candidates = await conn.fetch(
            "SELECT b.attempt_id,b.bucket FROM generation_responses b JOIN generation_provider_attempts a "
            "ON a.attempt_id=b.attempt_id JOIN generation_runs r ON r.id=a.run_id "
            "JOIN provider_calls c ON c.id=a.provider_call_id "
            "WHERE b.state IN ('complete','deleting') AND NOT b.reconciliation_hold "
            "AND b.reconciled_at IS NOT NULL AND r.finished_at IS NOT NULL "
            "AND r.status IN ('completed','failed','cancelled') AND " + CONFIRMED_TERMINAL_SQL + " "
            "AND GREATEST(r.finished_at,b.reconciled_at)+make_interval(secs=>$2::double precision)<=$1 "
            "ORDER BY b.created_at LIMIT 50",
            now,
            ttl,
        )
    deleted = 0
    for candidate in candidates:
        attempt_id, bucket = candidate["attempt_id"], candidate["bucket"]
        try:
            if not await _claim(attempt_id, bucket, now, ttl):
                continue
            source = objects or PrivateObjects()
            if objects is None and source.bucket != bucket:
                raise ResponseIndeterminate("generation storage bucket changed")
            # Durable deleting tombstone blocks readers/new holds while S3 work
            # runs with no budget/attempt lock. Repeated exact deletes are safe.
            async with get_pool().acquire() as conn, conn.transaction():
                async for row in conn.cursor(
                    "SELECT object_key,kind,chunk_index,sha256 FROM generation_response_chunks WHERE attempt_id=$1",
                    attempt_id,
                ):
                    expected = f"v1/{attempt_id}/{row['kind']}/{row['chunk_index']}-{row['sha256']}"
                    if row["object_key"] != expected:
                        raise ResponseIndeterminate(
                            "generation response deletion binding conflicts"
                        )
                    await source.delete(expected)
            async with get_pool().acquire() as conn, conn.transaction():
                await conn.fetchrow(
                    "SELECT bucket FROM generation_response_budgets WHERE bucket=$1 FOR UPDATE",
                    bucket,
                )
                await conn.fetchrow(
                    "SELECT attempt_id FROM generation_provider_attempts WHERE attempt_id=$1 FOR UPDATE",
                    attempt_id,
                )
                body = await conn.fetchrow(
                    "SELECT * FROM generation_responses WHERE attempt_id=$1 FOR UPDATE", attempt_id
                )
                if body["state"] == "deleted":
                    continue
                if body["state"] != "deleting" or body["reconciliation_hold"]:
                    raise ResponseIndeterminate("generation response deletion conflicts")
                # Chunk rows are body storage, not financial evidence. Reclaim
                # their overhead only after every object delete was confirmed.
                await conn.execute(
                    "DELETE FROM generation_response_chunks WHERE attempt_id=$1", attempt_id
                )
                await conn.execute(
                    "UPDATE generation_response_budgets SET allocated_bytes=allocated_bytes-$2 WHERE bucket=$1",
                    bucket,
                    body["allocated_bytes"],
                )
                await conn.execute(
                    "UPDATE generation_responses SET state='deleted',allocated_bytes=0,used_bytes=0,stored_bytes=0 WHERE attempt_id=$1",
                    attempt_id,
                )
            deleted += 1
        except Exception:
            # Lock contention, unknown delete/commit, or unavailable storage:
            # keep tombstone/reservations for the next same-namespace sweep.
            continue
    return deleted


async def cleanup_loop() -> None:
    while True:
        with suppress(Exception):
            await delete_expired_generation_responses(datetime.now(UTC))
        await asyncio.sleep(60)

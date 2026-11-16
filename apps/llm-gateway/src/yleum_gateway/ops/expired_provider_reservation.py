"""One-shot settlement of a selected expired provider reservation from stdin."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys

from asyncpg import Connection

from yleum_gateway.core import db
from yleum_gateway.services.generation_attempts import (
    ExpiredReservationScope,
    ReleaseExpiredReservationScope,
    close_expired_before_dispatch,
    close_released_before_dispatch,
)
from yleum_gateway.services.generation_identity import GenerationProviderIdentity, decode_request

_ACTION = "close_expired_before_dispatch"
_RELEASED_ACTION = "close_released_before_dispatch"
_HEAD = "0107_expired_provider_reservation"
_MAX_INPUT_BYTES = 4096
_FIELDS = {
    "version", "action", "release_sha", "reader_release_sha", "state_revision", "source_sha256",
    "identity",
}
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CHECK_PREFIX = "ck_generation_provider_attempts_ck_generation_attempt_"
_LOCAL_REASONS = (
    "COALESCE((terminal_reason = ANY (ARRAY['native_transcript_rejected'::text, "
    "'expired_before_dispatch'::text])), false)"
)
_FINANCIAL_REASONS = (
    "(terminal_reason = ANY (ARRAY['completed'::text, 'preconnect'::text, "
    "'rate_limit_refused'::text, 'upstream_refused'::text]))"
)
_EXPECTED_CHECKS = {
    _CHECK_PREFIX + "call": (
        "CHECK ((((phase = 'reserved'::text) OR ((phase = 'closed'::text) AND "
        f"{_LOCAL_REASONS})) = (provider_call_id IS NULL)))"
    ),
    _CHECK_PREFIX + "terminal": (
        "CHECK (((terminal_reason IS NULL) OR ((phase = 'closed'::text) AND "
        f"({_FINANCIAL_REASONS} OR ({_LOCAL_REASONS} AND (response_manifest_key IS NULL) "
        "AND (response_sha256 IS NULL))))))"
    ),
}


def _scope(
    raw: bytes, launch_sha: str | None,
) -> ExpiredReservationScope | ReleaseExpiredReservationScope:
    if not 0 < len(raw) <= _MAX_INPUT_BYTES or b"\0" in raw or raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("invalid settlement scope")
    raw.decode("utf-8")
    value = decode_request(raw)
    released = value.get("action") == _RELEASED_ACTION
    fields = _FIELDS | {"context_sha256"} if released else _FIELDS
    if (
        set(value) != fields
        or type(value["version"]) is not int or value["version"] != 1
        or value["action"] != (_RELEASED_ACTION if released else _ACTION)
        or type(value["state_revision"]) is not int or value["state_revision"] < 0
        or type(value["source_sha256"]) is not str
        or _SHA256.fullmatch(value["source_sha256"]) is None
    ):
        raise ValueError("invalid settlement scope")
    release, incumbent = value["release_sha"], value["reader_release_sha"]
    if (
        any(type(sha) is not str or _GIT_SHA.fullmatch(sha) is None or sha == "0" * 40
            for sha in (release, incumbent))
        or release == incumbent or release != launch_sha
    ):
        raise ValueError("invalid settlement release")
    identity = GenerationProviderIdentity.from_wire(value["identity"])
    if any(item.int == 0 for item in (
        identity.run_id, identity.project_id, identity.user_id, identity.attempt_id,
    )):
        raise ValueError("invalid settlement identity")
    if released:
        context = value["context_sha256"]
        if type(context) is not str or _SHA256.fullmatch(context) is None:
            raise ValueError("invalid settlement context")
        return ReleaseExpiredReservationScope(
            identity=identity, release_sha=release, state_revision=value["state_revision"],
            context_sha256=context, source_sha256=value["source_sha256"],
        )
    return ExpiredReservationScope(
        identity=identity, release_sha=release, state_revision=value["state_revision"],
        source_sha256=value["source_sha256"],
    )


async def _exact_schema(connection: Connection) -> None:
    if await connection.fetchval("SELECT current_schema()") != "public":
        raise ValueError("settlement schema is unconfirmed")
    # Hold the head and definitions stable across the writer's row transaction.
    await connection.execute("LOCK TABLE public.alembic_version IN SHARE MODE NOWAIT")
    await connection.execute(
        "LOCK TABLE public.generation_provider_attempts IN ACCESS SHARE MODE NOWAIT",
    )
    heads = await connection.fetch("SELECT version_num FROM public.alembic_version")
    if [row["version_num"] for row in heads] != [_HEAD]:
        raise ValueError("settlement migration head is unconfirmed")
    rows = await connection.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        "WHERE conrelid='public.generation_provider_attempts'::regclass "
        "AND contype='c' AND convalidated AND conname=ANY($1::text[])",
        list(_EXPECTED_CHECKS),
    )
    if {row["conname"]: row["definition"] for row in rows} != _EXPECTED_CHECKS:
        raise ValueError("settlement constraints are unconfirmed")


async def _settle(scope: ExpiredReservationScope | ReleaseExpiredReservationScope) -> str:
    async with asyncio.timeout(45):
        pool = await db.init_pool()
        try:
            async with pool.acquire() as connection, connection.transaction():
                await _exact_schema(connection)
                if isinstance(scope, ReleaseExpiredReservationScope):
                    receipt = await close_released_before_dispatch(scope)
                else:
                    receipt = await close_expired_before_dispatch(scope)
        finally:
            await db.close_pool()
    return hashlib.sha256(json.dumps(
        receipt, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise ValueError("unexpected settlement arguments")
        scope = _scope(sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1), os.getenv("OMNIA_RELEASE_SHA"))
        receipt_sha256 = asyncio.run(_settle(scope))
    except Exception:
        # Parsing, configuration and database errors may contain private values.
        print(json.dumps({"result": "not_confirmed"}))
        return 3
    print(json.dumps({"result": "settled", "receipt_sha256": receipt_sha256}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

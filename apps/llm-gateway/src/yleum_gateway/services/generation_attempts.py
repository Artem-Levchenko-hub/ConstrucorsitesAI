"""Durable dispatch control; no billing, response retention or replay authority."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.core.errors import GatewayError
from yleum_gateway.services import provider_calls
from yleum_gateway.services.generation_identity import (
    GenerationProviderIdentity,
    decode_request,
    request_digest,
)


class AttemptConflict(GatewayError):
    """Dispatch was not confirmed; never fall through to legacy provider admission."""

    code = "generation_attempt_unconfirmed"
    http_status = 409


@dataclass(frozen=True, slots=True)
class AttemptAdmission:
    action: Literal["dispatch", "stored", "pending", "conflict"]
    dispatch_token: UUID | None = None
    provider_call_id: UUID | None = None
    response_manifest_key: str | None = None


class _ObjectPairs(list[tuple[str, Any]]):
    """Keep every object key while distinguishing JSON objects from arrays."""


def _contains_identity(raw: bytes) -> bool:
    # Decode keys (including Unicode escapes), retaining ALL top-level metadata
    # occurrences. A later duplicate cannot erase the reserved protocol marker.
    body = json.loads(raw, object_pairs_hook=_ObjectPairs)
    return isinstance(body, _ObjectPairs) and any(
        key == "metadata" and isinstance(value, _ObjectPairs)
        and any(field == "generation_provider_identity" for field, _ in value)
        for key, value in body
    )


def parse_attempt(
    raw: bytes, route: str, *, require_identity: bool = False,
) -> GenerationProviderIdentity | None:
    try:
        if not require_identity and not _contains_identity(raw):
            return None  # Preserve ordinary identity-absent JSON semantics.
        body = decode_request(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise AttemptConflict("generation request is not canonical") from None
    metadata = body.get("metadata")
    if not isinstance(metadata, dict) or "generation_provider_identity" not in metadata:
        if require_identity:
            raise AttemptConflict("authenticated generation identity is required")
        return None
    if not get_settings().generation_provider_attempts_enabled:
        raise AttemptConflict("generation attempt protocol is disabled")
    try:
        identity = GenerationProviderIdentity.from_wire(metadata["generation_provider_identity"])
        if request_digest(route, body) != identity.request_sha256:
            raise ValueError
        if (
            metadata.get("run_id") != str(identity.run_id)
            or (metadata.get("project_id") != str(identity.project_id))
            or metadata.get("user_id") not in {None, str(identity.user_id)}
            or (body.get("user") not in {None, str(identity.user_id)})
        ):
            raise ValueError
        return identity
    except (ValueError, TypeError):
        raise AttemptConflict("generation attempt binding conflicts") from None


async def _owned(conn: Any, attempt_id: UUID, *, nowait: bool = False) -> Any:
    # Lock only dispatch control. API callers can hold project/run row locks while
    # awaiting HTTP; MVCC current committed owner checks must not deadlock them.
    return await conn.fetchrow(
        "SELECT a.* FROM generation_provider_attempts a "
        "JOIN generation_runs r ON r.id=a.run_id "
        "JOIN projects p ON p.id=a.project_id "
        "WHERE a.attempt_id=$1 AND r.project_id=a.project_id AND r.user_id=a.user_id "
        "AND p.owner_id=a.user_id AND r.prompt_hash=a.prompt_hash AND r.status='running' "
        "AND a.version=1 FOR UPDATE OF a" + (" NOWAIT" if nowait else ""),
        attempt_id,
    )


async def reserve_generation_attempt(identity: GenerationProviderIdentity) -> AttemptAdmission:
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            row = await _owned(conn, identity.attempt_id)
            if row is None or any(
                row[key] != getattr(identity, key)
                for key in (
                    "run_id",
                    "project_id",
                    "user_id",
                    "prompt_hash",
                    "request_sha256",
                    "version",
                )
            ):
                return AttemptAdmission("conflict")
            if row["phase"] != "reserved":
                # Task8 alone can verify and serve retained private body bytes.
                return AttemptAdmission("pending", provider_call_id=row["provider_call_id"])
            token = uuid4()
            await conn.execute(
                "UPDATE generation_provider_attempts SET dispatch_token=$2, "
                "updated_at=now() WHERE attempt_id=$1",
                identity.attempt_id,
                token,
            )
        return AttemptAdmission("dispatch", dispatch_token=token)
    except Exception:
        raise AttemptConflict("generation attempt admission unavailable") from None


async def mark_attempt_dispatching(
    attempt_id: UUID,
    *,
    dispatch_token: UUID,
    route: str,
    model: str,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
    run_id: UUID | None = None,
    message_id: UUID | None = None,
    stage: str | None = None,
    free: bool = False,
    provider_call_id: UUID | None = None,
    provider_scope: str = "llmgw",
) -> UUID:
    call = provider_call_id or uuid4()
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            row = await _owned(conn, attempt_id)
            if row is None or row["phase"] != "reserved" or row["dispatch_token"] != dispatch_token:
                raise AttemptConflict("generation attempt binding conflicts")
            if (
                row["run_id"] != run_id
                or row["project_id"] != project_id
                or (user_id is not None and row["user_id"] != user_id)
            ):
                raise AttemptConflict("generation attempt binding conflicts")
            await provider_calls.insert_call(
                conn,
                call,
                route=route,
                model=model,
                user_id=user_id,
                project_id=project_id,
                run_id=run_id,
                message_id=message_id,
                stage=stage,
                free=free,
                provider_scope=provider_scope,
            )
            changed = await conn.execute(
                "UPDATE generation_provider_attempts SET phase='dispatching',provider_call_id=$3,"
                "updated_at=now() WHERE attempt_id=$1 AND dispatch_token=$2 AND phase='reserved'",
                attempt_id,
                dispatch_token,
                call,
            )
            if changed != "UPDATE 1":
                raise AttemptConflict("generation attempt binding conflicts")
        # The only permission to POST exists AFTER successful commit. Unknown
        # commit outcomes raise; reconnecting or elapsed time never grants it.
        return call
    except Exception:
        raise AttemptConflict("generation dispatch was not confirmed") from None


async def dispatch(identity: GenerationProviderIdentity | None, **kwargs: Any) -> UUID:
    from yleum_gateway.services.generation_response_capture import before_dispatch

    await before_dispatch(identity)
    if identity is None:
        return await provider_calls.start_call(**kwargs)
    admitted = await reserve_generation_attempt(identity)
    if admitted.action != "dispatch" or admitted.dispatch_token is None:
        raise AttemptConflict("generation attempt is pending or conflicting")
    return await mark_attempt_dispatching(
        identity.attempt_id, dispatch_token=admitted.dispatch_token, **kwargs
    )


async def close_attempt(
    call_id: UUID,
    *,
    reason: Literal["completed", "preconnect", "rate_limit_refused", "upstream_refused"] | None,
) -> None:
    """Record control evidence only AFTER the existing financial receipt committed."""
    if reason not in (None, "completed", "preconnect", "rate_limit_refused", "upstream_refused"):
        raise AttemptConflict("generation terminal reason is unsupported")
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT a.attempt_id,a.phase,a.terminal_reason,c.status,c.receipt_hash "
                "FROM generation_provider_attempts a JOIN provider_calls c ON c.id=a.provider_call_id "
                "WHERE a.provider_call_id=$1 FOR UPDATE OF a",
                call_id,
            )
            if row is None:
                raise AttemptConflict("generation attempt binding conflicts")
            wanted = "completed" if reason == "completed" else "failed"
            if reason is not None and (row["status"] != wanted or not row["receipt_hash"]):
                raise AttemptConflict("generation attempt binding conflicts")
            phase = "closed" if reason else "indeterminate"
            if row["phase"] != "dispatching":
                if row["phase"] == phase and row["terminal_reason"] == reason:
                    return
                raise AttemptConflict("generation attempt binding conflicts")
            await conn.execute(
                "UPDATE generation_provider_attempts SET phase=$2,terminal_reason=$3,"
                "updated_at=now() WHERE attempt_id=$1",
                row["attempt_id"],
                phase,
                reason,
            )
    except Exception:
        raise AttemptConflict("generation terminal receipt was not confirmed") from None


async def finish(
    identity: GenerationProviderIdentity | None,
    call_id: UUID,
    *,
    reason: Literal["preconnect", "rate_limit_refused", "upstream_refused"] | None = None,
    **receipt: Any,
) -> None:
    """The existing journal commits first; only then may a safe retry be advertised."""
    await provider_calls.finish_call(call_id, **receipt)
    if identity is not None:
        await close_attempt(
            call_id,
            reason=("completed" if receipt.get("status", "completed") == "completed" else reason),
        )

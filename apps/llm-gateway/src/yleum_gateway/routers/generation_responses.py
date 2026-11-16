"""Private authenticated response reads and bounded journal reconciliation."""

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.generation_response_auth import (
    HEADER,
    verify_expired_reservation_token,
    verify_generation_response_token,
)
from yleum_gateway.services.generation_attempts import (
    AttemptConflict,
    RuntimeExpiredReservationScope,
    close_runtime_expired_before_dispatch,
    native_transcript_rejection_details,
)
from yleum_gateway.services.generation_response_store import (
    load_generation_response,
    reconcile_generation_response,
)

router = APIRouter(prefix="/_internal/generation-responses", tags=["internal"])


@router.post("/{attempt_id}/reconcile")
async def reconcile_response(attempt_id: UUID, request: Request) -> dict[str, Any]:
    if not get_settings().generation_response_retention_enabled:
        raise HTTPException(503, "generation response retention is disabled")
    headers = request.headers.getlist(HEADER)
    if len(headers) != 1:
        raise HTTPException(403, "generation response authentication required")
    try:
        identity = verify_generation_response_token(
            headers[0], purpose="generation_response.reconcile",
        )
        if identity.attempt_id != attempt_id:
            raise ValueError
    except ValueError:
        raise HTTPException(403, "generation response authentication denied") from None
    try:
        confirmed = await reconcile_generation_response(identity)
    except Exception:
        raise HTTPException(409, "generation response reconciliation is indeterminate") from None
    return {
        "version": 1, "identity": identity.to_wire(),
        "state": "confirmed" if confirmed else "pending",
    }


@router.post("/{attempt_id}/expire")
async def expire_reservation(attempt_id: UUID, request: Request) -> dict[str, Any]:
    settings = get_settings()
    if not (settings.generation_provider_attempts_enabled
            and settings.generation_response_retention_enabled):
        raise HTTPException(503, "generation expiry is disabled")
    headers = request.headers.getlist(HEADER)
    if len(headers) != 1:
        raise HTTPException(403, "generation response authentication required")
    try:
        identity, revision, context, source = verify_expired_reservation_token(headers[0])
        if identity.attempt_id != attempt_id:
            raise ValueError
    except ValueError:
        raise HTTPException(403, "generation response authentication denied") from None
    try:
        receipt = await close_runtime_expired_before_dispatch(
            RuntimeExpiredReservationScope(identity, revision, context, source),
        )
    except AttemptConflict:
        raise HTTPException(409, "generation expiry is unconfirmed") from None
    return {
        "version": 1, "identity": identity.to_wire(),
        "state": "expired_before_dispatch", "receipt": receipt,
    }


@router.get("/{attempt_id}")
async def generation_response(attempt_id: UUID, request: Request) -> dict[str, Any]:
    if not get_settings().generation_response_retention_enabled:
        raise HTTPException(503, "generation response retention is disabled")
    headers = request.headers.getlist(HEADER)
    if len(headers) != 1:
        raise HTTPException(403, "generation response authentication required")
    try:
        identity = verify_generation_response_token(headers[0], purpose="generation_response.read")
        if identity.attempt_id != attempt_id:
            raise ValueError
    except ValueError:
        raise HTTPException(403, "generation response authentication denied") from None
    try:
        data = await load_generation_response(identity)
        result: dict[str, Any] = {"version": 1, "identity": identity.to_wire(), "state": "pending"}
        if data is None:
            return result
        if data == "rejected":
            result.update(state="rejected", error={
                "type": "invalid_request_error", "details": native_transcript_rejection_details(),
            })
            return result
        lines = data.splitlines()
        first = json.loads(lines[0])
        if (
            not isinstance(first, dict)
            or first.get("identity") != identity.to_wire()
            or first.get("version") != 1
        ):
            raise ValueError
        if first.get("format") == "sse":
            events = [json.loads(line) for line in lines[1:]]
            if not events or events[-1] != "[DONE]":
                raise ValueError
            response = {"format": "sse", "events": events}
        elif first.get("format") == "json" and len(lines) == 1:
            response = {"format": "json", "status": first["status"], "body": first["body"]}
        else:
            raise ValueError
        result.update(state="stored", response=response)
        return result
    except Exception:
        raise HTTPException(409, "generation response recovery is indeterminate") from None

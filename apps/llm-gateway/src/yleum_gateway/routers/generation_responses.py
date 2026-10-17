"""Private authenticated reads, with current SQL ownership before any object access."""

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.generation_response_auth import HEADER, verify_generation_response_token
from yleum_gateway.services.generation_response_store import load_generation_response

router = APIRouter(prefix="/_internal/generation-responses", tags=["internal"])


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

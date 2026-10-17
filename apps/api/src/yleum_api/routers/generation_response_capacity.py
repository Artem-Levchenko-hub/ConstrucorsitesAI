"""Authenticated, allowlisted storage admission observations; never raw admin data."""

from fastapi import APIRouter, HTTPException, Request

from yleum_api.services.generation.response_auth import (
    CAPACITY_HEADER,
    sign_capacity_snapshot,
    verify_capacity_token,
)
from yleum_api.services.generation.response_capacity import current_capacity

router = APIRouter(prefix="/api/_internal", tags=["internal"])


@router.post("/generation-response-capacity")
async def generation_response_capacity(request: Request) -> dict[str, str]:
    headers = request.headers.getlist(CAPACITY_HEADER)
    if len(headers) != 1:
        raise HTTPException(403, "capacity authentication required")
    try:
        identity, nonce = verify_capacity_token(headers[0])
    except ValueError:
        raise HTTPException(403, "capacity authentication denied") from None
    try:
        values = await current_capacity(identity)
        return {"assertion": sign_capacity_snapshot(values, identity, nonce)}
    except Exception:
        raise HTTPException(503, "capacity unavailable") from None

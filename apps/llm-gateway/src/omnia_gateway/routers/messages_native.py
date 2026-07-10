"""Native Anthropic Messages passthrough — ``POST /v1/messages``.

Forwards a RAW Anthropic Messages request (``tools``, ``tool_choice``,
``thinking``, and ``thinking`` / ``tool_use`` / ``tool_result`` content blocks) to
oneprovider's DOCUMENTED Anthropic-native surface
(``https://api.oneprovider.dev/v1/messages``, ``x-api-key``) and returns the
upstream JSON UNCHANGED — thinking-block ``signature`` and ``stop_reason`` intact.
The native tool-use agent (apps/api) MUST echo those signatures back verbatim
across tool turns, so nothing here may reshape the body.

Transport: a sync ``httpx.Client`` on a worker thread with ``trust_env=False`` + a
no-op mounts transport, so the container's ``HTTPS_PROXY`` (a Gemini geo-bypass)
never tunnels the reseller endpoint and the ``AsyncClient`` TLS stall in the
long-lived uvicorn loop is avoided.

Pure passthrough — billing is added by the native-agent caller (it parses
``usage`` from the returned body). Internal-only endpoint (same network as the
other gateway routes), so no auth here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Request, Response

from omnia_gateway.services.model_router import native_messages_route

log = structlog.get_logger(__name__)
router = APIRouter()

_ANTHROPIC_VERSION = "2023-06-01"
_TIMEOUT_S = 240.0


def _err(status: int, err_type: str, message: str) -> Response:
    return Response(
        content=json.dumps(
            {"type": "error", "error": {"type": err_type, "message": message}}
        ),
        status_code=status,
        media_type="application/json",
    )


@router.post("/v1/messages")
async def native_messages(request: Request) -> Response:
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return _err(400, "invalid_request_error", "body is not valid JSON")

    model = body.get("model", "") if isinstance(body, dict) else ""

    route = native_messages_route()
    if route is None:
        return _err(
            400,
            "invalid_request_error",
            "ONEPROVIDER_API_KEY is not configured for the native /v1/messages upstream",
        )
    api_key, api_base = route
    url = f"{api_base.rstrip('/')}/v1/messages"

    headers = {
        "x-api-key": api_key,
        "anthropic-version": request.headers.get(
            "anthropic-version", _ANTHROPIC_VERSION
        ),
        "content-type": "application/json",
    }
    # Forward an anthropic-beta opt-in verbatim if the caller set one (e.g.
    # interleaved thinking) — the agent decides, not the gateway.
    beta = request.headers.get("anthropic-beta")
    if beta:
        headers["anthropic-beta"] = beta

    def _post() -> httpx.Response:
        with httpx.Client(
            timeout=httpx.Timeout(_TIMEOUT_S, connect=30.0),
            trust_env=False,
            mounts={"all://": httpx.HTTPTransport()},
        ) as client:
            return client.post(url, json=body, headers=headers)

    try:
        upstream = await asyncio.to_thread(_post)
    except httpx.HTTPError as exc:
        log.warning("native_messages.transport_error", model=model, error=str(exc))
        return _err(502, "api_error", f"upstream transport: {type(exc).__name__}")

    # Return the upstream response verbatim — content blocks (incl. thinking
    # signatures) and stop_reason must survive untouched.
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )

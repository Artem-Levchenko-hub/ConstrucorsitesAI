"""Native Anthropic Messages passthrough — ``POST /v1/messages``.

The OpenAI-shape ``/v1/chat/completions`` (LiteLLM Router) normalizes the response
and DROPS the Anthropic thinking-block ``signature`` — which the native tool-use
agent (apps/api) MUST echo back verbatim across tool turns, or Anthropic 400s
("thinking blocks ... cannot be modified"). This endpoint forwards a RAW Anthropic
Messages request (``tools``, ``tool_choice``, ``thinking``, and ``thinking`` /
``tool_use`` / ``tool_result`` content blocks) to the model's upstream
(``claude-opus-4-8`` → oneprovider, key/base from ``_PROXY_ROUTES``) and returns the
upstream JSON UNCHANGED — signatures intact, ``stop_reason`` intact.

Transport mirrors ``providers/vsegpt.py`` / ``providers/sber.py``: a sync
``httpx.Client`` on a worker thread with ``trust_env=False`` + a no-op mounts
transport, so (1) the container's ``HTTPS_PROXY`` (UK egress for Gemini) never
tunnels the reseller endpoint, and (2) the intermittent ``AsyncClient`` TLS stall in
the long-lived uvicorn loop is avoided.

Pure passthrough — billing is added by the native-agent caller (it parses ``usage``
from the returned body). Internal-only endpoint (same network as the other gateway
routes), so no auth here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Request, Response

from omnia_gateway.core.config import get_settings
from omnia_gateway.providers.vsegpt import is_vsegpt_model
from omnia_gateway.providers.vsegpt_native import anative_messages
from omnia_gateway.services.litellm_router import proxy_route_for

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

    # Owner 2026-07-01: the native agent rides vsegpt too — same ~3s no-thinking
    # Opus as /v1/chat/completions. vsegpt has no /v1/messages, so the adapter
    # converts Anthropic⇄OpenAI shapes (providers/vsegpt_native.py). Upstream
    # status codes pass through verbatim (the agent retries 429 itself).
    # Kill switch NATIVE_VIA_VSEGPT=false → the oneprovider passthrough below.
    settings = get_settings()
    if (
        settings.native_via_vsegpt
        and settings.vsegpt_api_key
        and is_vsegpt_model(model)
    ):
        try:
            status, out = await anative_messages(body)
        except httpx.HTTPError as exc:
            log.warning(
                "native_messages.vsegpt_transport_error", model=model, error=str(exc)
            )
            return _err(502, "api_error", f"vsegpt transport: {type(exc).__name__}")
        return Response(
            content=json.dumps(out, ensure_ascii=False),
            status_code=status,
            media_type="application/json",
        )

    route = proxy_route_for(model)
    if route is None:
        return _err(
            400,
            "invalid_request_error",
            f"model {model!r} has no native /v1/messages upstream configured",
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

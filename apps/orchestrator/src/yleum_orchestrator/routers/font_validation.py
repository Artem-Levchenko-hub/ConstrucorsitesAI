"""Authenticated, bounded parser RPC, independent of project generation leases."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request

from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.core.internal_auth import verify_internal_token
from yleum_orchestrator.services.font_validation import (
    MAX_INPUT,
    FontValidationCleanupUnconfirmed,
    reconcile_font_validation,
    run_font_validation,
)

router = APIRouter(prefix="/internal", tags=["font-validation"])
_slot = asyncio.Semaphore(1)
_admission_lock = asyncio.Lock()
_quarantine: FontValidationCleanupUnconfirmed | None = None


async def _claim_slot() -> None:
    global _quarantine
    async with _admission_lock:
        if _quarantine is not None:
            if not await asyncio.to_thread(reconcile_font_validation, _quarantine):
                raise OrchestratorError("container_failure", "Font validator cleanup pending", 503)
            _quarantine = None
            _slot.release()
        if _slot.locked():
            raise OrchestratorError(
                "container_failure", "Font validator is busy; retry shortly", 503
            )
        await _slot.acquire()


async def _read_input(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_INPUT:
            raise OrchestratorError("validation_failed", "Font input too large", 422)
    if not data:
        raise OrchestratorError("validation_failed", "Font input is empty", 422)
    return bytes(data)


@router.post("/font-validation")
async def validate_font(
    request: Request,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    global _quarantine
    verify_internal_token(x_internal_token)
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/octet-stream":
        raise OrchestratorError("validation_failed", "Expected binary font input", 422)
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            valid_length = 0 < int(declared) <= MAX_INPUT
        except ValueError:
            valid_length = False
        if not valid_length:
            raise OrchestratorError("validation_failed", "Unsupported font input size", 422)
    await _claim_slot()
    release_slot = True
    try:
        try:
            raw = await asyncio.wait_for(_read_input(request), timeout=10)
        except TimeoutError:
            raise OrchestratorError("validation_failed", "Font input timed out", 422) from None
        job = asyncio.create_task(asyncio.to_thread(run_font_validation, raw))
        try:
            return await asyncio.shield(job)
        except FontValidationCleanupUnconfirmed as failure:
            _quarantine, release_slot = failure, False
            raise
        except asyncio.CancelledError:
            # Retain the slot until the exact helper has ended and been removed.
            while not job.done():
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not job.cancelled():
                cleanup_failure = job.exception()
                if isinstance(cleanup_failure, FontValidationCleanupUnconfirmed):
                    _quarantine, release_slot = cleanup_failure, False
            raise
    finally:
        if release_slot:
            _slot.release()

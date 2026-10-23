"""One gateway-owned receive/store/settle task, independent of HTTP subscribers."""

from __future__ import annotations

import asyncio
import contextvars
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException, Request
from sse_starlette.sse import EventSourceResponse
from starlette.responses import JSONResponse, Response

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.generation_response_auth import (
    CAPACITY_HEADER,
    HEADER,
    mint_capacity_token,
    verify_capacity_snapshot,
    verify_generation_response_token,
)
from yleum_gateway.services.generation_attempts import parse_attempt
from yleum_gateway.services.generation_identity import GenerationProviderIdentity
from yleum_gateway.services.generation_response_ledger import (
    CapacitySnapshot,
    ResponseLedger,
    admit_response,
    require_fresh_capacity,
    validate_current_identity,
)
from yleum_gateway.services.generation_response_objects import PrivateObjects
from yleum_gateway.services.generation_response_store import (
    ChunkWriter,
    ResponseIndeterminate,
    canonical,
)

_TASKS: set[asyncio.Task[None]] = set()
_CURRENT: contextvars.ContextVar[Capture | None] = contextvars.ContextVar(
    "response_capture", default=None
)


async def preflight_response_store(
    identity: GenerationProviderIdentity,
) -> tuple[PrivateObjects, ResponseLedger]:
    settings = get_settings()
    await validate_current_identity(identity)  # No object/admin access before owned SQL binding.
    nonce = str(uuid4())
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10, connect=2), follow_redirects=False
    ) as client:
        response = await client.post(
            settings.generation_response_capacity_url,
            headers={CAPACITY_HEADER: mint_capacity_token(identity, nonce)},
        )
    if response.status_code != 200:
        raise ResponseIndeterminate("generation storage capacity unavailable")
    payload = response.json()
    if not isinstance(payload, dict) or set(payload) != {"assertion"}:
        raise ResponseIndeterminate("generation storage capacity unavailable")
    values = verify_capacity_snapshot(payload["assertion"], identity, nonce)
    if (
        values["bucket"] != settings.generation_response_s3_bucket
        or values["epoch"] != settings.generation_response_storage_epoch
        or values["quota_bytes"] != settings.generation_response_storage_budget_bytes
        or type(settings.generation_response_capacity_min_free_bytes) is not int
        or type(values["free_bytes"]) is not int
        or values["free_bytes"] < settings.generation_response_capacity_min_free_bytes
    ):
        raise ResponseIndeterminate("generation storage provisioning conflicts")
    capacity = CapacitySnapshot(
        values["bucket"],
        values["quota_bytes"],
        values["free_bytes"] - settings.generation_response_capacity_min_free_bytes,
        UUID(values["epoch"]),
        UUID(nonce),
        values["measured_at"] + 15,
    )
    objects = PrivateObjects()
    await objects.preflight()
    verify_capacity_snapshot(
        payload["assertion"], identity, nonce
    )  # Policy I/O cannot age admission proof.
    ledger = await admit_response(identity, capacity)
    return objects, ledger


@dataclass
class Capture:
    identity: GenerationProviderIdentity
    route: str
    objects: PrivateObjects | None = None
    ledger: ResponseLedger | None = None
    obtained: dict[str, Any] | None = None
    accounting_confirmed: bool = False
    stream_queue: asyncio.Queue[str | None] | None = None
    stream_task: asyncio.Task[Any] | None = None

    async def admit(self) -> None:
        if self.ledger is not None:
            raise ResponseIndeterminate("generation capture already dispatched")
        self.objects, self.ledger = await preflight_response_store(self.identity)


async def before_dispatch(identity: GenerationProviderIdentity | None) -> None:
    capture = _CURRENT.get()
    if capture is not None:
        if identity != capture.identity:
            raise ResponseIndeterminate("generation capture binding conflicts")
        await capture.admit()


def verify_dispatch_capacity() -> None:
    """Last pre-POST fence only. Admitted receive/store never checks token age."""
    capture = _CURRENT.get()
    if capture is not None:
        if capture.ledger is None:
            raise ResponseIndeterminate("generation capture was not admitted")
        require_fresh_capacity(capture.ledger.capacity_expires_at)


@contextmanager
def dispatch_capacity_guard() -> Iterator[None]:
    verify_dispatch_capacity()
    yield


def retention_active() -> bool:
    return _CURRENT.get() is not None


def remember_json_response(body: dict[str, Any]) -> None:
    capture = _CURRENT.get()
    if capture is not None:
        capture.obtained = {"format": "json", "status": 200, "body": body}


def remember_provider_refusal(body: dict[str, Any]) -> None:
    """A fully read, durably accounted refusal has no provider SSE to retain."""
    capture = _CURRENT.get()
    if capture is not None:
        if capture.stream_task is not None or capture.obtained is not None:
            raise ResponseIndeterminate("provider refusal followed accepted output")
        capture.obtained = {"format": "json", "status": 503, "body": body}


async def begin_stream_capture() -> None:
    capture = _CURRENT.get()
    if capture is None:
        return
    if capture.ledger is None or capture.objects is None or capture.stream_task is not None:
        raise ResponseIndeterminate("generation stream was not admitted")
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=2)
    capture.stream_queue = queue

    async def chunks() -> AsyncIterator[bytes]:
        yield (
            canonical({"version": 1, "format": "sse", "identity": capture.identity.to_wire()})
            + b"\n"
        )
        done = False
        while (event := await queue.get()) is not None:
            if done:
                raise ResponseIndeterminate("provider stream continued after completion")
            if event == "[DONE]":
                done = True
            else:
                document = json.loads(event)
                if not isinstance(document, dict) or "error" in document:
                    raise ResponseIndeterminate("provider stream did not complete")
            yield canonical(event) + b"\n"
        if not done:
            raise ResponseIndeterminate("provider stream lacks complete receipt")

    writer = ChunkWriter(capture.identity.attempt_id, capture.objects, capture.ledger)
    capture.stream_task = asyncio.create_task(
        writer.store(chunks(), finish_reason="complete_sse", route=capture.route)
    )


async def receive_stream_event(event: str | None) -> None:
    capture = _CURRENT.get()
    if capture is None:
        return
    if capture.stream_queue is None or capture.stream_task is None:
        raise ResponseIndeterminate("generation stream capture missing")
    put = asyncio.create_task(capture.stream_queue.put(event))
    try:
        await asyncio.wait((put, capture.stream_task), return_when=asyncio.FIRST_COMPLETED)
        if capture.stream_task.done():
            await capture.stream_task
            if event is None:
                return
            raise ResponseIndeterminate("generation stream already completed")
        await put
    finally:
        if not put.done():
            put.cancel()
            with suppress(asyncio.CancelledError):
                await put


async def finish_stream_capture() -> None:
    capture = _CURRENT.get()
    if capture is None or capture.stream_task is None:
        return
    if not capture.stream_task.done():
        await receive_stream_event(None)
    await capture.stream_task


def confirm_response_accounting() -> None:
    # Only the owning route calls this after its existing full charge/receipt
    # chain succeeds. HTTP status or a completed provider journal is insufficient.
    capture = _CURRENT.get()
    if capture is not None:
        capture.accounting_confirmed = True


async def authenticate_record(request: Request, route: str) -> GenerationProviderIdentity | None:
    headers = request.headers.getlist(HEADER)
    identity = parse_attempt(await request.body(), route, require_identity=bool(headers))
    if not headers and identity is None:
        return None
    enabled = getattr(get_settings(), "generation_response_retention_enabled", False)
    if not headers and (identity is None or not enabled):
        return None
    if not enabled:
        raise HTTPException(503, "generation response retention is disabled")
    if len(headers) != 1 or identity is None:
        raise HTTPException(403, "generation response authentication required")
    try:
        authenticated = verify_generation_response_token(
            headers[0], purpose="generation_response.record"
        )
    except ValueError:
        raise HTTPException(403, "generation response authentication denied") from None
    if authenticated != identity:
        raise HTTPException(403, "generation response binding conflicts")
    return identity


async def run_retained(
    identity: GenerationProviderIdentity, route: str, operation: Callable[[], Awaitable[Any]]
) -> Any:
    ready: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=64)
    subscribed = True

    def publish(value: str | None) -> None:
        nonlocal subscribed
        if not subscribed:
            return
        try:
            queue.put_nowait(value)
        except asyncio.QueueFull:
            # A detached/slow subscriber cannot stall capture or grow memory.
            subscribed = False
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(json.dumps({"error": {"code": "generation_response_indeterminate"}}))
            queue.put_nowait(None)

    async def subscriber() -> AsyncIterator[str]:
        nonlocal subscribed
        try:
            while (value := await queue.get()) is not None:
                yield value
        finally:
            subscribed = False

    async def execute() -> None:
        capture = Capture(identity, route)
        context = _CURRENT.set(capture)
        try:
            try:
                result = await operation()
            except HTTPException as exc:
                if capture.ledger is None:
                    raise
                # Preserve FastAPI's error envelope. A complete HTTP refusal
                # can be retained only when the existing committed journal
                # subsequently confirms its terminal accounting in reconciled().
                result = JSONResponse(
                    {"detail": exc.detail},
                    status_code=exc.status_code,
                    headers=exc.headers,
                )
            if capture.ledger is None or capture.objects is None:
                # A pre-provider rejection has no body capture or paid dispatch.
                if not ready.done():
                    ready.set_result(result)
                return
            writer = ChunkWriter(identity.attempt_id, capture.objects, capture.ledger)
            if isinstance(result, EventSourceResponse):
                if not ready.done():
                    ready.set_result(EventSourceResponse(subscriber()))

                done = False
                try:
                    async for value in result.body_iterator:
                        if not isinstance(value, str) or done:
                            raise ResponseIndeterminate("invalid completed response stream")
                        if value == "[DONE]":
                            done = True
                        else:
                            publish(value)
                finally:
                    close = getattr(result.body_iterator, "aclose", None)
                    if close is not None:
                        await close()
                if capture.stream_task is None:
                    if not capture.accounting_confirmed or capture.obtained is None:
                        raise ResponseIndeterminate("response stream lacks complete provider receipt")
                    await _store_obtained(writer, capture)
                else:
                    await capture.stream_task
                if capture.accounting_confirmed:
                    await capture.ledger.reconciled()
                    if done:
                        publish("[DONE]")
                publish(None)
            else:
                status = result.status_code if isinstance(result, Response) else 200
                body = bytes(result.body) if isinstance(result, Response) else canonical(result)

                if capture.obtained is None:
                    capture.obtained = {
                        "format": "json",
                        "status": status,
                        "body": json.loads(body),
                    }
                await _store_obtained(writer, capture)
                if capture.accounting_confirmed:
                    await capture.ledger.reconciled()
                if not ready.done():
                    ready.set_result(result)
        except BaseException as exc:
            if capture.stream_task is not None and not capture.stream_task.done():
                capture.stream_task.cancel()
                with suppress(BaseException):
                    await capture.stream_task
            if capture.ledger is not None:
                with suppress(BaseException):
                    await asyncio.shield(capture.ledger.indeterminate())
            if not ready.done():
                ready.set_exception(
                    exc
                    if isinstance(exc, HTTPException)
                    else HTTPException(
                        503,
                        {
                            "error": {
                                "code": "billing_unavailable",
                                "message": "Generation response outcome is indeterminate",
                                "details": {"provider_charge_ambiguous": True, "retry_safe": False},
                            }
                        },
                    )
                )
            else:
                publish(json.dumps({"error": {"code": "generation_response_indeterminate"}}))
                publish(None)
        finally:
            _CURRENT.reset(context)

    task = asyncio.create_task(execute())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    # Retrieve abandoned future exceptions; HTTP disconnect cannot cancel owner.
    ready.add_done_callback(lambda value: value.exception() if not value.cancelled() else None)
    return await asyncio.shield(ready)


async def _store_obtained(writer: ChunkWriter, capture: Capture) -> None:
    obtained = capture.obtained
    if obtained is None:
        raise ResponseIndeterminate("generation response was not received")

    async def chunks() -> AsyncIterator[bytes]:
        envelope = {"version": 1, "identity": capture.identity.to_wire(), **obtained}
        yield canonical(envelope)

    await writer.store(
        chunks(), finish_reason="complete_" + obtained["format"], route=capture.route
    )


async def drain_captures() -> None:
    if _TASKS:
        await asyncio.gather(*tuple(_TASKS), return_exceptions=True)

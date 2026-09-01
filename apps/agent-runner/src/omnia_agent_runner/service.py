"""Minimal resident fail-closed runner service."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


class RunHandler(Protocol):
    async def submit(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(slots=True)
class RunnerService:
    host: str = "127.0.0.1"
    port: int = 8080
    run_handler: RunHandler | None = None
    _shutdown: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _server: asyncio.AbstractServer | None = field(default=None, init=False)

    @property
    def ready(self) -> bool:
        return self.run_handler is not None

    async def serve(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        async with self._server:
            await self._shutdown.wait()

    def stop(self) -> None:
        self._shutdown.set()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request = await _read_request(reader)
            if request is None:
                return
            status_code, payload = await self._route(request)
            await _write_json_response(writer, status_code, payload)
        finally:
            writer.close()
            await writer.wait_closed()

    async def _route(self, request: HttpRequest) -> tuple[int, dict[str, Any]]:
        if request.method == "GET" and request.path == "/healthz":
            return 200, {"ok": True, "ready": self.ready, "service": "omnia-agent-runner"}
        if request.method == "GET" and request.path == "/readyz":
            if self.ready:
                return 200, {"ok": True, "ready": True}
            return 503, {"ok": False, "reason": "adapters_unconfigured"}
        if request.method == "POST" and request.path == "/runs":
            if self.run_handler is None:
                return 503, {"ok": False, "reason": "adapters_unconfigured"}
            body = _decode_json_body(request.body)
            if body is None:
                return 400, {"ok": False, "reason": "invalid_json"}
            response = await self.run_handler.submit(body)
            return 202, dict(response)
        return 404, {"ok": False, "reason": "not_found"}


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    path: str
    body: bytes


def load_runner_service_from_env(
    environ: Mapping[str, str] | None = None,
) -> RunnerService:
    env = dict(os.environ if environ is None else environ)
    host = env.get("OMNIA_RUNNER_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port_raw = env.get("OMNIA_RUNNER_PORT", "8080").strip() or "8080"
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError("OMNIA_RUNNER_PORT must be an integer") from exc
    if port <= 0 or port > 65535:
        raise ValueError("OMNIA_RUNNER_PORT must be between 1 and 65535")
    return RunnerService(host=host, port=port)


async def _read_request(reader: asyncio.StreamReader) -> HttpRequest | None:
    request_line = await reader.readline()
    if not request_line:
        return None
    method, path = _parse_request_line(request_line.decode("ascii", errors="strict"))
    content_length = 0

    while True:
        header_line = await reader.readline()
        if header_line in {b"", b"\r\n"}:
            break
        name, _, value = header_line.decode("ascii", errors="ignore").partition(":")
        if name.lower().strip() == "content-length":
            try:
                content_length = int(value.strip())
            except ValueError:
                content_length = 0

    body = await reader.readexactly(content_length) if content_length > 0 else b""
    return HttpRequest(method=method, path=path, body=body)


def _parse_request_line(request_line: str) -> tuple[str, str]:
    parts = request_line.strip().split()
    if len(parts) != 3:
        raise ValueError("malformed request line")
    method, path, _version = parts
    return method.upper(), path


def _decode_json_body(body: bytes) -> dict[str, Any] | None:
    if not body:
        return {}
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


async def _write_json_response(
    writer: asyncio.StreamWriter,
    status_code: int,
    payload: Mapping[str, Any],
) -> None:
    body = json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode("utf-8")
    writer.write(
        (
            f"HTTP/1.1 {status_code} {_reason_phrase(status_code)}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Content-Type: application/json\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
    )
    writer.write(body)
    await writer.drain()


def _reason_phrase(status_code: int) -> str:
    return {
        200: "OK",
        202: "Accepted",
        400: "Bad Request",
        404: "Not Found",
        503: "Service Unavailable",
    }.get(status_code, "OK")

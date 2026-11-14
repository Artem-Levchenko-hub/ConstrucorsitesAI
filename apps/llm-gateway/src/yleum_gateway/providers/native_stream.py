"""Collect one bounded upstream SSE response before exposing any tool actions.

The mutable evidence belongs to the caller's single journal owner. It survives
transport errors and cancellation, even when no complete response can be used.
"""

import asyncio
import json
import math
import re
import time
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, aclosing, asynccontextmanager
from typing import Any

import httpx

from yleum_gateway.core.errors import DispatchDeadlineError
from yleum_gateway.services.pricing import read_reported_cost
from yleum_gateway.services.provider_calls import ReceiptFailure
from yleum_gateway.services.receipt_evidence import aitunnel_request_id, receipt_evidence
from yleum_gateway.services.text_failover import decoded_body_headers

IDLE_TIMEOUT_SECONDS = 240.0
CONNECT_TIMEOUT_SECONDS = 30.0
RECEIPT_RESERVE_SECONDS = 15.0
MAX_WIRE_BYTES = 16 * 1024 * 1024
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 100_000
SONNET55_LARGE_OUTPUT_TOKENS = 128_000
SONNET55_LARGE_OUTPUT_WIRE_BYTES = 64 * 1024 * 1024
SONNET55_LARGE_OUTPUT_EVENTS = SONNET55_LARGE_OUTPUT_TOKENS + 4096
MAX_TOOLS = 256
TRANSPORT_STAGES = frozenset({"connect", "sending", "awaiting_headers", "headers", "body", "complete"})
_MILLISECONDS = (
    "response_budget_ms", "connect_timeout_ms", "read_timeout_ms", "write_timeout_ms", "pool_timeout_ms",
    "request_sent_ms", "headers_received_ms", "first_sse_data_ms",
    "response_completed_ms",
)
_FINISH_REASONS = {
    "stop", "tool_calls", "length", "content_filter", "end_turn", "tool_use",
    "max_tokens", "refusal", "pause_turn", "stop_sequence",
}


def aitunnel_output_cap(
    response: httpx.Response, max_tokens: object, *, provider_scope: str,
) -> int | None:
    if provider_scope != "aitunnel" or response.status_code != 200:
        return None
    values = response.headers.get_list("x-aitunnel-output-capped")
    if len(values) != 1 or not re.fullmatch(r"[1-9][0-9]{0,6}", values[0]):
        return None
    cap = int(values[0])
    if cap > 2_000_000 or (type(max_tokens) is int and cap >= max_tokens):
        return None
    return cap


def provider_request_deadline(value: str | None, started_at: float, *, reserve: float) -> float:
    """Share one request window across primary and reserve dispatches."""
    try:
        seconds = float(value) if value is not None else 270.0
    except ValueError:
        seconds = 270.0
    if not math.isfinite(seconds) or seconds <= 0:
        seconds = 270.0
    return started_at + min(seconds, 1500.0) - reserve


def _transport_timeout(io_timeout: float, *, provider_scope: str) -> httpx.Timeout:
    # The request deadline bounds AITunnel's header wait and response body.
    return httpx.Timeout(
        io_timeout,
        connect=min(CONNECT_TIMEOUT_SECONDS, io_timeout),
        read=None if provider_scope == "aitunnel" else io_timeout,
    )


def transport_diagnostics(evidence: dict[str, Any]) -> dict[str, int | str | None]:
    """Keep provider transport logs separate from private payload and accounting fields."""
    fields: dict[str, int | str | None] = {
        field: value for field in _MILLISECONDS
        if type(value := evidence.get(field)) is int and 0 <= value <= 2**31 - 1
    }
    started_at = evidence.get("transport_started_unix_ms")
    if type(started_at) is int and 0 < started_at <= 2**53 - 1:
        fields["transport_started_unix_ms"] = started_at
    if "read_timeout_ms" in evidence and evidence["read_timeout_ms"] is None:
        fields["read_timeout_ms"] = None
    mode = evidence.get("transport_mode")
    if mode in ("sse", "json"):
        fields["transport_mode"] = mode
    tokens_mode = evidence.get("max_tokens_mode")
    if tokens_mode in ("sent", "omitted"):
        fields["max_tokens_mode"] = tokens_mode
    maximum = evidence.get("requested_max_tokens")
    if type(maximum) is int and 1 <= maximum <= 2_000_000:
        fields["requested_max_tokens"] = maximum
    return fields


class _ResponseProgress:
    def __init__(
        self, evidence: dict[str, Any], payload: dict[str, Any], *,
        mode: str, budget_seconds: float, timeout: httpx.Timeout,
    ) -> None:
        self.evidence = evidence
        self.started_at = time.monotonic()
        evidence.update(
            transport_stage="connect", transport_mode=mode,
            transport_started_unix_ms=time.time_ns() // 1_000_000,
            response_budget_ms=round(budget_seconds * 1000),
            max_tokens_mode="sent" if "max_tokens" in payload else "omitted",
        )
        for field, seconds in timeout.as_dict().items():
            evidence[f"{field}_timeout_ms"] = round(seconds * 1000) if seconds is not None else None
        maximum = payload.get("max_tokens")
        if type(maximum) is int and 1 <= maximum <= 2_000_000:
            evidence["requested_max_tokens"] = maximum

    def record(self, stage: str, milestone: str) -> None:
        self.evidence["transport_stage"] = stage
        if milestone not in self.evidence:
            self.evidence[milestone] = round((time.monotonic() - self.started_at) * 1000)

    async def trace(self, event: str, _info: dict[str, Any]) -> None:
        # httpcore's event names are observed facts. Its event data may include
        # credentials or prompts, so ignore it completely.
        protocol, _, phase = event.partition(".")
        if protocol not in {"http11", "http2"}:
            return
        if phase == "send_request_headers.started":
            self.evidence["transport_stage"] = "sending"
        elif phase == "send_request_body.complete":
            self.record("awaiting_headers", "request_sent_ms")


def open_aitunnel_response(
    url: str, payload: dict[str, Any], headers: dict[str, str], *,
    deadline: float, evidence: dict[str, Any],
) -> AbstractAsyncContextManager[httpx.Response]:
    return open_timed_response(url, payload, headers, deadline=deadline, evidence=evidence,
                               provider_scope="aitunnel")


@asynccontextmanager
async def open_timed_response(
    url: str, payload: dict[str, Any], headers: dict[str, str], *,
    deadline: float, evidence: dict[str, Any], provider_scope: str,
) -> AsyncIterator[httpx.Response]:
    """Keep the socket within the original request's absolute window."""
    from yleum_gateway.services.generation_response_capture import verify_dispatch_capacity

    loop = asyncio.get_running_loop()
    remaining = deadline - loop.time()
    if remaining <= 0:
        raise DispatchDeadlineError()
    timeout = _transport_timeout(min(IDLE_TIMEOUT_SECONDS, remaining), provider_scope=provider_scope)
    progress = _ResponseProgress(
        evidence, payload, mode="sse" if payload.get("stream") is True else "json",
        budget_seconds=remaining, timeout=timeout,
    )
    async with asyncio.timeout_at(deadline), httpx.AsyncClient(
        timeout=timeout, trust_env=False, transport=httpx.AsyncHTTPTransport(retries=0),
    ) as client:
        request = client.build_request(
            "POST", url, json=payload, headers=headers, extensions={"trace": progress.trace},
        )
        verify_dispatch_capacity()
        if loop.time() >= deadline:
            raise DispatchDeadlineError()
        async with aclosing(await client.send(request, stream=True)) as response:
            progress.record("headers", "headers_received_ms")
            if 400 <= response.status_code <= 599:
                evidence["upstream_http_status"] = response.status_code
            evidence.update(receipt_evidence({}, response.headers, provider_scope=provider_scope))
            yield response


def _record_protocol_facts(evidence: dict[str, Any], event: dict[str, Any]) -> None:
    """Observe only normalized scalars; this must not influence receipt validation."""
    usage = event.get("usage")
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if type(value) is int and 0 <= value <= 2_000_000:
                evidence[key] = value
    choices = event.get("choices")
    if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
        reason = choices[0].get("finish_reason")
        if isinstance(reason, str):
            evidence["finish_reason"] = reason if reason in _FINISH_REASONS else "unknown"


class StreamReceiptError(ValueError):
    """A response could be paid but is not a coherent, complete receipt."""

    def __init__(self, reason: ReceiptFailure) -> None:
        self.reason = ReceiptFailure(reason)
        super().__init__(self.reason.value)


def _identity(value: Any) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 512
            or any(ord(char) < 32 for char in value)):
        raise StreamReceiptError(ReceiptFailure.INVALID_IDENTITY)
    return value


class _Collector:
    def __init__(self, evidence: dict[str, Any], *, provider_scope: str = "llmgw",
                 max_events: int = MAX_EVENTS) -> None:
        self.evidence = evidence
        self.provider_scope = provider_scope
        self.max_events = max_events
        self.provider_header_id: str | None = None
        self.identity: dict[str, str] = {}
        self.text: list[str] = []
        self.tools: dict[int, dict[str, Any]] = {}
        self.finish: str | None = None
        self.usage: dict[str, Any] | None = None
        self.metadata: dict[str, Any] = {}
        self.done = False
        self.events = 0

    def feed(self, raw: str) -> None:
        self.events += 1
        if self.events > self.max_events or self.done:
            raise StreamReceiptError(ReceiptFailure.EVENT_LIMIT)
        if raw == "[DONE]":
            self.done = True
            return
        try:
            event = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise StreamReceiptError(ReceiptFailure.INVALID_EVENT) from exc
        if not isinstance(event, dict):
            raise StreamReceiptError(ReceiptFailure.INVALID_EVENT)
        error = event.get("error")
        if error is not None:
            if self.provider_scope == "aitunnel":
                self.evidence["provider_stream_outcome"] = "error"
                if self.evidence.get("provider_request_id") is None and isinstance(error, dict):
                    request_id = aitunnel_request_id({"x-request-id": error.get("request_id")})
                    if request_id is not None and event.get("id", request_id) == request_id:
                        self.evidence["provider_request_id"] = request_id
            raise StreamReceiptError(ReceiptFailure.UPSTREAM_ERROR)
        _record_protocol_facts(self.evidence, event)
        for key in ("id", "model"):
            if event.get(key) is not None:
                value = _identity(event[key])
                if key in self.identity and value != self.identity[key]:
                    raise StreamReceiptError(ReceiptFailure.CONFLICTING_IDENTITY)
                self.identity[key] = value
                if key == "id" and self.provider_header_id is None:
                    self.evidence["provider_request_id"] = value
        usage = event.get("usage")
        if usage is not None:
            if not isinstance(usage, dict) or (self.usage is not None and usage != self.usage):
                raise StreamReceiptError(ReceiptFailure.CONFLICTING_USAGE)
            self.usage = usage
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            for key in ("cost_rub", "cost_usd"):
                if key in metadata:
                    self.metadata[key] = metadata[key]
        reported = read_reported_cost(event, {}, provider_scope=self.provider_scope)
        for field, reported_value in (("provider_cost_rub", reported.cost_rub),
                             ("provider_cost_usd", reported.cost_usd)):
            if reported_value is not None and self.evidence.get(field) is None:
                self.evidence[field] = reported_value
        choices = event.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise StreamReceiptError(ReceiptFailure.INVALID_RESPONSE)
        if not choices:
            return
        choice = choices[0]
        if not isinstance(choice, dict) or choice.get("index", 0) != 0:
            raise StreamReceiptError(ReceiptFailure.INVALID_RESPONSE)
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            raise StreamReceiptError(ReceiptFailure.INVALID_RESPONSE)
        if self.finish is not None and any(delta.get(k) for k in ("content", "tool_calls")):
            raise StreamReceiptError(ReceiptFailure.CONFLICTING_FINISH)
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise StreamReceiptError(ReceiptFailure.INVALID_RESPONSE)
            self.text.append(content)
        tools = delta.get("tool_calls", [])
        if not isinstance(tools, list):
            raise StreamReceiptError(ReceiptFailure.INVALID_TOOL)
        for tool in tools:
            if not isinstance(tool, dict):
                raise StreamReceiptError(ReceiptFailure.INVALID_TOOL)
            index = tool.get("index")
            if type(index) is not int or not 0 <= index < MAX_TOOLS:
                raise StreamReceiptError(ReceiptFailure.INVALID_TOOL)
            target = self.tools.setdefault(index, {"type": "function", "function": {
                "name": [], "arguments": []}})
            if tool.get("type", "function") != "function":
                raise StreamReceiptError(ReceiptFailure.INVALID_TOOL)
            if tool.get("id") is not None:
                identity = _identity(tool["id"])
                if target.get("id", identity) != identity:
                    raise StreamReceiptError(ReceiptFailure.CONFLICTING_IDENTITY)
                target["id"] = identity
            function = tool.get("function", {})
            if not isinstance(function, dict):
                raise StreamReceiptError(ReceiptFailure.INVALID_TOOL)
            for key in ("name", "arguments"):
                if key in function:
                    if not isinstance(function[key], str):
                        raise StreamReceiptError(ReceiptFailure.INVALID_TOOL)
                    target["function"][key].append(function[key])
        reason = choice.get("finish_reason")
        if reason is not None:
            if not isinstance(reason, str) or (self.finish is not None and self.finish != reason):
                raise StreamReceiptError(ReceiptFailure.CONFLICTING_FINISH)
            self.finish = reason

    def result(self) -> dict[str, Any]:
        if not self.done or self.finish is None or not all(key in self.identity for key in ("id", "model")):
            raise StreamReceiptError(ReceiptFailure.INCOMPLETE)
        if self.usage is None:
            if self.provider_scope == "aitunnel" and self.finish == "length":
                # AITunnel can cap a paid stream without sending usage.
                self.evidence["provider_stream_outcome"] = "output_limit"
            raise StreamReceiptError(ReceiptFailure.INCOMPLETE)
        for key in ("prompt_tokens", "completion_tokens"):
            value = self.usage.get(key)
            if type(value) is not int or value < 0:
                raise StreamReceiptError(ReceiptFailure.INVALID_USAGE)
        message: dict[str, Any] = {"role": "assistant", "content": ""}
        # A truncated reply can still be accounted for but must never execute.
        if self.finish != "length":
            message["content"] = "".join(self.text)
            if self.tools:
                if self.finish != "tool_calls" or sorted(self.tools) != list(range(len(self.tools))):
                    raise StreamReceiptError(ReceiptFailure.INVALID_TOOL)
                ids = []
                for tool in self.tools.values():
                    tool["function"] = {key: "".join(parts)
                                        for key, parts in tool["function"].items()}
                    ids.append(_identity(tool.get("id")))
                    _identity(tool["function"]["name"])
                    try:
                        args = json.loads(tool["function"]["arguments"])
                    except (ValueError, RecursionError) as exc:
                        raise StreamReceiptError(ReceiptFailure.INVALID_TOOL_ARGUMENTS) from exc
                    if not isinstance(args, dict):
                        raise StreamReceiptError(ReceiptFailure.INVALID_TOOL_ARGUMENTS)
                if len(set(ids)) != len(ids):
                    raise StreamReceiptError(ReceiptFailure.DUPLICATE_TOOL)
                message["tool_calls"] = [self.tools[index] for index in sorted(self.tools)]
            elif self.finish == "tool_calls":
                raise StreamReceiptError(ReceiptFailure.INVALID_TOOL)
        return {**self.identity, "choices": [{"index": 0, "message": message,
                "finish_reason": self.finish}], "usage": self.usage, "metadata": self.metadata}


def collected_stream_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "stream": True, "stream_options": {"include_usage": True}}


async def collect_response(
    url: str, payload: dict[str, Any], headers: dict[str, str], *,
    budget_seconds: float, evidence: dict[str, Any], provider_scope: str = "llmgw",
    deadline: float | None = None,
) -> httpx.Response:
    """One POST within the request deadline; never retries internally."""
    evidence.pop("upstream_http_status", None)
    if not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise TimeoutError("Provider response budget exhausted")
    # One delta per token plus framing and usage must fit in a bounded receipt.
    large_output = (
        provider_scope == "aitunnel"
        and payload.get("model") in ("claude-sonnet-5.5", "anthropic/claude-sonnet-5.5")
        and payload.get("max_tokens") == SONNET55_LARGE_OUTPUT_TOKENS
    )
    max_wire_bytes = SONNET55_LARGE_OUTPUT_WIRE_BYTES if large_output else MAX_WIRE_BYTES
    max_events = SONNET55_LARGE_OUTPUT_EVENTS if large_output else MAX_EVENTS
    collector = _Collector(evidence, provider_scope=provider_scope, max_events=max_events)
    request_headers = httpx.Headers(headers)
    request_headers["accept"] = "text/event-stream"
    from yleum_gateway.services.payload_limits import require_provider_payload_size

    wire_payload = collected_stream_payload(payload)
    require_provider_payload_size(wire_payload)
    remaining = budget_seconds if deadline is None else deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise DispatchDeadlineError()
    io_timeout = min(IDLE_TIMEOUT_SECONDS, remaining) if provider_scope == "aitunnel" else IDLE_TIMEOUT_SECONDS
    timeout = _transport_timeout(io_timeout, provider_scope=provider_scope)
    progress = _ResponseProgress(
        evidence, payload, mode="sse", budget_seconds=remaining, timeout=timeout,
    )
    extensions = {"trace": progress.trace}
    budget = asyncio.timeout(budget_seconds) if deadline is None else asyncio.timeout_at(deadline)
    async with budget, httpx.AsyncClient(
        timeout=timeout, trust_env=False, transport=httpx.AsyncHTTPTransport(retries=0),
    ) as client:
        from yleum_gateway.services.generation_response_capture import verify_dispatch_capacity

        response_scope: AbstractAsyncContextManager[httpx.Response]
        if deadline is None:
            verify_dispatch_capacity()
            response_scope = client.stream(
                "POST", url, json=wire_payload, headers=request_headers, extensions=extensions,
            )
        else:
            request = client.build_request(
                "POST", url, json=wire_payload, headers=request_headers, extensions=extensions,
            )
            verify_dispatch_capacity()
            if asyncio.get_running_loop().time() >= deadline:
                raise DispatchDeadlineError()
            response_scope = aclosing(await client.send(request, stream=True))
        async with response_scope as response:
            progress.record("headers", "headers_received_ms")
            if type(response.status_code) is int and 400 <= response.status_code <= 599:
                evidence["upstream_http_status"] = response.status_code
            cap = aitunnel_output_cap(response, payload.get("max_tokens"), provider_scope=provider_scope)
            if cap is not None:
                evidence["provider_output_cap_tokens"] = cap
            if provider_scope == "llmgw":
                provider_header_id = response.headers.get("x-llmgw-request-id")
                collector.provider_header_id = _identity(provider_header_id) if provider_header_id else None
                raw_id = collector.provider_header_id or response.headers.get("x-request-id")
            else:
                raw_id = aitunnel_request_id(response.headers)
            if raw_id:
                # The completion can replace a provisional header ID. LLMGW's
                # own header retains the existing chat receipt precedence.
                evidence["provider_request_id"] = _identity(raw_id)
            reported = read_reported_cost({}, response.headers, provider_scope=provider_scope)
            evidence.update(provider_cost_rub=reported.cost_rub, provider_cost_usd=reported.cost_usd)
            raw = bytearray()
            wire_bytes = 0
            event_lines: list[bytes] = []
            event_bytes = 0
            async for part in response.aiter_bytes():
                evidence["transport_stage"] = "body"
                wire_bytes += len(part)
                if wire_bytes > (16_384 if response.status_code >= 400 else max_wire_bytes):
                    raise StreamReceiptError(ReceiptFailure.SIZE_LIMIT)
                raw.extend(part)
                if response.status_code >= 400:
                    continue
                consumed = 0
                while (newline := raw.find(b"\n", consumed)) >= 0:
                    line = raw[consumed:newline].rstrip(b"\r")
                    consumed = newline + 1
                    event_bytes += len(line)
                    if event_bytes > MAX_EVENT_BYTES:
                        raise StreamReceiptError(ReceiptFailure.SIZE_LIMIT)
                    if not line:
                        if event_lines:
                            try:
                                collector.feed(b"\n".join(event_lines).decode("utf-8"))
                            except UnicodeError as exc:
                                raise StreamReceiptError(ReceiptFailure.INVALID_ENCODING) from exc
                        event_lines, event_bytes = [], 0
                        if collector.done:
                            result = collector.result()
                            progress.record("complete", "response_completed_ms")
                            return httpx.Response(response.status_code, headers=decoded_body_headers(response),
                                                  json=result, request=response.request)
                    elif line.startswith(b"data:"):
                        progress.record("body", "first_sse_data_ms")
                        event_lines.append(bytes(line[5:].lstrip(b" ")))
                del raw[:consumed]
                if len(raw) + event_bytes > MAX_EVENT_BYTES:
                    raise StreamReceiptError(ReceiptFailure.SIZE_LIMIT)
            if response.status_code >= 400:
                progress.record("complete", "response_completed_ms")
                return httpx.Response(response.status_code, headers=decoded_body_headers(response),
                                      content=bytes(raw), request=response.request, history=response.history)
            raise StreamReceiptError(ReceiptFailure.MISSING_COMPLETION)

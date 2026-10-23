"""Collect one bounded upstream SSE response before exposing any tool actions.

The mutable evidence belongs to the caller's single journal owner. It survives
transport errors and cancellation, even when no complete response can be used.
"""

import asyncio
import json
import math
from typing import Any

import httpx

from yleum_gateway.services.pricing import read_reported_cost
from yleum_gateway.services.provider_calls import ReceiptFailure

IDLE_TIMEOUT_SECONDS = 240.0
CONNECT_TIMEOUT_SECONDS = 30.0
RECEIPT_RESERVE_SECONDS = 15.0
MAX_WIRE_BYTES = 16 * 1024 * 1024
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 100_000
MAX_TOOLS = 256
_FINISH_REASONS = {
    "stop", "tool_calls", "length", "content_filter", "end_turn", "tool_use",
    "max_tokens", "refusal", "pause_turn", "stop_sequence",
}


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
    def __init__(self, evidence: dict[str, Any], *, provider_scope: str = "llmgw") -> None:
        self.evidence = evidence
        self.provider_scope = provider_scope
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
        if self.events > MAX_EVENTS or self.done:
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
        if event.get("error") is not None:
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
        if (not self.done or self.finish is None or self.usage is None
                or not all(key in self.identity for key in ("id", "model"))):
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


async def collect_response(
    url: str, payload: dict[str, Any], headers: dict[str, str], *,
    budget_seconds: float, evidence: dict[str, Any], provider_scope: str = "llmgw",
) -> httpx.Response:
    """One POST, bounded by wall time and idle time; never retries internally."""
    evidence.pop("upstream_http_status", None)
    if not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise TimeoutError("Provider response budget exhausted")
    evidence["transport_stage"] = "connect"
    evidence["max_tokens_mode"] = "sent" if "max_tokens" in payload else "omitted"
    maximum = payload.get("max_tokens")
    if type(maximum) is int and 1 <= maximum <= 2_000_000:
        evidence["requested_max_tokens"] = maximum
    collector = _Collector(evidence, provider_scope=provider_scope)
    request_headers = httpx.Headers(headers)
    request_headers["accept"] = "text/event-stream"
    async with asyncio.timeout(budget_seconds), httpx.AsyncClient(
        timeout=httpx.Timeout(IDLE_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        trust_env=False, transport=httpx.AsyncHTTPTransport(retries=0),
    ) as client:
        from yleum_gateway.services.generation_response_capture import verify_dispatch_capacity

        verify_dispatch_capacity()
        async with client.stream("POST", url, json={**payload, "stream": True,
            "stream_options": {"include_usage": True}},
            headers=request_headers) as response:
            evidence["transport_stage"] = "headers"
            if type(response.status_code) is int and 400 <= response.status_code <= 599:
                evidence["upstream_http_status"] = response.status_code
            provider_header_id = response.headers.get("x-llmgw-request-id")
            collector.provider_header_id = _identity(provider_header_id) if provider_header_id else None
            raw_id = collector.provider_header_id or response.headers.get("x-request-id")
            if raw_id:
                # Generic x-request-id can identify a proxy hop. Keep it only until
                # the completion supplies its canonical body ID; LLMGW's own header
                # retains the existing chat receipt precedence.
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
                if wire_bytes > (16_384 if response.status_code >= 400 else MAX_WIRE_BYTES):
                    raise StreamReceiptError(ReceiptFailure.SIZE_LIMIT)
                raw.extend(part)
                if response.status_code >= 400:
                    continue
                while b"\n" in raw:
                    line, _, rest = raw.partition(b"\n")
                    raw = bytearray(rest)
                    line = line.rstrip(b"\r")
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
                            evidence["transport_stage"] = "complete"
                            return httpx.Response(response.status_code, headers=response.headers,
                                                  json=result, request=response.request)
                    elif line.startswith(b"data:"):
                        event_lines.append(bytes(line[5:].lstrip(b" ")))
                if len(raw) + event_bytes > MAX_EVENT_BYTES:
                    raise StreamReceiptError(ReceiptFailure.SIZE_LIMIT)
            if response.status_code >= 400:
                return httpx.Response(response.status_code, headers=response.headers,
                                      content=bytes(raw), request=response.request)
            raise StreamReceiptError(ReceiptFailure.MISSING_COMPLETION)

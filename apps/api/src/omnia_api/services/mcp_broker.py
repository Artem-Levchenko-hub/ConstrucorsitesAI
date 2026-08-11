"""Server-side MCP capability broker for the native generation agent.

The browser never talks to MCP servers. Operators configure an allow-list, the
broker discovers only approved tools, and Gemini sees two stable meta-tools
instead of every third-party schema in every turn. Phase one is deliberately
read-only: external knowledge can improve a build, but cannot publish, charge,
delete or mutate customer data without a future confirmation workflow.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any, cast
from urllib.parse import urlparse

import httpx2
import structlog
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from omnia_api.core.config import Settings, get_settings

log = structlog.get_logger(__name__)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,48}$")
_TOOL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CONTEXT7_TOOLS = frozenset({"resolve-library-id", "query-docs"})


class McpBrokerError(RuntimeError):
    """Safe broker failure that can be returned to the model."""


@dataclass(frozen=True)
class McpServerSpec:
    key: str
    title: str
    url: str
    allowed_tools: frozenset[str]
    read_only: bool = True
    auth_env: str = ""
    headers_env: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 45.0

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.auth_env:
            token = os.getenv(self.auth_env, "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        for name, env_name in self.headers_env:
            value = os.getenv(env_name, "").strip()
            if value:
                headers[name] = value
        return headers


@dataclass(frozen=True)
class McpCapability:
    server: str
    tool: str
    title: str
    description: str
    input_schema: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "tool": self.tool,
            "title": self.title,
            "description": self.description,
            "input_schema": self.input_schema,
            "risk": "read_only",
        }


def _validate_url(url: str, *, production: bool) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("MCP URL must not contain credentials or fragments")
    if parsed.scheme != "https":
        local_dev = (
            not production
            and parsed.scheme == "http"
            and host
            in {
                "localhost",
                "127.0.0.1",
                "::1",
            }
        )
        if not local_dev:
            raise ValueError("MCP URL must use HTTPS")
    if not host:
        raise ValueError("MCP URL host is required")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if production and address is not None and not address.is_global:
        raise ValueError("private or loopback MCP addresses are forbidden in production")
    return url


def _from_mapping(raw: Mapping[str, Any], *, production: bool) -> McpServerSpec:
    key = str(raw.get("key") or "").strip().lower()
    if not _KEY_RE.fullmatch(key):
        raise ValueError("MCP server key must match [a-z][a-z0-9_-]{1,48}")
    url = _validate_url(str(raw.get("url") or "").strip(), production=production)
    tools_raw = raw.get("allowed_tools")
    if not isinstance(tools_raw, list):
        raise ValueError(f"MCP server {key} requires allowed_tools")
    tools = frozenset(str(item).strip() for item in tools_raw if str(item).strip())
    if not tools or any(not _TOOL_RE.fullmatch(item) for item in tools):
        raise ValueError(f"MCP server {key} has invalid allowed_tools")
    if raw.get("read_only") is not True:
        raise ValueError(
            f"MCP server {key} must explicitly set read_only=true; "
            "mutating capabilities are not accepted by the native agent"
        )
    headers_raw = raw.get("headers_env")
    headers: list[tuple[str, str]] = []
    if headers_raw is not None:
        if not isinstance(headers_raw, Mapping):
            raise ValueError(f"MCP server {key} headers_env must be an object")
        for header, env_name in headers_raw.items():
            clean_header = str(header).strip()
            clean_env = str(env_name).strip()
            if not clean_header or not clean_env:
                raise ValueError(f"MCP server {key} has an empty header mapping")
            headers.append((clean_header, clean_env))
    timeout = float(raw.get("timeout_seconds") or 45.0)
    if timeout < 5 or timeout > 300:
        raise ValueError(f"MCP server {key} timeout must be between 5 and 300 seconds")
    return McpServerSpec(
        key=key,
        title=str(raw.get("title") or key).strip()[:120],
        url=url,
        allowed_tools=tools,
        read_only=True,
        auth_env=str(raw.get("auth_env") or "").strip(),
        headers_env=tuple(headers),
        timeout_seconds=timeout,
    )


def configured_servers(settings: Settings | None = None) -> dict[str, McpServerSpec]:
    """Build the operator-controlled registry; invalid config fails closed."""

    cfg = settings or get_settings()
    production = cfg.env.lower() in {"prod", "production"}
    servers: dict[str, McpServerSpec] = {}
    if cfg.mcp_context7_enabled:
        context7 = McpServerSpec(
            key="context7",
            title="Context7 — актуальная документация библиотек",
            url="https://mcp.context7.com/mcp",
            allowed_tools=_CONTEXT7_TOOLS,
            auth_env="CONTEXT7_API_KEY",
            timeout_seconds=45.0,
        )
        servers[context7.key] = context7

    raw_json = cfg.mcp_servers_json.strip()
    if raw_json:
        try:
            decoded = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise McpBrokerError("MCP_SERVERS_JSON is not valid JSON") from exc
        if not isinstance(decoded, list):
            raise McpBrokerError("MCP_SERVERS_JSON must be an array")
        try:
            for item in decoded:
                if not isinstance(item, Mapping):
                    raise ValueError("each MCP server entry must be an object")
                server = _from_mapping(item, production=production)
                servers[server.key] = server
        except (TypeError, ValueError) as exc:
            raise McpBrokerError(str(exc)) from exc
    return servers


@asynccontextmanager
async def _connected_client(spec: McpServerSpec) -> AsyncIterator[Client]:
    """Connect with the official SDK; secrets stay on the API server."""

    headers = spec.headers()
    if not headers:
        async with Client(spec.url) as client:
            yield client
        return
    timeout = httpx2.Timeout(30.0, read=spec.timeout_seconds)
    async with httpx2.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
    ) as http_client:
        transport = streamable_http_client(spec.url, http_client=http_client)
        async with Client(transport) as client:
            yield client


def _schema(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping) and value.get("type") == "object":
        # JSON round-trip strips SDK/Pydantic wrappers and protects the gateway
        # from values that cannot be serialised into Gemini's function schema.
        return cast(
            dict[str, Any],
            json.loads(json.dumps(dict(value), ensure_ascii=False)),
        )
    return {"type": "object", "properties": {}}


def _block_json(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        dumped = block.model_dump(mode="json", exclude_none=True)
        return dumped if isinstance(dumped, dict) else {"value": dumped}
    if isinstance(block, Mapping):
        return dict(block)
    return {"type": "unknown", "value": str(block)}


def _compact_result(result: Any, *, max_chars: int) -> tuple[str, list[str], Any]:
    blocks = [_block_json(block) for block in getattr(result, "content", [])]
    artifacts: list[str] = []
    model_parts: list[str] = []
    for block in blocks:
        text = block.get("text")
        if isinstance(text, str) and text:
            model_parts.append(text)
        uri = block.get("uri") or block.get("resource", {}).get("uri")
        if isinstance(uri, str) and uri:
            artifacts.append(uri[:500])
        if block.get("type") in {"image", "audio"}:
            model_parts.append(f"[{block.get('type')} content returned by MCP server]")
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        structured_text = json.dumps(structured, ensure_ascii=False, default=str)
        if structured_text not in model_parts:
            model_parts.append(structured_text)
    text = "\n".join(model_parts).strip() or "MCP tool returned no textual content."
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n…[truncated {len(text) - max_chars} chars]"
    return text, artifacts[:20], structured


_catalog_cache: dict[str, tuple[float, tuple[McpCapability, ...]]] = {}
_catalog_locks: dict[str, asyncio.Lock] = {}


class McpBroker:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        servers: Mapping[str, McpServerSpec] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.servers = dict(servers) if servers is not None else configured_servers(self.settings)

    def _server(self, key: str) -> McpServerSpec:
        try:
            return self.servers[key]
        except KeyError as exc:
            raise McpBrokerError(f"unknown or disabled MCP server: {key}") from exc

    async def _catalog(self, spec: McpServerSpec) -> tuple[McpCapability, ...]:
        cached = _catalog_cache.get(spec.key)
        now = monotonic()
        if cached and cached[0] > now:
            return cached[1]
        lock = _catalog_locks.setdefault(spec.key, asyncio.Lock())
        async with lock:
            cached = _catalog_cache.get(spec.key)
            now = monotonic()
            if cached and cached[0] > now:
                return cached[1]
            try:
                async with asyncio.timeout(spec.timeout_seconds):
                    async with _connected_client(spec) as client:
                        page = await client.list_tools()
            except Exception as exc:
                log.warning(
                    "mcp.catalog_failed",
                    server=spec.key,
                    error=type(exc).__name__,
                )
                raise McpBrokerError(f"MCP server {spec.key} is temporarily unavailable") from exc
            capabilities: list[McpCapability] = []
            for tool in page.tools:
                name = str(tool.name)
                if name not in spec.allowed_tools:
                    continue
                capabilities.append(
                    McpCapability(
                        server=spec.key,
                        tool=name,
                        title=str(tool.title or name)[:160],
                        description=str(tool.description or "")[:1000],
                        input_schema=_schema(tool.input_schema),
                    )
                )
            catalog = tuple(capabilities)
            _catalog_cache[spec.key] = (
                now + float(self.settings.mcp_catalog_ttl_seconds),
                catalog,
            )
            return catalog

    async def discover(self, server: str = "") -> dict[str, Any]:
        if not self.settings.mcp_capabilities_enabled:
            return _error_observation(
                "MCP capabilities are disabled by the operator",
                stop="Do not retry in this generation.",
            )
        keys = [server] if server else sorted(self.servers)
        found: list[dict[str, Any]] = []
        unavailable: list[str] = []
        for key in keys:
            try:
                spec = self._server(key)
                found.extend(cap.public() for cap in await self._catalog(spec))
            except McpBrokerError as exc:
                unavailable.append(str(exc))
        status = "success" if found else "warning"
        summary = f"Discovered {len(found)} approved MCP capabilities."
        if unavailable:
            summary += " Unavailable: " + "; ".join(unavailable)
        return {
            "ok": bool(found),
            "status": status,
            "summary": summary,
            "content": json.dumps({"capabilities": found}, ensure_ascii=False),
            "next_actions": (
                ["Call one capability with exact server, tool and schema-valid arguments."]
                if found
                else ["Continue with native project tools; do not loop on discovery."]
            ),
            "artifacts": [],
        }

    async def call(
        self,
        *,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not self.settings.mcp_capabilities_enabled:
            return _error_observation(
                "MCP capabilities are disabled by the operator",
                stop="Do not retry in this generation.",
            )
        try:
            spec = self._server(server)
            if not spec.read_only:
                raise McpBrokerError(
                    "mutating MCP tools require an explicit confirmation flow and are disabled"
                )
            catalog = {cap.tool: cap for cap in await self._catalog(spec)}
            if tool not in catalog:
                raise McpBrokerError(f"tool {server}.{tool} is not in the approved catalog")
            encoded = json.dumps(arguments, ensure_ascii=False, default=str)
            if len(encoded) > 20_000:
                raise McpBrokerError("MCP tool arguments exceed the 20,000 character limit")
            async with asyncio.timeout(spec.timeout_seconds):
                async with _connected_client(spec) as client:
                    result = await client.call_tool(tool, dict(arguments))
            text, artifacts, structured = _compact_result(
                result,
                max_chars=self.settings.mcp_max_result_chars,
            )
            is_error = bool(getattr(result, "is_error", False))
            if is_error:
                return _error_observation(
                    f"MCP tool {server}.{tool} returned an error: {text}",
                    retry="Fix the arguments once using the discovered input schema.",
                    stop="After a repeated identical error, continue without this capability.",
                )
            return {
                "ok": True,
                "status": "success",
                "summary": f"MCP tool {server}.{tool} completed.",
                "content": text,
                "structured_content": structured,
                "next_actions": [
                    "Use the returned evidence in the product; do not call the same tool again "
                    "unless the question materially changes."
                ],
                "artifacts": artifacts,
            }
        except McpBrokerError as exc:
            return _error_observation(
                str(exc),
                retry="Run discover_capabilities once if the catalog may have changed.",
                stop="Do not bypass the allow-list or invent another server URL.",
            )
        except Exception as exc:
            log.warning(
                "mcp.call_failed",
                server=server,
                tool=tool,
                error=type(exc).__name__,
            )
            return _error_observation(
                f"MCP server {server} is temporarily unavailable ({type(exc).__name__})",
                retry="Retry once only if this evidence is essential.",
                stop="Then continue with native tools so the build does not stall.",
            )


def _error_observation(
    summary: str,
    *,
    retry: str = "",
    stop: str,
) -> dict[str, Any]:
    next_actions = [item for item in (retry, stop) if item]
    return {
        "ok": False,
        "status": "error",
        "summary": summary[:1000],
        "error": summary[:1000],
        "content": summary[:1000],
        "next_actions": next_actions,
        "artifacts": [],
    }


def reset_catalog_cache() -> None:
    """Test/admin hook; result payloads are never cached."""

    _catalog_cache.clear()
    _catalog_locks.clear()

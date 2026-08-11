from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from omnia_api.core.config import Settings
from omnia_api.services import mcp_broker
from omnia_api.services.mcp_broker import McpBroker, McpServerSpec

pytestmark = pytest.mark.asyncio


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://test:test@localhost/test",
        "jwt_secret": "test-secret",
        "env": "test",
        "mcp_capabilities_enabled": True,
        "mcp_context7_enabled": False,
        "mcp_catalog_ttl_seconds": 900,
        "mcp_max_result_chars": 12000,
    }
    values.update(overrides)
    return Settings(**values)


class _FakeClient:
    def __init__(self) -> None:
        self.list_calls = 0
        self.call_calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> Any:
        self.list_calls += 1
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="query-docs",
                    title="Query docs",
                    description="Current library documentation",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                ),
                SimpleNamespace(
                    name="dangerous-delete",
                    title="Delete",
                    description="Must never be exposed",
                    input_schema={"type": "object", "properties": {}},
                ),
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.call_calls.append((name, arguments))
        return SimpleNamespace(
            content=[
                SimpleNamespace(model_dump=lambda **_kwargs: {"type": "text", "text": "Fresh docs"})
            ],
            structured_content={"version": "current"},
            is_error=False,
        )


def _server(*, read_only: bool = True) -> McpServerSpec:
    return McpServerSpec(
        key="docs",
        title="Docs",
        url="https://mcp.example.com/mcp",
        allowed_tools=frozenset({"query-docs"}),
        read_only=read_only,
    )


async def test_discovery_filters_server_tools_and_caches_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()

    @asynccontextmanager
    async def _connect(_spec: McpServerSpec) -> Any:
        yield fake

    monkeypatch.setattr(mcp_broker, "_connected_client", _connect)
    mcp_broker.reset_catalog_cache()
    broker = McpBroker(settings=_settings(), servers={"docs": _server()})

    first = await broker.discover()
    second = await broker.discover()

    assert first["ok"] is True
    assert "query-docs" in first["content"]
    assert "dangerous-delete" not in first["content"]
    assert second["ok"] is True
    assert fake.list_calls == 1


async def test_approved_tool_call_returns_structured_harness_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()

    @asynccontextmanager
    async def _connect(_spec: McpServerSpec) -> Any:
        yield fake

    monkeypatch.setattr(mcp_broker, "_connected_client", _connect)
    mcp_broker.reset_catalog_cache()
    broker = McpBroker(settings=_settings(), servers={"docs": _server()})

    result = await broker.call(
        server="docs",
        tool="query-docs",
        arguments={"query": "Next.js route handlers"},
    )

    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["summary"] == "MCP tool docs.query-docs completed."
    assert result["content"] == 'Fresh docs\n{"version": "current"}'
    assert result["structured_content"] == {"version": "current"}
    assert result["next_actions"]
    assert fake.call_calls == [("query-docs", {"query": "Next.js route handlers"})]


async def test_unapproved_or_mutating_capability_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()

    @asynccontextmanager
    async def _connect(_spec: McpServerSpec) -> Any:
        yield fake

    monkeypatch.setattr(mcp_broker, "_connected_client", _connect)
    mcp_broker.reset_catalog_cache()
    broker = McpBroker(settings=_settings(), servers={"docs": _server(read_only=False)})

    result = await broker.call(server="docs", tool="query-docs", arguments={})

    assert result["ok"] is False
    assert result["status"] == "error"
    assert "confirmation flow" in result["summary"]
    assert result["next_actions"]
    assert fake.list_calls == 0


async def test_operator_registry_rejects_insecure_production_url() -> None:
    settings = _settings(
        env="production",
        mcp_servers_json=(
            '[{"key":"local","url":"http://127.0.0.1:9999/mcp","allowed_tools":["search"]}]'
        ),
    )

    with pytest.raises(mcp_broker.McpBrokerError, match="HTTPS"):
        mcp_broker.configured_servers(settings)


@pytest.mark.parametrize("read_only_fragment", ["", ',"read_only":false'])
async def test_custom_registry_requires_explicit_read_only_approval(
    read_only_fragment: str,
) -> None:
    settings = _settings(
        mcp_servers_json=(
            '[{"key":"docs","url":"https://mcp.example.com/mcp",'
            f'"allowed_tools":["search"]{read_only_fragment}}}]'
        ),
    )

    with pytest.raises(mcp_broker.McpBrokerError, match="explicitly set read_only=true"):
        mcp_broker.configured_servers(settings)


async def test_custom_registry_accepts_explicit_read_only_allow_list() -> None:
    settings = _settings(
        mcp_servers_json=(
            '[{"key":"docs","url":"https://mcp.example.com/mcp",'
            '"allowed_tools":["search"],"read_only":true}]'
        ),
    )

    server = mcp_broker.configured_servers(settings)["docs"]

    assert server.read_only is True
    assert server.allowed_tools == {"search"}


async def test_context7_is_a_real_default_remote_mcp_server() -> None:
    servers = mcp_broker.configured_servers(_settings(mcp_context7_enabled=True))

    assert servers["context7"].url == "https://mcp.context7.com/mcp"
    assert servers["context7"].allowed_tools == {
        "resolve-library-id",
        "query-docs",
    }

"""Contract of the Serverum client: what we send, how we read the answers, how
we wait and how we fail — all against a substituted httpx transport."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from yleum_orchestrator import serverum_cli
from yleum_orchestrator.services.serverum import (
    DRY_RUN_PLANS,
    OrderRequest,
    ServerumAuthError,
    ServerumClient,
    ServerumEndpoints,
    ServerumError,
    ServerumNotFound,
    ServerumSettings,
    ServerumTimeout,
    ServerumUnavailable,
    client_from_settings,
    dry_run_client,
    parse_plan,
    parse_server,
)

BASE = "https://panel.example.test/api/v1"


async def _no_sleep(_seconds: float) -> None:
    return None


def _client(handler: Any, **kwargs: Any) -> ServerumClient:
    return ServerumClient(
        base_url=BASE,
        token="secret-token",
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
        **kwargs,
    )


# ----------------------------------------------------------------- requests


async def test_plans_request_carries_bearer_token_and_parses_wrapped_list() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "vps-8-16",
                        "name": "8/16",
                        "cpu": 8,
                        "ram_gb": 16,
                        "disk": "160",
                        "price": {"month": "2189.00"},
                    },
                    {
                        "id": "vps-4-8",
                        "title": "4/8",
                        "vcpu": "4",
                        "ram_mb": 8192,
                        "disk_gb": 80,
                        "monthly_price": 1134,
                    },
                ]
            },
        )

    async with _client(handler) as client:
        plans = await client.list_plans()

    assert seen[0].method == "GET"
    assert str(seen[0].url) == f"{BASE}/plans"
    assert seen[0].headers["Authorization"] == "Bearer secret-token"
    assert seen[0].headers["Accept"] == "application/json"
    assert [plan.as_dict() for plan in plans] == [
        {
            "id": "vps-8-16",
            "name": "8/16",
            "vcpu": 8,
            "ram_mb": 16384,
            "disk_gb": 160,
            "price_month_rub": "2189.00",
        },
        {
            "id": "vps-4-8",
            "name": "4/8",
            "vcpu": 4,
            "ram_mb": 8192,
            "disk_gb": 80,
            "price_month_rub": "1134",
        },
    ]


async def test_order_sends_the_documented_body_and_reads_the_server_back() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={"server": {"id": 4711, "state": "provisioning", "hostname": "cells3"}},
        )

    request = OrderRequest(
        plan_id="vps-8-16",
        hostname="cells3",
        os="ubuntu-24.04",
        location="msk",
        ssh_public_keys=("ssh-ed25519 AAAA admin",),
        note="MAX Studio cells host",
    )
    async with _client(handler) as client:
        server = await client.order_server(request)

    assert seen[0].method == "POST"
    assert str(seen[0].url) == f"{BASE}/servers"
    assert json.loads(seen[0].content) == {
        "plan_id": "vps-8-16",
        "hostname": "cells3",
        "os": "ubuntu-24.04",
        "location": "msk",
        "ssh_keys": ["ssh-ed25519 AAAA admin"],
        "note": "MAX Studio cells host",
    }
    assert server.id == "4711"
    assert server.status == "provisioning"
    assert server.ready is False


def test_order_request_validates_hostname_and_plan() -> None:
    with pytest.raises(ServerumError, match="hostname"):
        OrderRequest(plan_id="p", hostname="Cells_3", os="u", location="msk")
    with pytest.raises(ServerumError, match="plan id"):
        OrderRequest(plan_id="", hostname="cells3", os="u", location="msk")


async def test_wait_for_ip_polls_until_active_then_returns_addresses() -> None:
    states = iter(
        [
            {"id": "s1", "status": "provisioning"},
            {"id": "s1", "status": "installing", "ip": None},
            {
                "id": "s1",
                "status": "active",
                "ip": {"address": "2.153.248.101"},
                "private_ip": "172.197.102.5",
                "plan": "vps-8-16",
            },
        ]
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "GET"
        assert str(request.url) == f"{BASE}/servers/s1"
        return httpx.Response(200, json=next(states))

    async with _client(handler) as client:
        server = await client.wait_for_ip("s1", timeout_seconds=60, poll_seconds=5)

    assert calls == 3
    assert server.ready is True
    assert server.public_ip == "2.153.248.101"
    assert server.private_ip == "172.197.102.5"
    assert server.plan_id == "vps-8-16"


async def test_wait_for_ip_times_out_and_fails_fast_on_terminal_status() -> None:
    def forever_provisioning(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "s2", "status": "provisioning"})

    async with _client(forever_provisioning) as client:
        with pytest.raises(ServerumTimeout, match="still 'provisioning' after 20 s"):
            await client.wait_for_ip("s2", timeout_seconds=20, poll_seconds=10)

    def failed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "s3", "status": "error"})

    async with _client(failed) as client:
        with pytest.raises(ServerumError, match="ended in status 'error'"):
            await client.wait_for_ip("s3", timeout_seconds=20, poll_seconds=10)


async def test_delete_and_error_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE" and request.url.path.endswith("/servers/s1"):
            return httpx.Response(204)
        if request.url.path.endswith("/servers/missing"):
            return httpx.Response(404, json={"error": "no"})
        if request.url.path.endswith("/servers/broken"):
            return httpx.Response(503, text="maintenance")
        if request.url.path.endswith("/servers/bad"):
            return httpx.Response(422, json={"error": "invalid plan"})
        return httpx.Response(401, json={"error": "unauthorized"})

    async with _client(handler) as client:
        await client.delete_server("s1")
        with pytest.raises(ServerumNotFound):
            await client.get_server("missing")
        with pytest.raises(ServerumUnavailable):
            await client.get_server("broken")
        with pytest.raises(ServerumError, match="422"):
            await client.get_server("bad")
        with pytest.raises(ServerumAuthError):
            await client.list_plans()


async def test_network_failure_is_unavailable_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _client(handler) as client:
        with pytest.raises(ServerumUnavailable):
            await client.list_servers()


def test_missing_token_is_rejected_before_any_request() -> None:
    with pytest.raises(ServerumAuthError, match="SERVERUM_API_TOKEN"):
        ServerumClient(base_url=BASE, token="")


# ------------------------------------------------------------ endpoint map


async def test_endpoint_map_can_be_overridden_for_the_real_panel() -> None:
    endpoints = ServerumEndpoints.from_json('{"plans": "/tariffs", "server": "/vps/{id}"}')
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(
            200,
            json={"tariffs": [], "vps": {"id": "v1", "status": "active", "ipv4": "203.0.113.4"}},
        )

    async with _client(handler, endpoints=endpoints) as client:
        assert await client.list_plans() == []
        server = await client.get_server("v1")

    assert seen == ["GET /api/v1/tariffs", "GET /api/v1/vps/v1"]
    assert server.public_ip == "203.0.113.4"
    with pytest.raises(ServerumError, match="unknown endpoint"):
        ServerumEndpoints.from_json('{"nope": "/x"}')
    with pytest.raises(ServerumError, match="valid JSON"):
        ServerumEndpoints.from_json("{")


def test_parsers_tolerate_alternative_field_names() -> None:
    plan = parse_plan({"code": "p1", "cores": 2, "memory": 4, "storage_gb": 50, "price": "636"})
    assert (plan.id, plan.vcpu, plan.ram_mb, plan.disk_gb) == ("p1", 2, 4096, 50)
    assert plan.price_month_rub == Decimal("636")
    server = parse_server({"uuid": "u1", "power_status": "on", "main_ip": ["198.51.100.2"]})
    assert server.id == "u1"
    assert server.ready is True
    assert server.public_ip == "198.51.100.2"


# ----------------------------------------------------------------- dry run


async def test_dry_run_orders_waits_and_deletes_without_network() -> None:
    client = dry_run_client()
    async with client:
        plans = await client.list_plans()
        assert [plan.id for plan in plans] == [str(item["id"]) for item in DRY_RUN_PLANS]
        ordered = await client.order_server(
            OrderRequest(plan_id=plans[-1].id, hostname="cells3", os="ubuntu-24.04", location="msk")
        )
        assert ordered.status == "provisioning"
        ready = await client.wait_for_ip(ordered.id, timeout_seconds=30, poll_seconds=1)
        assert ready.ready is True
        assert ready.public_ip == "203.0.113.11"
        assert ready.private_ip == "172.197.102.11"
        assert ready.hostname == "cells3"
        assert [server.id for server in await client.list_servers()] == ["dry-1"]
        await client.delete_server("dry-1")
        assert await client.list_servers() == []
        with pytest.raises(ServerumNotFound):
            await client.get_server("dry-1")


def test_settings_build_a_real_or_dry_client(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = ServerumSettings(api_base_url=BASE, api_token="", endpoints_json="")
    with pytest.raises(ServerumAuthError):
        client_from_settings(settings)
    assert client_from_settings(settings, dry_run=True).endpoints == ServerumEndpoints()
    configured = ServerumSettings(
        api_base_url=BASE, api_token="tok", endpoints_json='{"plans": "/tariffs"}'
    )
    assert client_from_settings(configured).endpoints.plans == "/tariffs"


# --------------------------------------------------------------------- CLI


def test_cli_dry_run_plans_and_order_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert serverum_cli.main(["--dry-run", "--json", "plans"]) == 0
    plans = json.loads(capsys.readouterr().out)
    assert plans[0]["id"] == "vps-4-8-80"

    assert (
        serverum_cli.main(
            [
                "--dry-run",
                "--json",
                "order",
                "--plan",
                "vps-8-16-160",
                "--hostname",
                "cells3",
                "--wait",
                "--poll",
                "0",
            ]
        )
        == 0
    )
    server = json.loads(capsys.readouterr().out)
    assert server["status"] == "active"
    assert server["public_ip"] == "203.0.113.11"

    assert serverum_cli.main(["--dry-run", "delete", "dry-1"]) == 2  # no --yes
    # A fresh dry-run client owns no servers, so the delete is a provider "not found".
    assert serverum_cli.main(["--dry-run", "--json", "delete", "dry-1", "--yes"]) == 3


def test_cli_without_token_explains_itself(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SERVERUM_API_TOKEN", raising=False)
    monkeypatch.setattr(serverum_cli, "ServerumSettings", lambda: ServerumSettings(api_token=""))
    assert serverum_cli.main(["plans"]) == 2
    assert "SERVERUM_API_TOKEN" in capsys.readouterr().err

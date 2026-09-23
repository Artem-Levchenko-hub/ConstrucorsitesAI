"""Serverum (serverum.ru) VPS ordering client — the automation behind
``infra/max-k3s/cells/60-order-cell-host.sh`` («нужен ещё хост ячеек»).

Provider facts, checked 2026-09-23: the customer panel is https://my.serverum.ru
(a custom SPA — ``/billmgr`` answers with the SPA shell, so it is not ISPsystem
BILLmanager), the public price list is per-month/per-hour in RUB (1 vCPU / 1 GB /
20 GB NVMe ≈ 282 ₽ … 8 vCPU / 16 GB / 160 GB ≈ 2 189 ₽ per month, Moscow), and
**no public API documentation** exists on the site, the blog or the panel.

The endpoints below therefore follow the usual shape of a hosting REST API
(bearer token, ``/plans``, ``/servers``, ``/servers/{id}``) and are marked
«эндпоинты уточнить»: the owner requests the API token and the endpoint list
from Serverum support (hd@serverum.ru / sales@serverum.ru). Adapting to the real
API means changing :class:`ServerumEndpoints` (or ``SERVERUM_ENDPOINTS_JSON``)
and, at most, the tolerant field pickers — the flow (list plans → order → wait
for the IP → delete) and every caller stay the same. Until then ``--dry-run``
(:class:`DryRunTransport`) exercises the whole path without a network.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

READY_STATUSES: frozenset[str] = frozenset({"active", "running", "ready", "online", "on"})
FAILED_STATUSES: frozenset[str] = frozenset(
    {"error", "failed", "cancelled", "canceled", "deleted", "suspended"}
)
_HOSTNAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


class ServerumError(RuntimeError):
    """The provider answered with something we cannot act on."""


class ServerumAuthError(ServerumError):
    """401/403: the token is missing, wrong or lacks the right."""


class ServerumNotFound(ServerumError):
    """404: no such server / plan."""


class ServerumUnavailable(ServerumError):
    """Network failure or 5xx: retry later."""


class ServerumTimeout(ServerumError):
    """The server did not become ready within the wait budget."""


class ServerumSettings(BaseSettings):
    """Standalone settings: the CLI runs from a laptop without the orchestrator's
    ``.env`` (DATABASE_URL, INTERNAL_TOKEN…), so it must not load `core.config`."""

    model_config = SettingsConfigDict(
        env_prefix="SERVERUM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # «уточнить»: base of the panel API (see the module docstring).
    api_base_url: str = "https://my.serverum.ru/api/v1"
    api_token: SecretStr = SecretStr("")
    timeout_seconds: float = 30.0
    # Serverum sells Moscow only (Tier III); the value is passed through as-is.
    default_location: str = "msk"
    default_os: str = "ubuntu-24.04"
    # JSON object overriding ServerumEndpoints fields, e.g. {"plans": "/tariffs"}.
    endpoints_json: str = ""


@dataclass(frozen=True, slots=True)
class ServerumEndpoints:
    """Paths relative to the API base. ``{id}`` is the server id."""

    plans: str = "/plans"
    servers: str = "/servers"
    server: str = "/servers/{id}"

    @classmethod
    def from_json(cls, raw: str) -> ServerumEndpoints:
        text = raw.strip()
        if not text:
            return cls()
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ServerumError("SERVERUM_ENDPOINTS_JSON is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ServerumError("SERVERUM_ENDPOINTS_JSON must be a JSON object")
        known = {name for name in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ServerumError(f"unknown endpoint names: {sorted(unknown)}")
        values = {name: str(value) for name, value in data.items()}
        return cls(**values)


@dataclass(frozen=True, slots=True)
class ServerumPlan:
    id: str
    name: str
    vcpu: int | None
    ram_mb: int | None
    disk_gb: int | None
    price_month_rub: Decimal | None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "vcpu": self.vcpu,
            "ram_mb": self.ram_mb,
            "disk_gb": self.disk_gb,
            "price_month_rub": None if self.price_month_rub is None else str(self.price_month_rub),
        }


@dataclass(frozen=True, slots=True)
class ServerumServer:
    id: str
    status: str
    hostname: str | None
    public_ip: str | None
    private_ip: str | None
    plan_id: str | None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def ready(self) -> bool:
        return self.status.lower() in READY_STATUSES and bool(self.public_ip)

    @property
    def failed(self) -> bool:
        return self.status.lower() in FAILED_STATUSES

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "status": self.status,
            "hostname": self.hostname,
            "public_ip": self.public_ip,
            "private_ip": self.private_ip,
            "plan_id": self.plan_id,
        }


@dataclass(frozen=True, slots=True)
class OrderRequest:
    plan_id: str
    hostname: str
    os: str
    location: str
    ssh_public_keys: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if _HOSTNAME.fullmatch(self.hostname) is None:
            raise ServerumError(
                f"hostname {self.hostname!r} must be lowercase letters, digits and '-' (max 32)"
            )
        if not self.plan_id:
            raise ServerumError("plan id is required")

    def body(self) -> dict[str, object]:
        """The order payload («уточнить» field names with support)."""
        payload: dict[str, object] = {
            "plan_id": self.plan_id,
            "hostname": self.hostname,
            "os": self.os,
            "location": self.location,
        }
        if self.ssh_public_keys:
            payload["ssh_keys"] = list(self.ssh_public_keys)
        if self.note:
            payload["note"] = self.note
        return payload


# ----------------------------------------------------------- tolerant parsing


def _pick(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        value = _pick(value, "value", "amount", "month", "monthly")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        value = _pick(value, "address", "ip", "ipv4", "value", "id")
    if isinstance(value, list | tuple):
        value = value[0] if value else None
    return None if value is None else str(value)


def _items(body: Any, *containers: str) -> list[Mapping[str, Any]]:
    """A list of records from ``[…]``, ``{"data": […]}``, ``{"items": […]}`` and friends."""
    candidate: Any = body
    if isinstance(body, Mapping):
        candidate = _pick(body, *containers, "data", "items", "results", "list")
        if isinstance(candidate, Mapping):
            candidate = list(candidate.values())
    if not isinstance(candidate, list):
        raise ServerumError("Serverum returned no list where one was expected")
    return [item for item in candidate if isinstance(item, Mapping)]


def _record(body: Any, *containers: str) -> Mapping[str, Any]:
    if isinstance(body, Mapping):
        inner = _pick(body, *containers, "data", "result")
        if isinstance(inner, Mapping):
            return inner
        return body
    raise ServerumError("Serverum returned no object where one was expected")


def parse_plan(item: Mapping[str, Any]) -> ServerumPlan:
    ram_mb = _as_int(_pick(item, "ram_mb", "memory_mb"))
    if ram_mb is None:
        ram_gb = _as_int(_pick(item, "ram_gb", "memory_gb", "ram", "memory"))
        ram_mb = None if ram_gb is None else ram_gb * 1024
    return ServerumPlan(
        id=str(_pick(item, "id", "plan_id", "code", "slug") or ""),
        name=str(_pick(item, "name", "title", "id") or ""),
        vcpu=_as_int(_pick(item, "vcpu", "cpu", "cores", "cpu_cores")),
        ram_mb=ram_mb,
        disk_gb=_as_int(_pick(item, "disk_gb", "disk", "storage_gb", "nvme_gb")),
        price_month_rub=_as_decimal(
            _pick(item, "price_month_rub", "price_month", "monthly_price", "price")
        ),
        raw=item,
    )


def parse_server(item: Mapping[str, Any]) -> ServerumServer:
    status = _pick(item, "status", "state", "power_status")
    return ServerumServer(
        id=str(_pick(item, "id", "server_id", "uuid") or ""),
        status=str(status or "unknown"),
        hostname=_as_str(_pick(item, "hostname", "name")),
        public_ip=_as_str(_pick(item, "public_ip", "ip", "ipv4", "ip_address", "main_ip")),
        private_ip=_as_str(_pick(item, "private_ip", "lan_ip", "internal_ip")),
        plan_id=_as_str(_pick(item, "plan_id", "plan", "tariff_id", "tariff")),
        raw=item,
    )


# --------------------------------------------------------------------- client


class ServerumClient:
    """Async client over the provider REST API (or the dry-run transport)."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        endpoints: ServerumEndpoints | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not token:
            raise ServerumAuthError("SERVERUM_API_TOKEN is not set")
        self.endpoints = endpoints or ServerumEndpoints()
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "max-studio-orchestrator/serverum",
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> ServerumClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, *, json: dict[str, object] | None = None
    ) -> Any:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise ServerumUnavailable(f"Serverum request failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise ServerumAuthError(f"Serverum rejected the token ({response.status_code})")
        if response.status_code == 404:
            raise ServerumNotFound(f"Serverum: {method} {path} → 404")
        if response.status_code >= 500:
            raise ServerumUnavailable(f"Serverum is unavailable ({response.status_code})")
        if response.status_code >= 400:
            raise ServerumError(
                f"Serverum refused {method} {path}: {response.status_code} {response.text[:300]}"
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ServerumError("Serverum returned a non-JSON body") from exc

    async def list_plans(self) -> list[ServerumPlan]:
        body = await self._request("GET", self.endpoints.plans)
        return [parse_plan(item) for item in _items(body, "plans", "tariffs")]

    async def list_servers(self) -> list[ServerumServer]:
        body = await self._request("GET", self.endpoints.servers)
        return [parse_server(item) for item in _items(body, "servers", "vps")]

    async def order_server(self, request: OrderRequest) -> ServerumServer:
        body = await self._request("POST", self.endpoints.servers, json=request.body())
        server = parse_server(_record(body, "server", "vps"))
        if not server.id:
            raise ServerumError("Serverum accepted the order but returned no server id")
        return server

    async def get_server(self, server_id: str) -> ServerumServer:
        body = await self._request("GET", self.endpoints.server.format(id=server_id))
        return parse_server(_record(body, "server", "vps"))

    async def delete_server(self, server_id: str) -> None:
        await self._request("DELETE", self.endpoints.server.format(id=server_id))

    async def wait_for_ip(
        self,
        server_id: str,
        *,
        timeout_seconds: float = 600.0,
        poll_seconds: float = 10.0,
    ) -> ServerumServer:
        """Poll until the server is active and has a public address."""
        waited = 0.0
        while True:
            server = await self.get_server(server_id)
            if server.ready:
                return server
            if server.failed:
                raise ServerumError(f"server {server_id} ended in status {server.status!r}")
            if waited >= timeout_seconds:
                raise ServerumTimeout(
                    f"server {server_id} still {server.status!r} after {int(waited)} s"
                )
            await self._sleep(poll_seconds)
            waited += poll_seconds


# -------------------------------------------------------------------- dry run


# The three configurations we would actually order for a cell host (the public
# price list of serverum.ru, Moscow, 2026-09-23).
DRY_RUN_PLANS: tuple[dict[str, object], ...] = (
    {
        "id": "vps-4-8-80",
        "name": "4 vCPU · 8 GB · 80 GB NVMe",
        "vcpu": 4,
        "ram_mb": 8192,
        "disk_gb": 80,
        "price_month_rub": "1134.00",
    },
    {
        "id": "vps-8-12-100",
        "name": "8 vCPU · 12 GB · 100 GB NVMe",
        "vcpu": 8,
        "ram_mb": 12288,
        "disk_gb": 100,
        "price_month_rub": "1768.00",
    },
    {
        "id": "vps-8-16-160",
        "name": "8 vCPU · 16 GB · 160 GB NVMe",
        "vcpu": 8,
        "ram_mb": 16384,
        "disk_gb": 160,
        "price_month_rub": "2189.00",
    },
)


class DryRunTransport(httpx.AsyncBaseTransport):
    """No network: answers like the panel would and records every call.

    A freshly ordered server is ``provisioning`` on its first read and ``active``
    with a documentation-range address (203.0.113.x) from the second read on,
    so ``wait_for_ip`` really polls.
    """

    def __init__(self, *, endpoints: ServerumEndpoints | None = None) -> None:
        self.endpoints = endpoints or ServerumEndpoints()
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self._servers: dict[str, dict[str, object]] = {}
        self._reads: dict[str, int] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        method = request.method.upper()
        path = request.url.path
        body: dict[str, object] | None = None
        if request.content:
            parsed = json.loads(request.content.decode())
            body = parsed if isinstance(parsed, dict) else None
        self.calls.append((method, path, body))

        def json_response(status: int, payload: object | None = None) -> httpx.Response:
            return httpx.Response(status, json=payload, request=request)

        server_prefix = self.endpoints.server.split("{id}")[0]
        if method == "GET" and path.endswith(self.endpoints.plans):
            return json_response(200, {"data": list(DRY_RUN_PLANS)})
        if method == "GET" and path.endswith(self.endpoints.servers):
            return json_response(200, {"data": list(self._servers.values())})
        if method == "POST" and path.endswith(self.endpoints.servers):
            index = len(self._servers) + 1
            server_id = f"dry-{index}"
            record: dict[str, object] = {
                "id": server_id,
                "status": "provisioning",
                "hostname": (body or {}).get("hostname"),
                "plan_id": (body or {}).get("plan_id"),
                "public_ip": None,
                "private_ip": None,
            }
            self._servers[server_id] = record
            return json_response(201, {"data": record})
        if server_prefix and server_prefix in path:
            server_id = path.rsplit("/", 1)[-1]
            record = self._servers.get(server_id) or {}
            if not record:
                return json_response(404, {"error": "not found"})
            if method == "DELETE":
                del self._servers[server_id]
                return httpx.Response(204, request=request)
            if method == "GET":
                reads = self._reads.get(server_id, 0) + 1
                self._reads[server_id] = reads
                if reads >= 2 and record["status"] == "provisioning":
                    index = int(server_id.rsplit("-", 1)[-1])
                    record["status"] = "active"
                    record["public_ip"] = f"203.0.113.{10 + index}"
                    record["private_ip"] = f"172.197.102.{10 + index}"
                return json_response(200, {"data": record})
        return json_response(404, {"error": f"dry-run: no route for {method} {path}"})


async def _no_sleep(_seconds: float) -> None:
    return None


def dry_run_client(*, endpoints: ServerumEndpoints | None = None) -> ServerumClient:
    return ServerumClient(
        base_url="https://dry-run.serverum.invalid/api/v1",
        token="dry-run",
        endpoints=endpoints,
        transport=DryRunTransport(endpoints=endpoints),
        sleep=_no_sleep,
    )


def client_from_settings(
    settings: ServerumSettings | None = None,
    *,
    dry_run: bool = False,
) -> ServerumClient:
    current = settings or ServerumSettings()
    endpoints = ServerumEndpoints.from_json(current.endpoints_json)
    if dry_run:
        return dry_run_client(endpoints=endpoints)
    return ServerumClient(
        base_url=current.api_base_url,
        token=current.api_token.get_secret_value(),
        endpoints=endpoints,
        timeout_seconds=current.timeout_seconds,
    )


def read_ssh_public_keys(paths: Sequence[str]) -> tuple[str, ...]:
    keys: list[str] = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    keys.append(line)
    return tuple(keys)


__all__ = [
    "DRY_RUN_PLANS",
    "DryRunTransport",
    "OrderRequest",
    "ServerumAuthError",
    "ServerumClient",
    "ServerumEndpoints",
    "ServerumError",
    "ServerumNotFound",
    "ServerumPlan",
    "ServerumServer",
    "ServerumSettings",
    "ServerumTimeout",
    "ServerumUnavailable",
    "client_from_settings",
    "dry_run_client",
    "parse_plan",
    "parse_server",
    "read_ssh_public_keys",
]
